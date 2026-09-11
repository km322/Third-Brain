"""Pure-logic tests for :mod:`app.services.secret_scan`.

Written from the quarantine contract, not the implementation: one positive case per
detector with a realistic fake secret, negatives for prose/docs/placeholders, exact
redaction semantics (the full secret must never appear anywhere), line numbers,
ordering, the 1000-match truncation cap, and the report_to_meta / is_approved /
mark_approved approval round-trip keyed to the document checksum.

The credential constants below are realistic-shaped but fake.
"""

from __future__ import annotations

import json

from app.services.secret_scan import (
    SCAN_META_KEY,
    ScanReport,
    SecretFinding,
    SecretSample,
    is_approved,
    mark_approved,
    report_to_meta,
    scan_text,
)

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GITHUB_PAT = (
    "github_pat_11ABCDEFG0123456789abc_cdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345678"
)
GITLAB_PAT = "glpat-x9Y2kQ8vN4mP7rT1wZ6u"
SLACK_TOKEN = "xoxb-2489462951-3814592043-Ab3dEf6hIj9lMn2pQr5t"
STRIPE_LIVE = "sk_live_9HqLyjWDarjtT1zdp7dcAB"
STRIPE_TEST = "rk_test_9HqLyjWDarjtT1zdp7dcAB"
GOOGLE_KEY = "AIzaSyA1bC2dE3fG4hI5jK6lM7nO8pQ9rS0tU1v"
OPENAI_KEY = "sk-Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4Cd5Ef6"
OPENAI_PROJ_KEY = "sk-proj-Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0"
ANTHROPIC_KEY = "sk-ant-api03-kZx8Qw2NvP4mR7tY1uH5jB0cD3eF6gA9sV2xZ4aB7cD"
SENDGRID_KEY = "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"
NPM_TOKEN = "npm_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
PYPI_TOKEN = "pypi-AgEIcHlwaS5vcmcAiRlbnRyeS1wcm9qZWN0LXRva2VuAAIsWyJhIl0"
TB_API_KEY = "tb_live_9fK3xQ8vZ2mN7pL4wR6tY1uH5jB0cD8eF2gA4sV6xZ8"
JWT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    ".dozjgNryP4J3jVmNHl0w5N7flQADFXY"
)
JWT_IO_EXAMPLE = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
"""The canonical jwt.io sample verbatim; appears across API docs, must not flag."""
AZURE_ACCOUNT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)
AZURE_CONN_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=devstoreaccount1;"
    f"AccountKey={AZURE_ACCOUNT_KEY};EndpointSuffix=core.windows.net"
)
PRIVATE_KEY_BODY = "MIIEowIBAAKCAQEA7bq0xM7cCQ1rV0dSxCbeYCUmX2v9Sd3LMOsvAK3mSt2"
PRIVATE_KEY_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n" + PRIVATE_KEY_BODY + "\n-----END RSA PRIVATE KEY-----"
)
URL_PASSWORD = "S3cr3t-Pa55w0rd"
URL_WITH_CREDS = f"postgres://svc_ingest:{URL_PASSWORD}@db.internal:5432/app"
KEYWORD_VALUE = "u8Kj2mQx9Lp4Vn7Rw3Ts"

ALL_SECRETS = [
    AWS_KEY,
    GITHUB_TOKEN,
    GITHUB_PAT,
    GITLAB_PAT,
    SLACK_TOKEN,
    STRIPE_LIVE,
    STRIPE_TEST,
    GOOGLE_KEY,
    OPENAI_KEY,
    OPENAI_PROJ_KEY,
    ANTHROPIC_KEY,
    SENDGRID_KEY,
    NPM_TOKEN,
    PYPI_TOKEN,
    TB_API_KEY,
    JWT_TOKEN,
]


def _finding(report: ScanReport, detector: str) -> SecretFinding:
    hits = [f for f in report.findings if f.detector == detector]
    assert hits, f"{detector} did not fire; got {[f.detector for f in report.findings]}"
    return hits[0]


def _meta_dump(report: ScanReport) -> str:
    return json.dumps(report_to_meta(report, checksum="cs", flagged_at="2026-07-09T00:00:00Z"))


class TestDetectors:
    """One positive case per detector."""

    def test_aws_access_key_id(self) -> None:
        report = scan_text(f"The AWS key id {AWS_KEY} was pasted into a doc.")
        finding = _finding(report, "aws-access-key-id")
        assert finding.severity == "high"
        assert finding.occurrences == 1
        assert finding.samples[0].redacted == "AKIA…LE"

    def test_private_key_block(self) -> None:
        report = scan_text(f"backup of the server\n{PRIVATE_KEY_BLOCK}\n")
        finding = _finding(report, "private-key")
        assert finding.severity == "high"
        assert PRIVATE_KEY_BODY not in _meta_dump(report)
        for sample in finding.samples:
            assert PRIVATE_KEY_BODY not in sample.redacted

    def test_gcp_service_account_json(self) -> None:
        text = (
            '{"type": "service_account", "project_id": "acme-prod",\n'
            '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIleaked\\n'
            '-----END PRIVATE KEY-----\\n"}'
        )
        report = scan_text(text)
        finding = _finding(report, "gcp-service-account")
        assert finding.severity == "high"
        assert "service_account" not in _meta_dump(report)

    def test_azure_storage_account_key(self) -> None:
        report = scan_text(AZURE_CONN_STRING)
        finding = _finding(report, "azure-storage-account-key")
        assert finding.severity == "high"
        assert AZURE_ACCOUNT_KEY not in _meta_dump(report)
        for sample in finding.samples:
            assert AZURE_ACCOUNT_KEY not in sample.redacted

    def test_github_token(self) -> None:
        report = scan_text(f"pushed with {GITHUB_TOKEN} by mistake")
        finding = _finding(report, "github-token")
        assert finding.severity == "high"
        assert finding.samples[0].redacted == "ghp_…r8"

    def test_github_fine_grained_pat(self) -> None:
        finding = _finding(scan_text(f"CI uses {GITHUB_PAT}"), "github-fine-grained-pat")
        assert finding.severity == "high"

    def test_gitlab_pat(self) -> None:
        finding = _finding(scan_text(f"runner registered via {GITLAB_PAT}"), "gitlab-pat")
        assert finding.severity == "high"

    def test_slack_token(self) -> None:
        finding = _finding(scan_text(f"bot connects with {SLACK_TOKEN}"), "slack-token")
        assert finding.severity == "high"

    def test_stripe_live_key_is_high(self) -> None:
        finding = _finding(scan_text(f"charge failed for {STRIPE_LIVE}"), "stripe-live-key")
        assert finding.severity == "high"

    def test_stripe_test_key_is_medium(self) -> None:
        finding = _finding(scan_text(f"sandbox uses {STRIPE_TEST}"), "stripe-test-key")
        assert finding.severity == "medium"

    def test_google_api_key(self) -> None:
        finding = _finding(scan_text(f"maps embed {GOOGLE_KEY}"), "google-api-key")
        assert finding.severity == "high"

    def test_openai_key(self) -> None:
        finding = _finding(scan_text(f"completions with {OPENAI_KEY}"), "openai-api-key")
        assert finding.severity == "high"

    def test_openai_project_key(self) -> None:
        finding = _finding(scan_text(f"scoped {OPENAI_PROJ_KEY} here"), "openai-api-key")
        assert finding.severity == "high"

    def test_anthropic_key_fires_only_anthropic(self) -> None:
        report = scan_text(f"claude call used {ANTHROPIC_KEY}")
        assert {f.detector for f in report.findings} == {"anthropic-api-key"}
        assert report.findings[0].severity == "high"

    def test_sendgrid_key(self) -> None:
        finding = _finding(scan_text(f"mailer set to {SENDGRID_KEY}"), "sendgrid-api-key")
        assert finding.severity == "high"

    def test_npm_token(self) -> None:
        finding = _finding(scan_text(f"publish used {NPM_TOKEN}"), "npm-token")
        assert finding.severity == "high"

    def test_pypi_token(self) -> None:
        finding = _finding(scan_text(f"twine upload with {PYPI_TOKEN}"), "pypi-token")
        assert finding.severity == "high"

    def test_third_brain_api_key(self) -> None:
        report = scan_text(f"Authorization header was Bearer {TB_API_KEY}")
        finding = _finding(report, "third-brain-api-key")
        assert finding.severity == "high"

    def test_jwt_is_medium(self) -> None:
        finding = _finding(scan_text(f"session cookie held {JWT_TOKEN}"), "jwt")
        assert finding.severity == "medium"

    def test_url_credentials_redact_only_the_password(self) -> None:
        report = scan_text(f"connect with {URL_WITH_CREDS} from the VPN")
        finding = _finding(report, "url-credentials")
        assert finding.severity == "high"
        assert finding.samples[0].redacted == "S3cr…rd"
        assert URL_PASSWORD not in _meta_dump(report)

    def test_keyword_assignment_equals(self) -> None:
        report = scan_text(f'db_password = "{KEYWORD_VALUE}"')
        finding = _finding(report, "keyword-assignment")
        assert finding.severity == "medium"
        assert finding.samples[0].redacted == "u8Kj…Ts"

    def test_keyword_assignment_colon(self) -> None:
        report = scan_text("api key: 9fQ2mX7pL4wR8tZ1kV6y")
        assert _finding(report, "keyword-assignment").occurrences == 1

    def test_keyword_assignment_real_credentials_flag(self) -> None:
        for text in (
            'api_key = "9fQ2mX7pL4wR8tZ1kV6yH3jB0cD8eF2g"',
            "password: 'Tr0ub4dor&3x!9'",
            "token=ghu8Zk2mQw17",
        ):
            assert _finding(scan_text(text), "keyword-assignment").severity == "medium", text

    def test_secret_signing_encryption_key_assignments_flag(self) -> None:
        value = "9fQ2mX7pL4wR8tZ1kV6yH3jB0cD8eF2g"
        for key in ("SECRET_KEY", "signing_key", "encryption-key"):
            report = scan_text(f'{key} = "{value}"')
            assert _finding(report, "keyword-assignment").severity == "medium", key

    def test_random_real_shaped_jwt_flags(self) -> None:
        token = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJ1c2VyIjoiYWxpY2UiLCJyb2xlIjoiYWRtaW4iLCJvcmciOjQyfQ"
            ".Kq3mZ9x1LpWr7tYuHnBvCdEfGh0jKlMnOpQrStUvWxYz01"
        )
        assert _finding(scan_text(token), "jwt").severity == "medium"


class TestNegatives:
    """Negatives: prose, docs, placeholders, code."""

    def test_plain_prose_is_clean(self) -> None:
        text = (
            "Third Brain ingests your company's knowledge so every tool can search it.\n"
            "Upload documents, connect sources, and ask questions with citations.\n"
            "The password: is rotated monthly by the platform team.\n"
        )
        report = scan_text(text)
        assert report.flagged is False
        assert report.findings == []
        assert report.truncated is False

    def test_markdown_doc_is_clean(self) -> None:
        text = (
            "# Deploy guide\n\n"
            "Set these environment variables before running the stack:\n\n"
            "    export DATABASE_URL=$THIRD_BRAIN_DB\n"
            '    export API_KEY="$MY_API_KEY"\n\n'
            "Commit 3f786850e387550fdab836ed7e6dc881de23001b shipped the fix, and\n"
            "request 123e4567-e89b-12d3-a456-426614174000 shows the trace end to end.\n"
        )
        assert scan_text(text).flagged is False

    def test_env_example_placeholders_are_clean(self) -> None:
        text = (
            "# Example configuration - copy to .env and fill in\n"
            "API_KEY=YOUR_API_KEY_HERE\n"
            "ADMIN_PASSWORD=changeme\n"
            "DB_PASSWORD=<your-password>\n"
            "SLACK_TOKEN=xoxb-your-token-here\n"
            "STRIPE_SECRET_KEY=sk_live_xxxxxxxxxxxxxxxxxxxxxxxx\n"
            "SESSION_SECRET=${SESSION_SECRET}\n"
            "OPENAI_API_KEY=\n"
        )
        report = scan_text(text)
        assert report.flagged is False
        assert report.findings == []

    def test_git_sha_in_prose_is_clean(self) -> None:
        text = "Deployed 3f786850e387550fdab836ed7e6dc881de23001b to production yesterday."
        assert scan_text(text).flagged is False

    def test_uuid_in_prose_is_clean(self) -> None:
        text = "Incident 123e4567-e89b-12d3-a456-426614174000 resolved after failover."
        assert scan_text(text).flagged is False

    def test_code_referencing_env_vars_is_clean(self) -> None:
        text = (
            "import os\n\n"
            'password = os.environ["DB_PASSWORD"]\n'
            'token = os.getenv("API_TOKEN", "")\n'
            "api_key = settings.OPENAI_API_KEY\n"
            "client_secret = process.env.CLIENT_SECRET\n"
        )
        assert scan_text(text).flagged is False

    def test_keyword_value_without_digits_or_symbols_is_clean(self) -> None:
        assert scan_text("password=abcdefghijklmnop").flagged is False

    def test_keyword_low_entropy_value_is_clean(self) -> None:
        assert scan_text("password=aaaabbbb1111!!!!").flagged is False

    def test_keyword_value_equal_to_key_name_is_clean(self) -> None:
        assert scan_text("client_secret=CLIENT-SECRET").flagged is False

    def test_keyword_code_url_and_prose_values_are_clean(self) -> None:
        for text in (
            "const apiKey = getApiKey();",
            "api_key = load_api_key_from_vault()",
            'token = localStorage.getItem("session")',
            "token = jwt.encode(payload, key)",
            "Forgot password: https://app.acme.io/reset-password",
            "password: state-of-the-art-encryption",
        ):
            assert scan_text(text).flagged is False, text

    def test_secret_key_placeholder_is_clean(self) -> None:
        assert scan_text("SECRET_KEY=changeme").flagged is False

    def test_gcp_service_account_format_doc_is_clean(self) -> None:
        text = '{"type": "service_account", "project_id": "YOUR_PROJECT"}'
        assert scan_text(text).flagged is False

    def test_azure_placeholder_account_key_is_clean(self) -> None:
        assert scan_text("AccountKey=" + "A" * 88).flagged is False

    def test_jwt_io_example_token_is_skipped(self) -> None:
        assert scan_text(f"Docs show a token like {JWT_IO_EXAMPLE} in the header.").flagged is False
        resigned = JWT_IO_EXAMPLE.rsplit(".", 1)[0] + ".DIFFERENTsignature01234abcd"
        assert scan_text(resigned).flagged is False

    def test_aws_like_string_inside_longer_word_is_clean(self) -> None:
        assert scan_text(f"X{AWS_KEY}").flagged is False

    def test_empty_and_whitespace_inputs(self) -> None:
        assert scan_text("").flagged is False
        assert scan_text("   \n\t ").flagged is False


class TestRedaction:
    """Redaction: the full secret never appears anywhere."""

    def test_full_secret_never_in_samples_or_meta(self) -> None:
        for secret in ALL_SECRETS:
            report = scan_text(f"value {secret} end")
            assert report.flagged, f"expected a finding for {secret[:6]}..."
            dump = _meta_dump(report)
            assert secret not in dump
            for finding in report.findings:
                for sample in finding.samples:
                    assert secret not in sample.redacted

    def test_long_secret_keeps_first_four_and_last_two(self) -> None:
        sample = _finding(scan_text(AWS_KEY), "aws-access-key-id").samples[0]
        assert sample.redacted == AWS_KEY[:4] + "…" + AWS_KEY[-2:]

    def test_short_secret_is_fully_masked(self) -> None:
        """An 8-char URL password must be masked with no partial characters at all."""
        report = scan_text("postgres://app:q7Zw2Xy9@db.internal/app")
        sample = _finding(report, "url-credentials").samples[0]
        assert sample.redacted == "····"
        assert "q7Zw2Xy9" not in _meta_dump(report)


class TestAggregation:
    """Aggregation: line numbers, sample cap, ordering, truncation."""

    def test_line_numbers_are_one_based(self) -> None:
        text = f"intro\n\n{AWS_KEY}\nmiddle\nend {AWS_KEY}"
        finding = _finding(scan_text(text), "aws-access-key-id")
        assert finding.occurrences == 2
        assert [s.line for s in finding.samples] == [3, 5]

    def test_samples_capped_at_three_but_occurrences_keep_counting(self) -> None:
        text = "\n".join([AWS_KEY] * 5)
        finding = _finding(scan_text(text), "aws-access-key-id")
        assert finding.occurrences == 5
        assert len(finding.samples) == 3
        assert [s.line for s in finding.samples] == [1, 2, 3]

    def test_findings_sorted_high_first_then_occurrences_desc(self) -> None:
        text = "\n".join(
            [
                f"stripe {STRIPE_TEST}",
                f"jwt {JWT_TOKEN}",
                f"jwt {JWT_TOKEN}",
                f"jwt {JWT_TOKEN}",
                f"aws {AWS_KEY}",
            ]
        )
        report = scan_text(text)
        assert [f.detector for f in report.findings] == [
            "aws-access-key-id",
            "jwt",
            "stripe-test-key",
        ]
        assert [f.severity for f in report.findings] == ["high", "medium", "medium"]

    def test_truncation_cap_at_1000_matches(self) -> None:
        text = "\n".join([AWS_KEY] * 1005)
        report = scan_text(text)
        assert report.truncated is True
        assert _finding(report, "aws-access-key-id").occurrences == 1000
        meta = report_to_meta(report, checksum="cs", flagged_at="now")
        assert meta["truncated"] is True

    def test_per_detector_cap_still_runs_later_detectors(self) -> None:
        """Over 1000 AWS-key matches must not starve the private-key detector that runs
        later in the table; every detector always gets a chance to fire.
        """
        text = "\n".join([AWS_KEY] * 1005) + "\n" + PRIVATE_KEY_BLOCK
        report = scan_text(text)
        assert report.truncated is True
        assert _finding(report, "aws-access-key-id").occurrences == 1000
        assert _finding(report, "private-key").occurrences == 1

    def test_flagged_property(self) -> None:
        assert ScanReport(findings=[], truncated=False).flagged is False
        finding = SecretFinding(
            detector="d",
            label="l",
            severity="high",
            occurrences=1,
            samples=[SecretSample(redacted="····", line=1)],
        )
        assert ScanReport(findings=[finding], truncated=False).flagged is True


class TestMetaHelpers:
    """Meta payload + approval round-trip."""

    def test_scan_meta_key_value(self) -> None:
        assert SCAN_META_KEY == "secret_scan"

    def test_report_to_meta_shape(self) -> None:
        """The meta payload has the documented shape, and is JSON-serializable as stored on
        the Document row.
        """
        report = scan_text(f"key {AWS_KEY}")
        meta = report_to_meta(report, checksum="sha256:abc", flagged_at="2026-07-09T12:00:00Z")
        assert set(meta) == {"flagged_at", "checksum", "truncated", "findings"}
        assert meta["flagged_at"] == "2026-07-09T12:00:00Z"
        assert meta["checksum"] == "sha256:abc"
        assert meta["truncated"] is False
        entry = meta["findings"][0]
        assert set(entry) == {"detector", "label", "severity", "occurrences", "samples"}
        assert set(entry["samples"][0]) == {"redacted", "line"}
        json.dumps(meta)

    def test_report_to_meta_allows_none_checksum(self) -> None:
        meta = report_to_meta(scan_text(AWS_KEY), checksum=None, flagged_at="t")
        assert meta["checksum"] is None

    def test_approval_round_trip_keyed_to_checksum(self) -> None:
        """An approval only holds for the checksum it was granted against.

        A different checksum means the content changed, which sends the document back for
        re-review; a missing checksum never counts as approved.
        """
        report = scan_text(f"leak {AWS_KEY}")
        doc_meta = {SCAN_META_KEY: report_to_meta(report, checksum="c1", flagged_at="t1")}
        assert is_approved(doc_meta, "c1") is False

        approved = mark_approved(doc_meta, checksum="c1", user_id="u1", at="t2")
        assert approved is not doc_meta
        assert is_approved(approved, "c1") is True
        assert is_approved(approved, "c2") is False
        assert is_approved(approved, None) is False
        assert is_approved(approved, "") is False

    def test_mark_approved_preserves_findings_and_other_keys(self) -> None:
        """Stamping an approval keeps the findings and neighbouring keys, and copies rather
        than mutating: the input dict is never touched.
        """
        report = scan_text(f"leak {AWS_KEY}")
        doc_meta = {
            SCAN_META_KEY: report_to_meta(report, checksum="c1", flagged_at="t1"),
            "via": "mcp",
        }
        approved = mark_approved(doc_meta, checksum="c1", user_id="u1", at="t2")
        assert approved["via"] == "mcp"
        assert approved[SCAN_META_KEY]["findings"] == doc_meta[SCAN_META_KEY]["findings"]
        assert approved[SCAN_META_KEY]["flagged_at"] == "t1"
        assert approved[SCAN_META_KEY]["approved"] == {"checksum": "c1", "by": "u1", "at": "t2"}
        assert "approved" not in doc_meta[SCAN_META_KEY]

    def test_mark_approved_from_empty_meta(self) -> None:
        stamped = mark_approved(None, checksum="c9", user_id=None, at="t")
        assert is_approved(stamped, "c9") is True
        assert stamped[SCAN_META_KEY]["approved"]["by"] is None

    def test_none_checksum_never_approves(self) -> None:
        stamped = mark_approved(None, checksum=None, user_id="u", at="t")
        assert is_approved(stamped, None) is False

    def test_is_approved_tolerates_malformed_meta(self) -> None:
        assert is_approved(None, "c") is False
        assert is_approved({}, "c") is False
        assert is_approved({SCAN_META_KEY: "junk"}, "c") is False
        assert is_approved({SCAN_META_KEY: {"approved": "junk"}}, "c") is False
