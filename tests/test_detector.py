"""Detector pure helpers (DESIGN §4.2) — fingerprinting, slug guessing, status.
No network: detect() itself is not exercised here."""
import pytest

from jobradar import detector
from jobradar.detector import (
    Detection, _scan, _slug_candidates, _careers_candidates,
    detection_status, status_for_ats,
    STATUS_READY, STATUS_NO_ADAPTER, STATUS_NOT_DETECTED, STATUS_FETCH_FAILED,
)


@pytest.mark.parametrize("text,expected", [
    ("https://boards.greenhouse.io/helsing", ("greenhouse", "helsing")),
    ("https://boards-api.greenhouse.io/v1/boards/helsing/jobs", ("greenhouse", "helsing")),
    ("https://jobs.lever.co/spotify", ("lever", "spotify")),
    ("https://jobs.ashbyhq.com/ElevenLabs", ("ashby", "ElevenLabs")),
    ("https://acme.jobs.personio.com/", ("personio", "acme")),
    ("https://acme.wd3.myworkdayjobs.com/careers", ("workday", "acme")),
])
def test_scan_extracts_ats_and_slug(text, expected):
    assert _scan(text) == expected


def test_scan_ignores_non_slug_tokens_and_misses():
    assert _scan("https://example.com/about") is None
    # 'www' is a non-slug token for personio's marketing host
    assert _scan("https://www.jobs.personio.com") is None


def test_slug_candidates_from_name_and_domain():
    cands = _slug_candidates("Eleven Labs", "https://elevenlabs.io/careers")
    assert "elevenlabs" in cands          # name with spaces removed / domain root
    assert "eleven-labs" in cands         # hyphenated name form
    assert len(cands) == len(set(cands))  # de-duped


def test_careers_candidates_pulls_links():
    html = '<a href="/careers">Jobs</a> <a href="https://x.com/about">x</a>'
    out = _careers_candidates(html, "https://x.com")
    assert "https://x.com/careers" in out
    assert all("about" not in u for u in out)


def test_detection_status_resolved():
    assert detection_status(Detection("greenhouse", "x", "validated")) == STATUS_READY
    assert detection_status(Detection("workday", "x", "detected_no_adapter")) == STATUS_NO_ADAPTER


def test_detection_status_unresolved():
    assert detection_status(Detection(None, None, "fetch_failed")) == STATUS_FETCH_FAILED
    assert detection_status(Detection(None, None, "no_fingerprint")) == STATUS_NOT_DETECTED


def test_status_for_ats():
    assert status_for_ats("greenhouse") == STATUS_READY
    assert status_for_ats("workday") == STATUS_NO_ADAPTER


def test_detection_resolved_property():
    assert Detection("lever", "spotify", "validated").resolved is True
    assert Detection(None, None, "x").resolved is False


def test_adapter_backed_set_matches_pipeline_adapters():
    # Guard: the validated set must equal the adapters the pipeline actually wires up.
    from jobradar.pipeline import _ADAPTERS
    assert detector._ADAPTER_BACKED == set(_ADAPTERS)
