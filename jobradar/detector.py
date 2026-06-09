"""ATS detector — turn name + careers_url into (ats, slug) automatically (DESIGN §4.2).

Strategy:
1. Fetch the given URL (following redirects). If it looks like a homepage rather
   than a careers page and no ATS fingerprint is found, follow a careers/jobs link.
2. Scan the final URL + HTML (links, script srcs, iframes) for known ATS
   fingerprints and extract the slug.
3. Validate the slug against the ATS's real API where we have an adapter, so a
   detected slug that returns no board is treated as unresolved.
"""
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Fingerprint patterns. Each ATS maps to a list of regexes whose first capture
# group is the slug. Patterns are matched against both the final URL and the
# page HTML. Order within a list is most-specific first.
_FINGERPRINTS = {
    "greenhouse": [
        r"boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)",
        r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_-]+)",
        r"greenhouse\.io/embed/job_board\?for=([a-zA-Z0-9_-]+)",
    ],
    "lever": [
        r"api\.lever\.co/v0/postings/([a-zA-Z0-9_-]+)",
        r"jobs\.lever\.co/([a-zA-Z0-9_-]+)",
    ],
    "ashby": [
        r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)",
        r"jobs\.ashbyhq\.com/([a-zA-Z0-9_%.-]+)",
    ],
    "personio": [
        r"([a-zA-Z0-9_-]+)\.jobs\.personio\.(?:com|de)",
    ],
    # Detected and flagged, but no adapter yet (DESIGN §4.3 priority order).
    "workday": [
        r"([a-zA-Z0-9_-]+)\.[a-z0-9]+\.myworkdayjobs\.com",
        r"([a-zA-Z0-9_-]+)\.myworkdayjobs\.com",
    ],
    "smartrecruiters": [
        r"api\.smartrecruiters\.com/v1/companies/([a-zA-Z0-9_-]+)",
        r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
    ],
    "recruitee": [
        r"([a-zA-Z0-9_-]+)\.recruitee\.com",
    ],
}

# ATSes we have a working adapter for (so detection can be validated).
_ADAPTER_BACKED = {"greenhouse", "lever", "ashby", "personio"}

# Human-readable detect outcomes, written back into the company source (DESIGN §4.2)
# so the skipped-but-silent cases are visible at a glance instead of buried in logs.
STATUS_READY = "✓ ready"            # resolved to an ATS we can fetch
STATUS_NO_ADAPTER = "⚠ no adapter"  # real ATS detected, but no adapter yet (see ATS column)
STATUS_NOT_DETECTED = "✗ not detected"  # no fingerprint and slug-guess failed
STATUS_FETCH_FAILED = "✗ fetch failed"  # careers URL never loaded


def detection_status(det: "Detection") -> str:
    """Map a Detection to the status string written back to the company source."""
    if det.resolved:
        return STATUS_READY if det.ats in _ADAPTER_BACKED else STATUS_NO_ADAPTER
    if det.source == "fetch_failed":
        return STATUS_FETCH_FAILED
    return STATUS_NOT_DETECTED


def status_for_ats(ats: str) -> str:
    """Status for an already-resolved row (skipped by detect), from its ATS alone."""
    return STATUS_READY if (ats or "").strip().lower() in _ADAPTER_BACKED else STATUS_NO_ADAPTER

_CAREERS_LINK_RE = re.compile(r"(careers|jobs|join|join-us|join_us|werkenbij|stellen)", re.I)
_COMMON_CAREERS_PATHS = ["/careers", "/jobs", "/join", "/company/careers", "/about/careers"]


@dataclass
class Detection:
    ats: Optional[str]
    slug: Optional[str]
    source: str  # how it was found, for debugging / flagging (incl. "validated")

    @property
    def resolved(self) -> bool:
        return bool(self.ats and self.slug)


def _scan(text: str) -> Optional[tuple[str, str]]:
    """Return (ats, slug) for the first fingerprint that matches text, else None."""
    for ats, patterns in _FINGERPRINTS.items():
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                slug = m.group(1)
                # Personio's own marketing host uses 'www'; ignore obvious non-slugs.
                if slug.lower() in {"www", "jobs", "careers", "embed"}:
                    continue
                return ats, slug
    return None


def _validate(ats: str, slug: str) -> bool:
    """Confirm a detected slug actually resolves to a board on its ATS."""
    if ats not in _ADAPTER_BACKED:
        return False  # can't validate ATSes we don't have an adapter for
    try:
        if ats == "greenhouse":
            from jobradar.adapters.greenhouse import GreenhouseAdapter
            return len(GreenhouseAdapter().fetch(slug)) > 0
        if ats == "lever":
            from jobradar.adapters.lever import LeverAdapter
            # Lever returns [] for unknown slugs (200) and 404 for bad ones.
            return len(LeverAdapter().fetch(slug)) > 0
        if ats == "ashby":
            from jobradar.adapters.ashby import AshbyAdapter
            return len(AshbyAdapter().fetch(slug)) > 0
        if ats == "personio":
            from jobradar.adapters.personio import PersonioAdapter
            return len(PersonioAdapter().fetch(slug)) > 0
    except Exception:
        return False
    return False


def _slug_candidates(name: str, careers_url: str) -> list[str]:
    """Generate likely slugs from a company name + careers URL domain, most-likely first."""
    name_l = name.strip().lower()
    netloc = urlparse(careers_url).netloc.lower()
    # Strip common subdomains and the TLD to get a domain root.
    host = re.sub(r"^(www|careers|jobs|job|boards|apply|work|life|join)\.", "", netloc)
    domain_root = host.split(".")[0] if host else ""

    raw = [
        name_l.replace(" ", ""),
        domain_root,
        name_l.replace(" ", "-"),
        domain_root.replace("-", ""),
        name_l.replace(" ", "_"),
    ]
    seen, out = set(), []
    for c in raw:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _guess(name: str, careers_url: str) -> Optional[tuple[str, str]]:
    """Probe adapter-backed ATS APIs with candidate slugs; first that resolves wins.

    Validation by construction: a candidate is only accepted if its board actually
    returns postings. Used as a fallback when static HTML fingerprinting fails
    (common for JS-rendered SPA careers pages).
    """
    for slug in _slug_candidates(name, careers_url):
        for ats in ("greenhouse", "lever", "ashby", "personio"):
            if _validate(ats, slug):
                return ats, slug
    return None


def _fetch(url: str, session: requests.Session) -> Optional[requests.Response]:
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        if resp.status_code == 200:
            return resp
    except Exception:
        pass
    return None


def _careers_candidates(html: str, base_url: str) -> list[str]:
    """Extract likely careers-page URLs from a homepage's links."""
    candidates: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        href = m.group(1)
        if _CAREERS_LINK_RE.search(href):
            candidates.append(urljoin(base_url, href))
    # De-dupe, preserve order.
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def detect(name: str, careers_url: str) -> Detection:
    """Classify the ATS + slug for a company from its name + careers URL."""
    session = requests.Session()
    session.headers.update(_HEADERS)

    resp = _fetch(careers_url, session)
    hit = None

    if resp is not None:
        # Scan the final (post-redirect) URL first, then the page body.
        hit = _scan(resp.url) or _scan(resp.text)

    # Homepage fallback: no fingerprint here — try careers/jobs links and paths.
    if hit is None and resp is not None:
        tried = set()
        for cand in _careers_candidates(resp.text, resp.url):
            if cand in tried:
                continue
            tried.add(cand)
            sub = _fetch(cand, session)
            if sub is None:
                continue
            hit = _scan(sub.url) or _scan(sub.text)
            if hit:
                break

        if hit is None:
            origin = f"{urlparse(careers_url).scheme}://{urlparse(careers_url).netloc}"
            for path in _COMMON_CAREERS_PATHS:
                cand = origin + path
                if cand in tried:
                    continue
                sub = _fetch(cand, session)
                if sub is None:
                    continue
                hit = _scan(sub.url) or _scan(sub.text)
                if hit:
                    break

    # Static fingerprinting failed (or the page never loaded) — fall back to
    # guessing the slug from the name/domain and probing the real ATS APIs.
    if hit is None:
        guessed = _guess(name, careers_url)
        if guessed:
            return Detection(guessed[0], guessed[1], source="guessed")
        return Detection(None, None, source=("fetch_failed" if resp is None else "no_fingerprint"))

    ats, slug = hit
    validated = _validate(ats, slug)
    source = "validated" if validated else (
        "detected_no_adapter" if ats not in _ADAPTER_BACKED else "detected_unvalidated"
    )
    return Detection(ats, slug, source=source)
