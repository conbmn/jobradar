"""Matcher: tiers 1-4 scoring (see DESIGN §4.4). LLM tier deferred to M6."""
import re


def match(title: str, location: str, config: dict) -> tuple[float, str]:
    """Score a posting against the loaded matcher config.

    Returns (score, reason). A score of 0.0 with reason 'excluded' or 'no_match'
    means the posting should be omitted from the discovery list.
    """
    title_lower = title.lower()
    location_lower = location.lower()

    # Tier 2: exclude check (applied first — kill noise immediately)
    for term in config.get("exclude_terms", []):
        if re.search(term, title_lower, re.IGNORECASE):
            return 0.0, "excluded"

    # Tier 1: include check
    scoring = config.get("scoring", {})
    include_points = float(scoring.get("include_match_points", 2))
    matched_includes = []
    for term in config.get("include_terms", []):
        if re.search(term, title_lower, re.IGNORECASE):
            matched_includes.append(term)

    if not matched_includes:
        return 0.0, "no_match"

    score = len(matched_includes) * include_points

    # Seniority bonus
    seniority_bonus = float(scoring.get("seniority_bonus", 1))
    seniority_hit = False
    for term in config.get("seniority_terms", []):
        if re.search(term, title_lower, re.IGNORECASE):
            seniority_hit = True
            break
    if seniority_hit:
        score += seniority_bonus

    # Tier 3: geography — HARD filter (DESIGN §4.4). A posting whose specified
    # location isn't in the accepted set is dropped, not merely unscored — otherwise
    # a great-title role in Chile/India/etc. still surfaces. Blank/unknown locations
    # are kept (benefit of the doubt) but earn no bonus.
    locations = config.get("locations", [])
    has_location = bool(location_lower.strip())
    location_hit = any(loc.lower() in location_lower for loc in locations)
    if locations and has_location and not location_hit:
        return 0.0, f"location not accepted ({location})"

    location_bonus = float(scoring.get("location_match_bonus", 1))
    if location_hit:
        score += location_bonus

    # Build a human-readable reason
    parts = [f"matched: {', '.join(matched_includes)}"]
    if seniority_hit:
        parts.append("seniority bonus")
    if location_hit:
        parts.append(f"location ok ({location})")
    elif not has_location:
        parts.append("location unspecified")

    return score, "; ".join(parts)
