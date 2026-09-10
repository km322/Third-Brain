"""Unit tests for the DLP / PII scanner, written from its contract.

Verifies detection + Luhn validation + severity->sensitivity classification and that raw
values are never emitted (only redacted samples).
"""

from __future__ import annotations

import json
import time

from app.models.enums import SensitivityLevel
from app.services.dlp_scan import report_to_meta, scan_text


class TestClassification:
    def test_ssn_and_card_are_confidential(self) -> None:
        report = scan_text("Employee SSN 123-45-6789 paid via card 4111 1111 1111 1111.")
        assert report.flagged
        assert report.sensitivity == SensitivityLevel.CONFIDENTIAL
        detectors = {f.detector for f in report.findings}
        assert "us-ssn" in detectors
        assert "credit-card" in detectors

    def test_email_only_is_pii(self) -> None:
        report = scan_text("Reach jane.doe@example.com about the offsite.")
        assert report.sensitivity == SensitivityLevel.PII
        assert {f.detector for f in report.findings} == {"email"}

    def test_phone_is_pii(self) -> None:
        report = scan_text("Call me at (415) 555-0142 tomorrow.")
        assert report.sensitivity == SensitivityLevel.PII
        assert any(f.detector == "phone" for f in report.findings)

    def test_clean_text_is_none(self) -> None:
        report = scan_text("The quarterly roadmap focuses on latency and reliability.")
        assert not report.flagged
        assert report.sensitivity == SensitivityLevel.NONE


class TestValidators:
    def test_luhn_invalid_card_not_detected(self) -> None:
        """The last digit of a valid test card is altered so Luhn fails."""
        report = scan_text("card 4111 1111 1111 1112 on file")
        assert not any(f.detector == "credit-card" for f in report.findings)

    def test_structurally_invalid_ssn_rejected(self) -> None:
        for bad in ("000-12-3456", "666-12-3456", "900-12-3456", "123-00-4567", "123-45-0000"):
            report = scan_text(f"id {bad} here")
            assert not any(f.detector == "us-ssn" for f in report.findings), bad


class TestLinearSafety:
    def test_email_detector_is_linear_on_adversarial_input(self) -> None:
        """A crafted zero-match string must not trigger super-linear (quadratic) scanning.

        The old ``[A-Za-z0-9.-]+\\.[A-Za-z]{2,}`` domain pattern backtracked quadratically on
        ``('a.'*n)@('b.'*n)!``; ~250k repeats (a 1 MB body) took minutes and pegged a worker.
        A generous 2 s ceiling over a 256 KB payload catches any reintroduced quadratic blowup
        while staying far from the (now ~60 ms) linear runtime, so it is not timing-flaky.
        """
        payload = ("a." * 64000) + "@" + ("b." * 64000) + "!"
        started = time.perf_counter()
        report = scan_text(payload)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"DLP scan took {elapsed:.2f}s - possible quadratic regression"
        assert not any(f.detector == "email" for f in report.findings)


class TestRedaction:
    def test_raw_values_never_emitted(self) -> None:
        """No raw match survives into the persisted metadata - only the redacted tails do."""
        report = scan_text("SSN 123-45-6789, card 4111 1111 1111 1111, mail bob@corp.com")
        blob = json.dumps(report_to_meta(report))
        assert "123-45-6789" not in blob
        assert "4111111111111111" not in blob
        assert "4111 1111 1111 1111" not in blob
        assert "bob@corp.com" not in blob
        assert "6789" in blob and "1111" in blob
