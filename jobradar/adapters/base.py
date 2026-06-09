import html
import re
from dataclasses import dataclass, field
from typing import Optional

# Description cap (chars). Bounds LLM input tokens per survivor; the first ~1500
# chars of a JD reliably carry role substance + seniority, which is what the fit
# judgment needs (DESIGN §4.4).
_DESC_CAP = 1500
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def to_plain_text(s: Optional[str], max_len: int = _DESC_CAP) -> str:
    """Normalize an HTML / entity-encoded / plain JD string to capped plain text.

    Handles all four ATS shapes: Greenhouse returns entity-encoded HTML
    (`&lt;p&gt;`), Personio raw HTML, Lever/Ashby already-plain text. unescape →
    strip tags → unescape again (entities revealed after stripping) → collapse ws.
    """
    if not s:
        return ""
    s = html.unescape(s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:max_len]


@dataclass
class Posting:
    job_id: str
    title: str
    location: str
    url: str
    company: str = ""
    description: str = ""  # capped plain-text JD, used by the LLM tier (§4.4); not persisted
    raw: Optional[dict] = field(default=None, repr=False)


class ATSAdapter:
    def fetch(self, slug: str, with_content: bool = False) -> list[Posting]:
        """Return postings for a board. with_content=True populates Posting.description
        where doing so costs an extra/larger request (Greenhouse); adapters whose list
        payload already includes the JD populate it regardless."""
        raise NotImplementedError
