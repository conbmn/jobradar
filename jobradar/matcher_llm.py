"""Optional LLM matching tier (DESIGN §4.4 tier 5).

Applied ONLY to candidates that survive tiers 1-4 (a small set). Passes the
posting title + location + a short JD excerpt and a user profile to Claude,
returns a 0-1 fit score and a one-line reason. Gated behind the llm_tier config
toggle; the tool works fully without it.
"""
from pathlib import Path

from anthropic import Anthropic
from pydantic import BaseModel, Field

_DEFAULT_MODEL = "claude-haiku-4-5"  # cheap default; this is a simple title-fit classification


def read_cv_text(path: str) -> str:
    """Extract plain text from a CV file (.pdf via pypdf, else read as text)."""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"CV not found: {p}")
    if p.suffix.lower() == ".pdf":
        try:
            import pypdf
        except ImportError as exc:
            raise RuntimeError(
                "Reading a PDF CV needs pypdf — install with: pip install -e \".[llm]\""
            ) from exc
        reader = pypdf.PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return p.read_text(encoding="utf-8", errors="ignore")


def distill_profile(cv_text: str, model: str = _DEFAULT_MODEL) -> str:
    """Turn a CV into a tight matching profile (background + strong/weak fit)."""
    instructions = (
        "From the CV below, write a concise candidate profile used to match job "
        "postings by title. ~110 words, plain prose — no bullets, no headings, no "
        "preamble. Cover: background and domains, seniority level, the kinds of roles "
        "that are a strong fit, and the kinds that are a weak or non-fit. Be specific "
        "and decisive. Output only the profile text.\n\nCV:\n"
    )
    resp = Anthropic().messages.create(
        model=model or _DEFAULT_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": instructions + cv_text}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

_INSTRUCTIONS = (
    "You screen job postings for a specific person. Given their profile and a "
    "single posting (title, location, and a short description excerpt when "
    "available), judge whether it is a genuine fit.\n"
    "Return a score from 0.0 (clearly not a fit) to 1.0 (excellent fit) and a "
    "concise one-line reason (max ~15 words). Judge on role substance and "
    "seniority from the description, not just title keyword overlap. When a "
    "description is present, ground your reason in something specific from it.\n\n"
    "Candidate profile:\n"
)

_DEAL_BREAKERS_PREFIX = (
    "\n\nHard requirements (deal-breakers). If the posting clearly requires something "
    "this person lacks or has ruled out, it is NOT a fit no matter how well it "
    "otherwise matches: cap the score at 0.15 and name the violated requirement in "
    "your reason. Only apply a deal-breaker when the posting clearly triggers it; if "
    "unsure, judge on fit as normal. The deal-breakers:\n"
)


class FitJudgment(BaseModel):
    score: float = Field(description="Fit score from 0.0 (no fit) to 1.0 (excellent fit)")
    reason: str = Field(description="One-line justification, ~15 words max")


class LLMMatcher:
    def __init__(self, model: str = _DEFAULT_MODEL, profile: str = "", deal_breakers: str = ""):
        self.client = Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model or _DEFAULT_MODEL
        self.profile = profile.strip()
        self.deal_breakers = deal_breakers.strip()
        self.calls = 0  # number of LLM calls made, for verification/telemetry
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0

    def _system(self) -> list[dict]:
        # Stable across every candidate in a run → cache the prefix.
        text = _INSTRUCTIONS + (self.profile or "(no profile provided)")
        if self.deal_breakers:
            text += _DEAL_BREAKERS_PREFIX + self.deal_breakers
        return [{
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral"},
        }]

    def score(self, title: str, location: str, description: str = "") -> tuple[float, str]:
        """Return (llm_score 0-1, reason) for one posting."""
        content = f"Title: {title}\nLocation: {location or 'unspecified'}"
        if description:
            content += f"\n\nDescription:\n{description}"
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=1024,
            thinking={"type": "disabled"},  # simple classification — no thinking needed
            system=self._system(),
            messages=[{"role": "user", "content": content}],
            output_format=FitJudgment,
        )
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u is not None:
            self.input_tokens += getattr(u, "input_tokens", 0) or 0
            self.output_tokens += getattr(u, "output_tokens", 0) or 0
            self.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        j = resp.parsed_output
        return max(0.0, min(1.0, float(j.score))), j.reason.strip()
