"""Pipeline orchestration — scrape → match → store → render (see DESIGN §3)."""
from pathlib import Path

import yaml

from jobradar.adapters.greenhouse import GreenhouseAdapter
from jobradar.adapters.lever import LeverAdapter
from jobradar.adapters.ashby import AshbyAdapter
from jobradar.adapters.personio import PersonioAdapter
from jobradar.matcher import match
from jobradar.render import write_csv
from jobradar.render.html import write_html
from jobradar.sources import get_companies_source
from jobradar.store import (
    init_db,
    company_has_rows,
    upsert_posting,
    mark_closed,
    close_unwatched,
    mark_notified,
    open_matching_jobs,
    _DEFAULT_DB,
)

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_COMPANIES_CSV = _CONFIG_DIR / "companies.csv"
_MATCHER_YML = _CONFIG_DIR / "matcher.yml"

_ADAPTERS = {
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "ashby": AshbyAdapter(),
    "personio": PersonioAdapter(),
}


def _load_matcher_config(path: Path = _MATCHER_YML) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _safe_send(send_fn, *args) -> bool:
    """Notifications must never fail the run — by the time we notify, the scrape,
    scoring, and Notion update are already done. Log and carry on if a send fails
    (e.g. a bad Telegram token or a transient API error)."""
    try:
        send_fn(*args)
        return True
    except Exception as exc:
        print(f"  [notify failed — run continues] {exc}")
        return False


def _autodetect_missing(companies, source) -> int:
    """Resolve ats/slug for any row that's missing them, write just those back.

    Makes run/discover self-healing (DESIGN §4.2): a company added to the source —
    e.g. typed into the Notion table — is detected on the next pass without a manual
    `detect`. Already-resolved rows are skipped, so steady-state cost is ~zero; only
    still-unresolved rows are re-probed. Write-back failures are non-fatal: detection
    lives in memory for this run regardless, so fetching proceeds.
    """
    from jobradar.detector import detect, detection_status

    pending = [c for c in companies if not (c.ats.strip() and c.slug.strip())]
    if not pending:
        return 0

    print(f"  auto-detecting {len(pending)} company(s) missing ats/slug…")
    resolved = 0
    for row in pending:
        det = detect(row.name, row.careers_url)
        row.status, row.dirty = detection_status(det), True
        if det.resolved:
            row.ats, row.slug = det.ats, det.slug
            resolved += 1
            print(f"    [{row.name}] {det.ats}/{det.slug}")
        else:
            print(f"    [{row.name}] unresolved ({det.source}) — flagged in source")
    try:
        source.save(companies)  # only the dirty (pending) rows are written
    except Exception as exc:
        print(f"  [detect write-back failed — run continues] {exc}")
    return resolved


def _coverage_line(fetched: int, skipped_missing: int, skipped_no_adapter: int) -> str:
    """One-line scrape coverage so a 'successful' run that fetched almost nothing
    (e.g. companies added in Notion but never run through `detect`) is obvious."""
    parts = [f"{fetched} fetched"]
    if skipped_missing:
        parts.append(f"{skipped_missing} skipped (no ats/slug — run detect)")
    if skipped_no_adapter:
        parts.append(f"{skipped_no_adapter} skipped (unsupported ATS)")
    return "Coverage: " + ", ".join(parts)


def _build_llm_matcher(matcher_config: dict):
    """Construct the optional LLM matcher if llm_tier.enabled, else None (DESIGN §4.4)."""
    llm_cfg = matcher_config.get("llm_tier") or {}
    if not llm_cfg.get("enabled"):
        return None
    from jobradar.matcher_llm import LLMMatcher
    return LLMMatcher(
        model=llm_cfg.get("model", "claude-opus-4-8"),
        profile=llm_cfg.get("profile", ""),
        deal_breakers=llm_cfg.get("deal_breakers", ""),
    )


def _show_threshold(matcher_config: dict, llm_matcher, kw_threshold: float) -> float:
    """Floor for showing/alerting: the LLM fit cutoff when the tier is on (0-1 scale),
    else the keyword alert threshold. Keeps one coherent scale per mode."""
    if llm_matcher is None:
        return kw_threshold
    return float((matcher_config.get("llm_tier") or {}).get("min_fit", 0.6))


def _score_posting(p, matcher_config: dict, kw_threshold: float, llm_matcher) -> tuple[float, str]:
    """Tier 1-4 keyword score, then optional LLM judgment for survivors only (§4.4).

    With the LLM tier on, the keyword score is just the cheap candidate gate: postings
    below kw_threshold aren't worth a call (score 0). Survivors get the LLM's 0-1 fit as
    their score, which is then gated by min_fit — so the model alone decides fit and
    rank, on a clean 0-1 scale, while keywords keep the call volume small.
    """
    kw_score, reason = match(p.title, p.location, matcher_config)
    if llm_matcher is None:
        return kw_score, reason
    if kw_score < kw_threshold:
        return 0.0, reason  # not a keyword survivor — not worth an LLM call
    fit, llm_reason = llm_matcher.score(p.title, p.location, getattr(p, "description", ""))
    return fit, f"fit {fit:.2f} — {llm_reason} (kw {kw_score:g})"


def _set_yaml_value(path: Path, key: str, value: str, quote: bool = True) -> int:
    """Replace `key: <anything>` on its line, preserving comments/formatting.

    Matches the key at any indentation, so it updates both the top-level
    `companies_source` and the nested `*_database_id` keys. Returns match count.
    """
    import re
    text = path.read_text(encoding="utf-8")
    val = f'"{value}"' if quote else value
    pattern = re.compile(rf'^(\s*{re.escape(key)}:\s*).*$', re.MULTILINE)
    new_text, n = pattern.subn(lambda m: m.group(1) + val, text)
    if n:
        path.write_text(new_text, encoding="utf-8")
    return n


def _set_yaml_block(path: Path, key: str, text: str, wrap: int = 78) -> bool:
    """Replace a folded block-scalar value (`key: >`) with new wrapped text, leaving
    the rest of the file (comments, other keys) untouched. Returns True if found."""
    import re
    import textwrap
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    i = 0
    found = False
    while i < len(lines):
        m = re.match(rf"^(\s*){re.escape(key)}:\s*", lines[i])
        if m and not found:
            found = True
            indent = len(m.group(1))
            out.append(f"{' ' * indent}{key}: >")
            for wrapped in textwrap.wrap(" ".join(text.split()), wrap):
                out.append(f"{' ' * (indent + 2)}{wrapped}")
            i += 1
            # Drop the old block body: deeper-indented or blank lines.
            while i < len(lines) and (
                lines[i].strip() == "" or (len(lines[i]) - len(lines[i].lstrip())) > indent
            ):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if found:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return found


def generate_profile(cv_path: str, matcher_path: Path = _MATCHER_YML) -> None:
    """Distill a CV into the matcher `profile` and write it into matcher.yml (§4.9).

    The CV is only an input — it stays local, never committed and never sent to the
    scheduler; only the distilled paragraph lands in config.
    """
    import os

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set in .env.local — add it first.")
        return

    from jobradar.matcher_llm import read_cv_text, distill_profile

    try:
        cv_text = read_cv_text(cv_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(exc)
        return
    if not cv_text.strip():
        print(f"Couldn't extract any text from {cv_path} (scanned/image PDF?).")
        return

    matcher_config = _load_matcher_config(matcher_path)
    model = (matcher_config.get("llm_tier") or {}).get("model") or "claude-haiku-4-5"
    print(f"Distilling profile from {cv_path} via {model}…")
    profile = distill_profile(cv_text, model=model)

    print("\n--- generated profile ---\n" + profile + "\n-------------------------")
    if _set_yaml_block(matcher_path, "profile", profile):
        print(f"\nWrote it into {matcher_path}. Review/edit there, then re-run discover.")
    else:
        print(f"\nCouldn't find a `profile:` block in {matcher_path} — add the profile by hand.")


def setup_notion(parent: str, matcher_path: Path = _MATCHER_YML) -> None:
    """Create the Companies + Roles databases and wire their ids into matcher.yml.

    The friendly onboarding (DESIGN §4.9): share one page with the integration,
    run this, then fill the Companies table — no schema to hand-build.
    """
    import os
    import requests

    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        print("NOTION_TOKEN is not set in .env.local. Create an integration at "
              "https://www.notion.so/my-integrations and add the token first.")
        return

    from jobradar.sources.notion_source import (
        create_companies_database, seed_example_company, extract_page_id,
    )
    from jobradar.render.notion import create_roles_database

    page_id = extract_page_id(parent)
    print(f"Creating databases under page {page_id}…")
    try:
        companies_id = create_companies_database(token, page_id)
        seed_example_company(token, companies_id)
        roles_id = create_roles_database(token, page_id)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            print("Page not found, or not shared with the integration. In Notion open "
                  "the page → ⋯ → Connections → add your integration, then re-run.")
            return
        raise

    _set_yaml_value(matcher_path, "companies_source", "notion", quote=False)
    wrote_c = _set_yaml_value(matcher_path, "companies_database_id", companies_id)
    wrote_r = _set_yaml_value(matcher_path, "roles_database_id", roles_id)

    print("\nCreated:")
    print(f"  Companies DB: {companies_id}")
    print(f"  Roles DB:     {roles_id}")
    if wrote_c and wrote_r:
        print(f"Wrote ids + set companies_source: notion in {matcher_path}.")
    else:
        print(f"Add these ids under `notion:` in {matcher_path} (keys not found to auto-write).")
    print("Next: fill the Companies table, then `python -m jobradar detect && "
          "python -m jobradar discover`.")


def detect_companies(
    companies_path: Path = _COMPANIES_CSV,
    matcher_path: Path = _MATCHER_YML,
    force: bool = False,
) -> None:
    """Fill in blank ats/slug for each company via the detector (DESIGN §4.2).

    Reads/writes through the configured companies source (CSV or Notion). Only rows
    missing ats or slug are detected unless force=True. Resolved rows are written
    back to the source; unresolved rows are left blank and reported for hand-fixing.
    """
    from jobradar.detector import detect, detection_status, status_for_ats

    matcher_config = _load_matcher_config(matcher_path)
    source = get_companies_source(matcher_config, companies_path)
    companies = source.load()
    if not companies:
        print("No companies to detect.")
        return

    resolved, unresolved, skipped = [], [], []

    for row in companies:
        if row.ats and row.slug and not force:
            # Already resolved — don't re-fetch, but stamp status so the column is
            # complete (a row resolved to an unsupported ATS still won't be fetched).
            row.status, row.dirty = status_for_ats(row.ats), True
            skipped.append(row.name)
            continue

        print(f"  [{row.name}] detecting from {row.careers_url}…", end=" ", flush=True)
        det = detect(row.name, row.careers_url)
        row.status, row.dirty = detection_status(det), True
        if det.resolved:
            row.ats, row.slug = det.ats, det.slug
            tag = {"validated": "✓ validated",
                   "guessed": "✓ guessed from name (verify)",
                   "detected_no_adapter": "detected (no adapter yet)",
                   "detected_unvalidated": "detected (unvalidated)"}.get(det.source, det.source)
            print(f"{det.ats}/{det.slug}  [{tag}]")
            resolved.append(row.name)
        else:
            print(f"UNRESOLVED ({det.source})")
            unresolved.append(row.name)

    source.save(companies)

    print(
        f"\nDetect complete: {len(resolved)} resolved, "
        f"{len(unresolved)} unresolved, {len(skipped)} already set."
    )
    if unresolved:
        print("Unresolved (fix ats/slug by hand): " + ", ".join(unresolved))


def discover(
    db_path: Path = _DEFAULT_DB,
    companies_path: Path = _COMPANIES_CSV,
    matcher_path: Path = _MATCHER_YML,
    output_path=None,
) -> Path:
    """Scrape all configured companies, match, store, and write a ranked CSV.

    Returns the path of the written CSV.
    """
    if output_path is None:
        output_path = Path("discovery.csv")

    conn = init_db(db_path)
    matcher_config = _load_matcher_config(matcher_path)
    source = get_companies_source(matcher_config, companies_path)
    companies = source.load()
    _autodetect_missing(companies, source)
    kw_threshold = float(
        matcher_config.get("scoring", {}).get("alert_threshold", 0.0)
    )
    llm_matcher = _build_llm_matcher(matcher_config)
    show_threshold = _show_threshold(matcher_config, llm_matcher, kw_threshold)

    close_unwatched(conn, {c.name for c in companies})

    total_new = 0
    total_closed = 0
    fetched = skipped_missing = skipped_no_adapter = 0

    for company in companies:
        name = company.name
        ats = company.ats.strip().lower()
        slug = company.slug.strip()

        if not ats or not slug:
            print(f"  [{name}] skipping — missing ats or slug")
            skipped_missing += 1
            continue

        adapter = _ADAPTERS.get(ats)
        if adapter is None:
            print(f"  [{name}] skipping — no adapter for '{ats}'")
            skipped_no_adapter += 1
            continue

        fetched += 1
        print(f"  [{name}] fetching from {ats}/{slug}…", end=" ", flush=True)
        try:
            postings = adapter.fetch(slug, with_content=llm_matcher is not None)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue
        print(f"{len(postings)} postings")

        seen_ids: set[str] = set()
        for p in postings:
            p.company = name
            seen_ids.add(p.job_id)
            score, reason = _score_posting(p, matcher_config, kw_threshold, llm_matcher)
            is_new = upsert_posting(
                conn, name, p.job_id, p.title, p.location, p.url, score, reason
            )
            if is_new:
                total_new += 1

        closed = mark_closed(conn, name, seen_ids)
        total_closed += closed

    rows = open_matching_jobs(conn, threshold=show_threshold)

    # Render targets are config-driven (DESIGN §4.6): `outputs:` in matcher.yml, a
    # subset of csv/html/notion. Unset → all three (notion only if a roles DB is set),
    # preserving prior behaviour. A Notion-only setup sets `outputs: [notion]` and gets
    # no local files.
    targets = {o.strip().lower() for o in (matcher_config.get("outputs")
                                           or ["csv", "html", "notion"])}
    written = []
    if "csv" in targets:
        write_csv(rows, output_path)
        written.append(str(output_path))
    if "html" in targets:
        html_path = output_path.with_suffix(".html")
        write_html(rows, html_path)
        written.append(str(html_path))
    roles_db = ((matcher_config.get("notion") or {}).get("roles_database_id") or "").strip()
    if "notion" in targets and roles_db:
        from jobradar.render.notion import render_notion
        n = render_notion(rows, roles_db)
        written.append(f"{n} roles → Notion")

    if llm_matcher is not None:
        print(f"  LLM tier ({llm_matcher.model}): {llm_matcher.calls} calls, "
              f"{llm_matcher.input_tokens} in / {llm_matcher.output_tokens} out "
              f"+ {llm_matcher.cache_read_tokens} cached tokens")
    print(f"\n{_coverage_line(fetched, skipped_missing, skipped_no_adapter)}")
    print(
        f"Discover complete: {total_new} new, {total_closed} closed, "
        f"{len(rows)} matching rows → {', '.join(written) or '(no outputs configured)'}"
    )
    return output_path


def review_export(
    db_path: Path = _DEFAULT_DB,
    matcher_path: Path = _MATCHER_YML,
    output_path=None,
) -> Path:
    """Write a CSV of every keyword-relevant open role with score, fit reason, and a
    shown/dropped flag — for eyeballing filter quality (DESIGN §4.4). Read-only; no
    scraping or LLM calls. Reflects the DB as of the last run, so run/discover first.
    """
    import csv as _csv

    if output_path is None:
        output_path = Path("review.csv")

    conn = init_db(db_path)
    matcher_config = _load_matcher_config(matcher_path)
    # Same effective cutoff run/discover use, computed without building the LLM client
    # (no API key needed just to export).
    llm_cfg = matcher_config.get("llm_tier") or {}
    if llm_cfg.get("enabled"):
        threshold = float(llm_cfg.get("min_fit", 0.6))
    else:
        threshold = float(matcher_config.get("scoring", {}).get("alert_threshold", 3.0))

    # Skip pure 'no_match' titles (no include term fired) — thousands of irrelevant
    # rows. Keep everything the include tier touched: shown, low-fit, location-dropped,
    # excluded. Shown roles first, then by score.
    rows = conn.execute(
        """
        SELECT company, title, location, match_score, match_reason, url, first_seen
        FROM jobs
        WHERE status = 'open' AND match_reason != 'no_match'
        ORDER BY (match_score >= ?) DESC, match_score DESC
        """,
        (threshold,),
    ).fetchall()

    shown = dropped = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["shown", "score", "company", "title", "location", "reason",
                    "first_seen", "url"])
        for r in rows:
            is_shown = (r["match_score"] or 0.0) >= threshold
            shown += is_shown
            dropped += not is_shown
            w.writerow([
                "yes" if is_shown else "no",
                round(float(r["match_score"] or 0.0), 2),
                r["company"], r["title"], r["location"] or "",
                r["match_reason"] or "", r["first_seen"] or "", r["url"] or "",
            ])

    print(f"Review: {shown} shown, {dropped} dropped (threshold {threshold:g}) "
          f"→ {output_path}  (open in Excel/Numbers)")
    return output_path


def run(
    db_path: Path = _DEFAULT_DB,
    companies_path: Path = _COMPANIES_CSV,
    matcher_path: Path = _MATCHER_YML,
) -> None:
    """Scrape → diff → match → alert on new steady-state matches only (see DESIGN §6)."""
    from jobradar.notify.telegram import TelegramNotifier
    notifier = TelegramNotifier()

    conn = init_db(db_path)
    matcher_config = _load_matcher_config(matcher_path)
    source = get_companies_source(matcher_config, companies_path)
    companies = source.load()
    _autodetect_missing(companies, source)
    kw_threshold = float(matcher_config.get("scoring", {}).get("alert_threshold", 3.0))
    llm_matcher = _build_llm_matcher(matcher_config)
    show_threshold = _show_threshold(matcher_config, llm_matcher, kw_threshold)

    close_unwatched(conn, {c.name for c in companies})

    total_new = total_alerted = total_closed = seeded = 0
    fetched = skipped_missing = skipped_no_adapter = 0
    pending_alerts: list = []  # (posting, score, reason) — flushed after the scrape so a
    #                            count header can lead, then the cards, ranked by score

    for company in companies:
        name = company.name
        ats = company.ats.strip().lower()
        slug = company.slug.strip()

        if not ats or not slug:
            print(f"  [{name}] skipping — missing ats or slug")
            skipped_missing += 1
            continue

        adapter = _ADAPTERS.get(ats)
        if adapter is None:
            print(f"  [{name}] skipping — no adapter for '{ats}'")
            skipped_no_adapter += 1
            continue

        fetched += 1
        # Evaluate seed status BEFORE any inserts (DESIGN §6)
        is_seed = not company_has_rows(conn, name)
        if is_seed:
            seeded += 1

        print(f"  [{name}] fetching ({'seed' if is_seed else 'steady-state'})…", end=" ", flush=True)
        try:
            postings = adapter.fetch(slug, with_content=llm_matcher is not None)
        except Exception as exc:
            print(f"ERROR: {exc}")
            continue
        print(f"{len(postings)} postings")

        seen_ids: set[str] = set()
        for p in postings:
            p.company = name
            seen_ids.add(p.job_id)
            score, reason = _score_posting(p, matcher_config, kw_threshold, llm_matcher)
            is_new = upsert_posting(conn, name, p.job_id, p.title, p.location, p.url, score, reason)

            if is_new:
                total_new += 1
                if is_seed:
                    mark_notified(conn, name, p.job_id)
                elif score >= show_threshold:
                    # Defer the send so all alerts can go out as one ranked batch
                    # behind a count header (below). Mark notified now — intent to
                    # alert is committed, matching the prior inline behaviour.
                    pending_alerts.append((p, score, reason))
                    mark_notified(conn, name, p.job_id)

        closed = mark_closed(conn, name, seen_ids)
        total_closed += closed

    # Flush deferred alerts: a "N new roles" header first, then the cards, high score
    # first — so the Telegram thread leads with the count the user asked for (§4.7).
    if pending_alerts:
        pending_alerts.sort(key=lambda t: t[1], reverse=True)
        n = len(pending_alerts)
        _safe_send(notifier.send_text, f"🎯 *jobradar* — {n} new matching role{'s' if n != 1 else ''}")
        for p, score, reason in pending_alerts:
            if _safe_send(notifier.send, p, score, reason):
                total_alerted += 1

    open_rows = open_matching_jobs(conn, threshold=show_threshold)

    # Keep the Notion roles table fresh on every scheduled run, not just discover (§4.6).
    notion_note = ""
    roles_db = ((matcher_config.get("notion") or {}).get("roles_database_id") or "").strip()
    if roles_db:
        from jobradar.render.notion import render_notion
        n = render_notion(open_rows, roles_db)
        notion_note = f", {n} roles → Notion"

    # Heartbeat: confirm the run happened even when nothing new alerted, so silence
    # is never ambiguous (DESIGN §4.7). Default on; disable via notify.heartbeat: false.
    heartbeat = (matcher_config.get("notify") or {}).get("heartbeat", True)
    if heartbeat and total_alerted == 0:
        link = f"\n[Open backlog](https://www.notion.so/{roles_db.replace('-', '')})" if roles_db else ""
        if seeded:
            body = (f"🔍 *jobradar* initialized — seeded {seeded} companies, "
                    f"{len(open_rows)} matching roles in your backlog. "
                    f"New postings from now on will ping you.")
        else:
            body = (f"🔍 *jobradar* ran — no new matching roles. "
                    f"{len(open_rows)} open matches in your backlog.")
        _safe_send(notifier.send_text, body + link)

    if llm_matcher is not None:
        print(f"  LLM tier ({llm_matcher.model}): {llm_matcher.calls} calls, "
              f"{llm_matcher.input_tokens} in / {llm_matcher.output_tokens} out "
              f"+ {llm_matcher.cache_read_tokens} cached tokens")
    print(f"\n{_coverage_line(fetched, skipped_missing, skipped_no_adapter)}")
    print(f"Run complete: {total_new} new, {total_closed} closed, "
          f"{total_alerted} alerted{notion_note}")
