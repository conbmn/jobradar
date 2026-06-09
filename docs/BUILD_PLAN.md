# jobradar — Build Plan

The staged execution plan for building jobradar. This is the **living** document:
tick boxes, add notes, mark milestones done. The durable rationale lives in
`DESIGN.md` (the spec); this file is mutable progress state. Section references like
(§4.3) point into `DESIGN.md`.

## How to use this with Claude Code

- Build **one milestone at a time, in order.** Each is a vertical slice that produces
  something runnable.
- **Stop after each milestone** and verify against its **Acceptance check** before
  starting the next. "Done" means the acceptance check passes, not "code exists."
- Don't let the agent run ahead. The intended loop is: "implement Milestone N" →
  review → confirm acceptance → "implement Milestone N+1."
- When a milestone reveals the spec was wrong, fix `DESIGN.md` first, then continue.

Suggested rule to put in `CLAUDE.md`:
> Implement milestones from `docs/BUILD_PLAN.md` strictly in order, one vertical slice
> at a time. After each milestone, stop and report against its acceptance check; do
> not begin the next milestone until the current one passes. Never commit `.env`,
> real config files, or the SQLite database.

---

## Milestone 0 — Scaffolding

**Goal:** an empty but coherent project skeleton that runs, with config/secrets/state
separation correct from day one (§8).

- [ ] Create the package layout (proposed):
  ```
  jobradar/
  ├── CLAUDE.md
  ├── README.md
  ├── docs/{DESIGN.md, BUILD_PLAN.md}
  ├── requirements.txt            # pin deps + Python version
  ├── .env.example                # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY template (committed); copy to .env.local
  ├── .gitignore                  # .env, .env.local, *.db, config/companies.csv, config/matcher.yml
  ├── config/
  │   ├── companies.example.csv   # name,careers_url,ats,slug
  │   └── matcher.example.yml     # include/exclude terms, locations, weights, threshold, llm toggle
  ├── jobradar/
  │   ├── __init__.py
  │   ├── adapters/{__init__.py, base.py}     # ATSAdapter + Posting (§4.3)
  │   ├── detector.py             # (§4.2) — stub for now
  │   ├── matcher.py              # (§4.4) — stub for now
  │   ├── store.py                # (§5,§7) SQLite
  │   ├── notify/{__init__.py, base.py}        # Notifier interface (§4.7) — stub
  │   ├── render/__init__.py      # CSV/HTML/Notion (§4.6) — stub
  │   ├── pipeline.py             # scrape→diff→match→notify orchestration
  │   └── cli.py                  # entrypoints (§4.9) — stub
  ├── .claude/commands/           # slash commands (§4.9) — added in M9
  └── .github/workflows/          # scheduler (§4.8) — added in M7
  ```
- [ ] `requirements.txt` with pinned deps (start minimal: `requests`, `PyYAML`,
      `python-dotenv`) and a documented Python version.
- [ ] `.gitignore` covers `.env`, the `*.db` state file, and the real (non-example)
      config files.
- [ ] `.env.example` and the two `*.example.*` config files committed; real copies are
      gitignored.
- [ ] Lean `CLAUDE.md` that references `docs/DESIGN.md` + `docs/BUILD_PLAN.md` and
      encodes the workflow + "never commit secrets/state" rules.
- [ ] `store.py` initializes an **empty** SQLite DB with the §5 schema if none exists.

**Acceptance check:** fresh clone → `pip install -r requirements.txt` → running the
entrypoint creates an empty DB and exits cleanly. `git status` shows no `.env`, no
`*.db`, no real config files as tracked.

---

## Milestone 1 — Walking skeleton (Greenhouse → match → store → CSV)

**Goal:** one real end-to-end path. Pull live jobs from a few Greenhouse companies,
score them, store them, and export a ranked CSV you can actually open.

- [ ] `adapters/base.py`: `Posting` shape `{job_id, title, location, url, raw?}` and
      the `ATSAdapter.fetch(slug) -> list[Posting]` interface (§4.3).
- [ ] `adapters/greenhouse.py`: hit `boards-api.greenhouse.io/v1/boards/{slug}/jobs`,
      normalize to `Posting`. `job_id` = Greenhouse's stable ID (§4.3).
- [ ] `matcher.py`: tiers 1–4 (§4.4) — include keywords, exclude keywords, geography,
      scored threshold. Returns `(score, reason)`. Reads terms/weights from
      `matcher.yml`. (LLM tier deferred to M6.)
- [ ] `store.py`: insert-if-new on `(company, job_id)`; always store every posting and
      always persist `match_score`/`match_reason` (§6 invariants).
- [ ] `render/`: CSV export of open roles ranked by score, columns
      company/title/location/score/reason/url.
- [ ] `pipeline.py` + `cli.py`: a `discover` path that scrapes the configured
      companies, matches, stores, and writes the CSV.
- [ ] Populate `config/companies.csv` with 3–5 real Greenhouse companies for testing.

**Acceptance check:** running discover on 3–5 real companies produces a CSV with real,
correctly-ranked roles; spot-check that the top entries genuinely fit and obvious
non-matches (e.g. sales/intern) are excluded or low-scored.

---

## Milestone 2 — Steady-state diff + Telegram alerts

**Goal:** the incremental "something new dropped" path, with the seed rule so the
first run never floods.

- [ ] Implement the §6 lifecycle: per-company seed detection evaluated at run start;
      seed inserts suppress alerts; steady-state above-threshold inserts alert; closed
      roles marked `status='closed'`.
- [ ] `notify/telegram.py`: implement `Notifier.send(...)` via
      `api.telegram.org/bot{token}/sendMessage`, formatted with title/company/location
      + apply link. Token + chat ID from env (§8).
- [ ] `pipeline.py`: a `run` path = scrape → diff → match → alert on new steady-state
      matches only.

**Acceptance check:** first `run` on a fresh DB sends **zero** Telegram messages and
seeds the backlog. Then simulate a new posting (e.g. add a company already past seed,
or inject a fake new `job_id`) and confirm exactly **one** alert fires, with a working
apply link. Re-running without changes fires nothing.

---

## Milestone 3 — More ATS adapters

**Goal:** cover the bulk of a real company list beyond Greenhouse.

- [ ] `adapters/lever.py` — `api.lever.co/v0/postings/{slug}?mode=json`.
- [ ] `adapters/ashby.py` — GraphQL endpoint.
- [ ] `adapters/personio.py` — EMEA-heavy.
- [ ] Add Workday / SmartRecruiters / Recruitee **only if** the real target list needs
      them.
- [ ] Pipeline dispatches to the right adapter by the `ats` field in config.

**Acceptance check:** each adapter returns correctly-normalized postings (stable
`job_id`, sane title/location/url) for at least one real company on that ATS; the
pipeline runs a mixed-ATS company list in a single pass without per-adapter special-casing.

---

## Milestone 4 — ATS detector

**Goal:** turn "name + careers URL" into `ats + slug` automatically so onboarding
isn't manual (§4.2).

- [ ] Find careers page from a homepage when needed (`careers|jobs|join`, common paths).
- [ ] Fingerprint the ATS from HTML/script/iframe/host patterns and extract the slug.
- [ ] Write `ats`/`slug` back into the company config; flag unresolved ones for manual
      fixing.

**Acceptance check:** run the detector over a real list of ~15–20 companies; the large
majority are auto-classified correctly and the unresolved remainder are clearly
flagged rather than silently wrong.

---

## Milestone 5 — Discovery polish

**Goal:** make the backlog genuinely browsable, not just a CSV dump (§4.6).

- [ ] `render/html.py`: sortable HTML table, ranked, with score + reason + apply link.
- [ ] `render/notion.py` (optional but recommended): push the ranked list into a Notion
      database with a status column (applied / interested / pass).
- [ ] Discovery output is re-runnable and reflects current `status='open'` + score.

**Acceptance check:** you can sit down with the rendered output, scan it top-down, and
the ranking + reasons make the top candidates obvious; re-running after a matcher tweak
visibly re-ranks.

---

## Milestone 6 — Optional LLM matching tier

**Goal:** fuzzy judgment on the borderline survivors only, behind a toggle (§4.4 tier 5).

- [ ] Add the `anthropic` dependency and read the API key from env.
- [ ] Apply the LLM pass **only** to candidates that pass tiers 1–4; pass
      title (+ location, + description if fetched) and get back a 0–1 score + one-line
      reason.
- [ ] Gate the whole tier behind a config toggle; the tool must work fully with it off.

**Acceptance check:** with the toggle off, behavior is identical to M5. With it on,
borderline roles get a sensible score + readable reason, and the number of LLM calls
per run equals the (small) count of tier-1–4 survivors, not the full scrape.

---

## Milestone 7 — Scheduling

**Goal:** runs itself twice daily without your machine being awake (§4.8).

- [ ] `.github/workflows/monitor.yml`: cron (twice daily), installs deps, runs `run`.
- [ ] Secrets via GitHub repo secrets (not committed).
- [ ] State persistence between ephemeral runs: commit the DB back, or save/restore it
      as a workflow artifact.

**Acceptance check:** a scheduled (or manually-triggered) Actions run completes, sends
alerts for genuinely new roles, and the persisted state carries over so the next run
doesn't re-alert the same roles.

---

## Milestone 8 — Shareability pass

**Goal:** a technical friend clones and runs cold in ~5 minutes, no code edits (§8).

- [ ] Confirm example configs, `.env.example`, `.gitignore`, pinned deps are all in
      place and correct.
- [ ] `README.md` Setup section: BotFather token + chat ID, copy example configs,
      install, run detect, run.
- [ ] **Cold-clone test:** have a second person (or a clean checkout in a temp dir)
      clone and follow only the README.

**Acceptance check:** the cold clone reaches a personal discovery backlog without
touching code and without inheriting your companies or your "seen" state.

---

## Milestone 9 — Command surface

**Goal:** wrap operations as ergonomic commands so you don't memorize scripts (§4.9).

- [ ] `.claude/commands/`: `setup.md`, `discover.md`, `add-company.md`, `run.md` as
      thin wrappers over the existing pipeline functions (not a second code path).
- [ ] `/setup`: interactive interview that runs the detector and writes
      `companies.csv` + `matcher.yml`.
- [ ] `/add-company`: append one company, detect it, seed it silently (§6).

**Acceptance check:** a fresh user can run `/setup`, answer questions, and end up with
working config + a first discovery run — without hand-editing YAML.

---

## Milestone 11 — Notion as the friendly config + discovery surface

**Goal:** let a friend run the tool without learning the CSV schema. The company list
lives in a Notion database (dropdowns + machine-filled `ats`/`slug`), and matching
roles render back into Notion. Promotes the parking-lot "Notion as primary surface"
item (§4.1, §4.6). CSV stays as the simple offline fallback.

- [x] **A — Pluggable companies source + Notion reader.** Factored into a source
      interface (`load()` + `save(rows)`) in `jobradar/sources/`. Notion source uses
      Notion REST via `requests` (no SDK), selected by `companies_source: notion`;
      token from env (`NOTION_TOKEN`), database id from config. Property map:
      title→name, url→careers_url, select→ats ('auto'→blank), text→slug.
  - *Acceptance:* ✅ `discover` ran off the live Notion DB and produced the same 37
    matching rows as the CSV path.
- [x] **B — Detect writes back to Notion.** `detect` sets a `dirty` flag on resolved
      rows; the Notion source patches `ats`/`slug` back onto those pages only.
  - *Acceptance:* ✅ added Anthropic (ATS=auto, slug blank) → `detect` → row gained
    greenhouse/anthropic in Notion, leaving the 7 already-set rows untouched.
- [x] **C — `setup-notion` provisioning command.** `python -m jobradar setup-notion
      <page-url>` creates both the Companies (with ATS dropdown + example row) and
      Roles databases under a shared page, then writes their ids + `companies_source:
      notion` into matcher.yml (preserving comments). Created via the tool's own
      integration, so no extra share step for the new DBs.
  - *Acceptance:* ✅ ran against a shared page → both DBs created with correct schemas
    (Companies example row readable, Roles Status options New/Interested/Applied/Pass),
    config auto-written; page-id parser unit-tested (URL, UUID, query-string forms).
- [x] **D — Discovery renders back into Notion.** `render/notion.py` upserts matching
      open roles into a Roles DB with a Status column (New / Interested / Applied /
      Pass), ranked by score, keyed on `company::job_id`. Status is set once on insert
      and never overwritten. Wired into `discover` when `notion.roles_database_id` set.
  - *Acceptance:* ✅ `discover` populated 40 ranked roles; a re-run kept it at 40 (no
    dupes) and preserved a manually-set 'Applied' status.

---

## Milestone 10 — Optional: Playwright fallback

**Goal:** handle specific high-value companies on bespoke/JS boards with no clean API.

- [ ] Add Playwright as an **optional** dependency (tool still runs without it).
- [ ] `adapters/playwright_fallback.py` for named companies that justify it.

**Acceptance check:** a previously-unscrapeable target company yields normalized
postings through the fallback, and the tool still installs/runs for someone who never
installs Playwright.

---

## Parking lot (post-PoC, see DESIGN §12)

- [ ] Email notifier backend.
- [x] Notion as the primary human-facing surface with status workflow.
      → promoted to **Milestone 11**.
- [ ] Optional downstream application layer (CV/cover-letter; see DESIGN §13 prior art).
- [ ] Migrate state to Supabase/Postgres + thin web UI (only if multi-device/UI wanted).

---

## Post-build enhancements (operational)

The milestones above are complete and the tool runs twice daily on GitHub Actions.
Enhancements since, all live (details in `DESIGN.md`):

- [x] **Self-healing detect** — `run`/`discover` auto-detect companies added to Notion
      that still lack ats/slug; no manual `detect` needed (§4.2).
- [x] **Detect Status flag** — detector writes `✓ ready / ⚠ no adapter / ✗ …` back to
      the Companies DB so skipped companies are visible, not silent (§4.2).
- [x] **Coverage line** on `run`/`discover` (`N fetched, M skipped …`).
- [x] **Alert count header** — new matches sent as one ranked batch led by a
      "N new matching roles" header (§4.7).
- [x] **First seen** date on Notion roles — sort a view by it to see what's new (§4.6).
- [x] **LLM tier on Haiku**, fed a capped JD excerpt (not title-only) for grounded
      fit reasons (§4.4).
- [x] **Soft/hard personalization split** — `profile` (graded) + `deal_breakers`
      (binary vetoes, capped at 0.15) + `min_fit` (trust dial) (§4.4).
- [x] **`review` command** — CSV of shown-vs-dropped roles with reasons, for filter QA.
- [x] Scheduler at 09:13 / 17:13 CET; cron offset off-the-hour for reliability.
