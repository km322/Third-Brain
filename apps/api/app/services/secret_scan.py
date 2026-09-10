"""Heuristic secret detection for content entering the knowledge base.

Scans raw document text BEFORE chunking/embedding so that leaked credentials can be
quarantined instead of indexed. Detection is a curated table of high-confidence regex
patterns (cloud keys, VCS tokens, private-key blocks, this product's own ``tb_...`` API
keys, ...) plus one generic ``keyword = value`` detector guarded by entropy and
placeholder checks so ``.env.example``-style documentation does not trip it.

Guarantees:

- Raw secret material never leaves this module. Samples are redacted to at most the
  first four and last two characters (``AKIA…LE``); private-key and service-account
  matches use a fixed literal instead of any matched text.
- Every pattern is linear-safe (single bounded character classes, no nested
  quantifiers), so scanning cost stays proportional to the input size.
- At most three redacted samples are kept per detector; each detector counts at most
  1000 valid matches and examines at most a bounded number of raw candidates, so a
  document engineered to spray candidate matches cannot force superlinear work.
  ``ScanReport.truncated`` flags any detector that hit either cap.

This is a tripwire for accidental credential ingestion, not a full DLP system.
Reviewers approve or discard quarantined documents from the redacted findings alone;
approval is stamped into ``Document.meta`` keyed to the document checksum via
:func:`mark_approved` / :func:`is_approved`.

Laid out, in order: redaction / scoring helpers, validators (run per regex match, before
the match is counted), the detector table, then the public API.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

SCAN_META_KEY = "secret_scan"

_MAX_SAMPLES = 3
_MAX_COUNTED_MATCHES = 1000

_MAX_EXAMINED_MATCHES = 5000
"""Hard bound on how many raw regex candidates a single detector inspects, counted BEFORE
the validator runs. Without it, a document full of validator-failing candidates (e.g.
millions of ``sk-...``-shaped slugs, or ``"type": "service_account"`` blocks with no
key) would make ``finditer`` walk the entire input while nothing ever counts, turning a
cheap scan into an attacker-controlled sink. Set above ``_MAX_COUNTED_MATCHES`` so the
counted cap is still reachable on all-valid input."""

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

_PRIVATE_KEY_REDACTION = "[private key material]"
_SERVICE_ACCOUNT_REDACTION = "[service account credentials]"


@dataclass(frozen=True)
class SecretSample:
    """One redacted occurrence of a detected secret (never the raw value)."""

    redacted: str
    line: int


@dataclass(frozen=True)
class SecretFinding:
    """Aggregated matches for one detector."""

    detector: str
    label: str
    severity: str
    occurrences: int
    samples: list[SecretSample]


@dataclass(frozen=True)
class ScanReport:
    """Result of scanning one document's text."""

    findings: list[SecretFinding]
    truncated: bool

    @property
    def flagged(self) -> bool:
        """True iff at least one detector fired."""
        return bool(self.findings)


def _redact(value: str) -> str:
    """Redact a matched secret: first four + ellipsis + last two, or fully masked."""
    if len(value) > 8:
        return value[:4] + "…" + value[-2:]
    return "····"


def _shannon_entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character."""
    if not value:
        return 0.0
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in Counter(value).values())


_DIGIT_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[A-Za-z]")
_NON_LETTER_RE = re.compile(r"[^A-Za-z]")
_SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")
_MASK_RE = re.compile(r"[xX*#•·._-]{4,}")
_ENV_CONST_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_HYPHENATED_PROSE_RE = re.compile(r"[a-z]+(?:-[a-z]+)+")

_PLACEHOLDER_EXACT = {
    "password",
    "passwd",
    "passw0rd",
    "pass",
    "secret",
    "test",
    "testing",
    "letmein",
    "hunter2",
    "admin",
    "root",
    "welcome1",
    "password1",
    "password123",
    "secret123",
    "qwerty123",
}
_PLACEHOLDER_SUBSTRINGS = (
    "changeme",
    "change-me",
    "change_me",
    "example",
    "sample",
    "dummy",
    "redacted",
    "placeholder",
    "your-",
    "your_",
    "fixme",
    "not-a-real",
    "notareal",
    "insert-",
    "insert_",
    "replace-me",
    "replace_me",
)
_CODE_REF_PREFIXES = (
    "process.env",
    "os.environ",
    "os.getenv",
    "getenv(",
    "env(",
    "environ[",
    "config.",
    "config[",
    "settings.",
    "secrets.",
)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _looks_placeholder(value: str) -> bool:
    """True when ``value`` is documentation filler rather than a plausible secret."""
    lower = value.lower()
    if lower in _PLACEHOLDER_EXACT:
        return True
    if any(marker in lower for marker in _PLACEHOLDER_SUBSTRINGS):
        return True
    if _MASK_RE.fullmatch(value):
        return True
    if value.startswith("<") and value.endswith(">"):
        return True
    if value.startswith(("$", "{{", "%(")):
        return True
    if lower.startswith(_CODE_REF_PREFIXES):
        return True
    return bool(_ENV_CONST_RE.fullmatch(value))


def _has_variety(match: re.Match[str]) -> bool:
    """Reject mask-style bodies (``sk_live_xxxxxxxx…``); real keys are high-variety."""
    body = match.group(match.lastindex or 0)
    return len(set(body)) >= 6


def _slack_token_ok(match: re.Match[str]) -> bool:
    """Real Slack tokens embed numeric workspace/bot ids."""
    return bool(_DIGIT_RE.search(match.group(1)))


def _openai_key_ok(match: re.Match[str]) -> bool:
    """Reject prose-like ``sk-...`` slugs: require a digit and reasonable entropy."""
    value = match.group(0)
    return bool(_DIGIT_RE.search(value)) and _shannon_entropy(value) >= 3.0


def _url_password_ok(match: re.Match[str]) -> bool:
    """Reject placeholder passwords and ``host:port@``-shaped false positives."""
    username, password = match.group(1), match.group(2)
    if password.lower() == username.lower():
        return False
    if password.isdigit() and len(password) <= 5:
        return False
    return not _looks_placeholder(password)


def _looks_code_expression(value: str) -> bool:
    """True for call/member-access shapes and values ending in code punctuation."""
    if "(" in value:
        return True
    return value.endswith((")", ";", ","))


def _is_bare_url(value: str) -> bool:
    """True for a plain ``scheme://host/path`` URL with no ``user:pass@`` userinfo."""
    marker = value.find("://")
    if marker < 0:
        return False
    authority = value[marker + 3 :].split("/", 1)[0]
    return "@" not in authority


def _has_credential_variety(value: str) -> bool:
    """Real credentials carry a digit, or mix letter case together with a symbol."""
    if _DIGIT_RE.search(value):
        return True
    has_upper = any(char.isupper() for char in value)
    has_lower = any(char.islower() for char in value)
    return has_upper and has_lower and bool(_SYMBOL_RE.search(value))


def _keyword_value_ok(match: re.Match[str]) -> bool:
    """Accept only credential-shaped values, rejecting code, URLs, and prose.

    Beyond entropy, a real hardcoded credential must not look like a function call or
    member access, must not be a plain URL, must not be hyphen-joined lowercase prose,
    and must show variety (a digit, or mixed case together with a symbol).
    """
    key, value = match.group(1), match.group(2)
    if not (_LETTER_RE.search(value) and _NON_LETTER_RE.search(value)):
        return False
    if _looks_placeholder(value):
        return False
    if _looks_code_expression(value) or _is_bare_url(value):
        return False
    if _HYPHENATED_PROSE_RE.fullmatch(value):
        return False
    if not _has_credential_variety(value):
        return False
    if _normalize(value) == _normalize(key):
        return False
    return _shannon_entropy(value) >= 3.3


_GCP_PRIVATE_KEY_RE = re.compile(r'"private_key"|-----BEGIN [A-Z ]{0,32}PRIVATE KEY')
"""Presence of this pattern anywhere in the document is what promotes a
``"type": "service_account"`` block from "documentation of the JSON shape" to "leaked
credential". It is a property of the whole text, not of any individual match, so it is
evaluated ONCE per :func:`scan_text` call (see ``has_gcp_key``) rather than re-scanned
per match: doing the latter is quadratic on a document full of service-account markers."""


_JWT_EXAMPLE_PREFIXES = frozenset(
    {
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ",
    }
)


def _jwt_ok(match: re.Match[str]) -> bool:
    """Skip the canonical jwt.io sample under any signature; flag real-shaped tokens.

    The well-known example header and payload appear verbatim across API docs; keying
    the skip on the first two segments means any re-signing of them is ignored while a
    randomly generated token still flags.
    """
    parts = match.group(0).split(".")
    if ".".join(parts[:2]) in _JWT_EXAMPLE_PREFIXES:
        return False
    return _has_variety(match)


@dataclass(frozen=True)
class _Detector:
    """One compiled detection rule.

    ``group`` selects the subgroup treated as the secret for redaction and line
    numbers (0 = whole match). ``fixed_redaction`` overrides redaction entirely so
    key material is never sliced. ``requires_gcp_key`` gates the detector on the
    document-level presence of private-key material (see ``_GCP_PRIVATE_KEY_RE``),
    evaluated once per scan instead of per match.
    """

    id: str
    label: str
    severity: str
    pattern: re.Pattern[str]
    group: int = 0
    validator: Callable[[re.Match[str]], bool] | None = None
    fixed_redaction: str | None = None
    requires_gcp_key: bool = False


_DETECTORS: tuple[_Detector, ...] = (
    _Detector(
        id="aws-access-key-id",
        label="AWS access key ID",
        severity="high",
        pattern=re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)([0-9A-Z]{16})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="private-key",
        label="Private key (PEM block)",
        severity="high",
        pattern=re.compile(r"-----BEGIN [A-Z ]{0,32}PRIVATE KEY(?: BLOCK)?-----"),
        fixed_redaction=_PRIVATE_KEY_REDACTION,
    ),
    _Detector(
        id="gcp-service-account",
        label="Google Cloud service account JSON",
        severity="high",
        pattern=re.compile(r'"type"\s*:\s*"service_account"'),
        requires_gcp_key=True,
        fixed_redaction=_SERVICE_ACCOUNT_REDACTION,
    ),
    _Detector(
        id="github-token",
        label="GitHub token",
        severity="high",
        pattern=re.compile(r"\bgh[opsur]_([A-Za-z0-9]{36})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="github-fine-grained-pat",
        label="GitHub fine-grained personal access token",
        severity="high",
        pattern=re.compile(r"\bgithub_pat_([A-Za-z0-9_]{36,255})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="gitlab-pat",
        label="GitLab personal access token",
        severity="high",
        pattern=re.compile(r"\bglpat-([A-Za-z0-9_-]{20,64})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="slack-token",
        label="Slack token",
        severity="high",
        pattern=re.compile(r"\bxox[baprs]-([A-Za-z0-9-]{10,64})\b"),
        validator=_slack_token_ok,
    ),
    _Detector(
        id="stripe-live-key",
        label="Stripe live-mode secret key",
        severity="high",
        pattern=re.compile(r"\b(?:sk|rk)_live_([A-Za-z0-9]{16,64})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="google-api-key",
        label="Google API key",
        severity="high",
        pattern=re.compile(r"\bAIza([0-9A-Za-z_-]{35})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="anthropic-api-key",
        label="Anthropic API key",
        severity="high",
        pattern=re.compile(r"\bsk-ant-([A-Za-z0-9_-]{20,})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="openai-api-key",
        label="OpenAI-style API key",
        severity="high",
        pattern=re.compile(r"\bsk-(?!ant-)([A-Za-z0-9_-]{29,})\b"),
        validator=_openai_key_ok,
    ),
    _Detector(
        id="sendgrid-api-key",
        label="SendGrid API key",
        severity="high",
        pattern=re.compile(r"\bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{16,64}\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="azure-storage-account-key",
        label="Azure storage account key",
        severity="high",
        pattern=re.compile(r"AccountKey=([A-Za-z0-9+/]{80,100}={0,2})"),
        group=1,
        validator=_has_variety,
    ),
    _Detector(
        id="npm-token",
        label="npm access token",
        severity="high",
        pattern=re.compile(r"\bnpm_([A-Za-z0-9]{36})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="pypi-token",
        label="PyPI API token",
        severity="high",
        pattern=re.compile(r"\bpypi-AgEIcHlwaS5vcmc([A-Za-z0-9_-]{20,})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="third-brain-api-key",
        label="Third Brain API key",
        severity="high",
        pattern=re.compile(r"\btb_(?:live|test)_([A-Za-z0-9_-]{30,64})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="url-credentials",
        label="Credentials embedded in URL",
        severity="high",
        pattern=re.compile(
            r"\b[A-Za-z][A-Za-z0-9+.-]{1,30}://"
            r'([^\s/:@"\']{1,64}):([^\s/@"\']{3,128})@[A-Za-z0-9]'
        ),
        group=2,
        validator=_url_password_ok,
    ),
    _Detector(
        id="stripe-test-key",
        label="Stripe test-mode secret key",
        severity="medium",
        pattern=re.compile(r"\b(?:sk|rk)_test_([A-Za-z0-9]{16,64})\b"),
        validator=_has_variety,
    ),
    _Detector(
        id="jwt",
        label="JSON Web Token",
        severity="medium",
        pattern=re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        validator=_jwt_ok,
    ),
    _Detector(
        id="keyword-assignment",
        label="Possible hardcoded credential",
        severity="medium",
        pattern=re.compile(
            r"(api[ _-]?key|apikey|secret[ _-]?key|signing[ _-]?key"
            r"|encryption[ _-]?key|secret|token|password|passwd|auth[ _-]?token"
            r"|access[ _-]?key|client[ _-]?secret|private[ _-]?key)"
            r'["\']?[ \t]*[:=]=?[ \t]*["\']?([^\s"\'`]{8,128})',
            re.IGNORECASE,
        ),
        group=2,
        validator=_keyword_value_ok,
    ),
)


def scan_text(text: str) -> ScanReport:
    """Scan ``text`` and return aggregated, redacted findings.

    Findings are sorted severity-high-first, then by occurrence count descending. Every
    detector always runs and counts at most ``_MAX_COUNTED_MATCHES`` valid matches while
    inspecting at most ``_MAX_EXAMINED_MATCHES`` raw candidates; ``truncated`` records
    that some detector reached either cap. The examined cap keeps cost linear even when a
    document is engineered so that most candidates fail their validator.

    ``has_gcp_key`` is a document-level property, evaluated once: whether any private-key
    material is present at all. Detectors flagged ``requires_gcp_key`` (the GCP
    service-account block) are pure documentation without it, so they are skipped wholesale
    rather than re-scanning the full text on every match - the source of the previous
    O(n^2) blowup.
    """
    if not text:
        return ScanReport(findings=[], truncated=False)
    has_gcp_key = bool(_GCP_PRIVATE_KEY_RE.search(text))
    findings: list[SecretFinding] = []
    truncated = False
    for detector in _DETECTORS:
        if detector.requires_gcp_key and not has_gcp_key:
            continue
        occurrences = 0
        examined = 0
        samples: list[SecretSample] = []
        for match in detector.pattern.finditer(text):
            if examined >= _MAX_EXAMINED_MATCHES:
                truncated = True
                break
            examined += 1
            if detector.validator is not None and not detector.validator(match):
                continue
            if occurrences >= _MAX_COUNTED_MATCHES:
                truncated = True
                break
            occurrences += 1
            if len(samples) < _MAX_SAMPLES:
                secret = match.group(detector.group)
                redacted = detector.fixed_redaction or _redact(secret)
                line = text.count("\n", 0, match.start(detector.group)) + 1
                samples.append(SecretSample(redacted=redacted, line=line))
        if occurrences:
            findings.append(
                SecretFinding(
                    detector=detector.id,
                    label=detector.label,
                    severity=detector.severity,
                    occurrences=occurrences,
                    samples=samples,
                )
            )
    findings.sort(
        key=lambda f: (_SEVERITY_RANK.get(f.severity, len(_SEVERITY_RANK)), -f.occurrences)
    )
    return ScanReport(findings=findings, truncated=truncated)


def summarize_findings(report: ScanReport) -> tuple[list[str], int]:
    """Return ``(sorted unique detector ids, total occurrences)`` for ``report``.

    Shared by the ingestion quarantine audit and the MCP review surface so both describe
    a flagged document with the same aggregation.
    """
    detectors = sorted({finding.detector for finding in report.findings})
    occurrences = sum(finding.occurrences for finding in report.findings)
    return detectors, occurrences


def report_to_meta(report: ScanReport, *, checksum: str | None, flagged_at: str) -> dict:
    """Serialize ``report`` into the JSON payload stored under ``meta[SCAN_META_KEY]``."""
    return {
        "flagged_at": flagged_at,
        "checksum": checksum,
        "truncated": report.truncated,
        "findings": [
            {
                "detector": finding.detector,
                "label": finding.label,
                "severity": finding.severity,
                "occurrences": finding.occurrences,
                "samples": [
                    {"redacted": sample.redacted, "line": sample.line} for sample in finding.samples
                ],
            }
            for finding in report.findings
        ],
    }


def is_approved(meta: dict | None, checksum: str | None) -> bool:
    """True iff ``meta`` carries an approval stamped for exactly ``checksum``.

    A falsy checksum never counts as approved, so changed or unknown content always
    requires a fresh review.
    """
    if not checksum or not isinstance(meta, dict):
        return False
    scan = meta.get(SCAN_META_KEY)
    if not isinstance(scan, dict):
        return False
    approved = scan.get("approved")
    if not isinstance(approved, dict):
        return False
    return approved.get("checksum") == checksum


def mark_approved(
    meta: dict | None,
    *,
    checksum: str | None,
    user_id: str | None,
    at: str,
) -> dict:
    """Return a NEW meta dict with an approval stamp for ``checksum``.

    Existing keys (including the scan findings under ``SCAN_META_KEY``) are preserved;
    the input dict is never mutated, so callers can assign the result straight onto a
    JSON column without mutation-tracking pitfalls.
    """
    new_meta = dict(meta or {})
    scan = new_meta.get(SCAN_META_KEY)
    new_scan = dict(scan) if isinstance(scan, dict) else {}
    new_scan["approved"] = {"checksum": checksum, "by": user_id, "at": at}
    new_meta[SCAN_META_KEY] = new_scan
    return new_meta
