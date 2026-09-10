"""Heuristic PII / sensitive-data detection (DLP), a sibling of :mod:`secret_scan`.

Where the secret scanner tripwires leaked *credentials*, this classifies documents that
carry *personal or confidential data* (SSNs, payment cards, IBANs, emails, phone numbers)
so they can be labelled (``Document.sensitivity``) and surfaced in the oversharing report.

Same guarantees as the secret scanner: raw values never leave this module (samples are
redacted), every pattern is linear-safe, and per-detector match counts are bounded. This
is a heuristic classifier, not a compliance-grade DLP engine.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.models.enums import SensitivityLevel

DLP_META_KEY = "dlp_scan"

_MAX_SAMPLES = 3
_MAX_COUNTED_MATCHES = 1000
_MAX_EXAMINED_MATCHES = 5000
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

_SEVERITY_TO_LEVEL = {
    "high": SensitivityLevel.CONFIDENTIAL,
    "medium": SensitivityLevel.PII,
    "low": SensitivityLevel.PII,
}
"""High-severity PII (financial / government id) implies CONFIDENTIAL; anything else PII."""


@dataclass(frozen=True)
class DlpSample:
    redacted: str
    line: int


@dataclass(frozen=True)
class DlpFinding:
    detector: str
    label: str
    severity: str
    occurrences: int
    samples: list[DlpSample]


@dataclass(frozen=True)
class DlpReport:
    findings: list[DlpFinding]
    sensitivity: SensitivityLevel
    truncated: bool

    @property
    def flagged(self) -> bool:
        return bool(self.findings)


def _redact_tail(value: str, keep: int = 4) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) <= keep:
        return "····"
    return "•" * (len(digits) - keep) + digits[-keep:]


def _redact_email(value: str) -> str:
    local, _, domain = value.partition("@")
    head = local[0] if local else ""
    return f"{head}···@{domain}"


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _credit_card_ok(match: re.Match[str]) -> bool:
    digits = re.sub(r"[ -]", "", match.group(0))
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def _ssn_ok(match: re.Match[str]) -> bool:
    """Reject structurally-invalid US SSNs (area 000/666/9xx, group 00, serial 0000)."""
    area, group, serial = match.group(1), match.group(2), match.group(3)
    if area in {"000", "666"} or area[0] == "9":
        return False
    return group != "00" and serial != "0000"


@dataclass(frozen=True)
class _Detector:
    id: str
    label: str
    severity: str
    pattern: re.Pattern[str]
    redactor: Callable[[str], str]
    validator: Callable[[re.Match[str]], bool] | None = None


_DETECTORS: tuple[_Detector, ...] = (
    _Detector(
        id="us-ssn",
        label="US Social Security number",
        severity="high",
        pattern=re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b"),
        redactor=lambda v: "•••-••-" + v[-4:],
        validator=_ssn_ok,
    ),
    _Detector(
        id="credit-card",
        label="Payment card number",
        severity="high",
        pattern=re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
        redactor=_redact_tail,
        validator=_credit_card_ok,
    ),
    _Detector(
        id="iban",
        label="IBAN (bank account)",
        severity="high",
        pattern=re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        redactor=_redact_tail,
    ),
    _Detector(
        id="phone",
        label="Phone number",
        severity="medium",
        pattern=re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
        redactor=_redact_tail,
    ),
    _Detector(
        id="email",
        label="Email address",
        severity="low",
        pattern=re.compile(
            r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,10}"
            r"\.[A-Za-z]{2,24}\b"
        ),
        redactor=_redact_email,
    ),
)
r"""The detectors, in the order they are run.

Every quantifier is bounded and no two adjacent classes overlap, so match cost is constant
per attempt and the scan stays linear in the input size. The email detector is the
cautionary tale: its previous ``[A-Za-z0-9.-]+\.[A-Za-z]{2,}`` had an unbounded ``+`` over a
class that also contained the following literal ``.``, which backtracked quadratically on
crafted zero-match input (``('a.'*n)@('b.'*n)!``) - a worker-pegging DoS."""


def scan_text(text: str) -> DlpReport:
    """Scan ``text`` for PII and return aggregated, redacted findings + a sensitivity level."""
    if not text:
        return DlpReport(findings=[], sensitivity=SensitivityLevel.NONE, truncated=False)
    findings: list[DlpFinding] = []
    truncated = False
    for detector in _DETECTORS:
        occurrences = 0
        examined = 0
        samples: list[DlpSample] = []
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
                line = text.count("\n", 0, match.start()) + 1
                samples.append(DlpSample(redacted=detector.redactor(match.group(0)), line=line))
        if occurrences:
            findings.append(
                DlpFinding(
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
    sensitivity = SensitivityLevel.NONE
    for finding in findings:
        level = _SEVERITY_TO_LEVEL.get(finding.severity, SensitivityLevel.PII)
        if level == SensitivityLevel.CONFIDENTIAL:
            sensitivity = SensitivityLevel.CONFIDENTIAL
            break
        sensitivity = SensitivityLevel.PII
    return DlpReport(findings=findings, sensitivity=sensitivity, truncated=truncated)


def report_to_meta(report: DlpReport) -> dict:
    """Serialize a report into the JSON stored under ``meta[DLP_META_KEY]``."""
    return {
        "sensitivity": report.sensitivity.value,
        "truncated": report.truncated,
        "findings": [
            {
                "detector": f.detector,
                "label": f.label,
                "severity": f.severity,
                "occurrences": f.occurrences,
                "samples": [{"redacted": s.redacted, "line": s.line} for s in f.samples],
            }
            for f in report.findings
        ],
    }
