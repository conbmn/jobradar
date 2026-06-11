# jobradar — Design Document

> A personal job-posting monitor: watches a roster of companies' careers pages and
> tells you which matching roles are newly open. See `BUILD_PLAN.md` for the staged
> execution plan.

## 1. Purpose

A personal, self-hosted tool that monitors the careers pages of a curated list of
companies, filters new job postings by role and geography, and delivers them in two
modes:

1. **Discovery** — a one-time (and re-runnable) ranked, browsable list of every
   currently-open role that matches the user's criteria, across the whole company
   list. This is the primary day-one value: the list LinkedIn never gave us.
2. **Alerting** — low-latency incremental notifications (Telegram) when a *new*
   matching role appears, checked on a schedule (e.g. twice daily).

The tool exists because aggregators (LinkedIn) miss or bury relevant postings, and
checking each company's site by hand does not scale.

## 2. Goals and non-goals

### Goals
- Works reliably for a single technical user, run from their own machine or a free
  cloud scheduler.
- **Shareable to technical friends** via `git clone` + a ~5-minute configuration
  step, with **no code changes** required. (Primary second user: Fenja, technical,
  same M-series Mac.)
- Config-driven: companies, role keywords, geography, and scoring are all editable
  configuration, never hardcoded logic.
- Low maintenance in steady state; adding a company is a one-line config change.
- Polite scraping: caching, modest request rate, honor robots.txt.

### Non-goals (explicitly out of scope for the PoC)
- A hosted service, web UI, user accounts, or anything for *non-technical* users.
  That is a different, much larger project.
- Multi-device shared state or concurrent multi-user access (the reason we are
  **not** using Supabase/Postgres yet — see §7).
- Bulk-scraping full job descriptions for the keyword tiers (those use title +
  location only — see §5). The optional LLM tier does fetch a capped JD excerpt, but
  only for the handful of keyword survivors, not the whole board (see §4.4).

## 3. High-level architecture

A scheduled pipeline of four stages over a shared state store:

```
┌───────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐
│ Scheduler │──▶│ Scrapers │──▶│  Differ │──▶│ Notifier │
│ (GH       │   │ per-ATS  │   │ (vs     │   │ (Telegram│
│  Actions) │   │ adapters │   │  state) │   │  + render)│
└───────────┘   └────┬─────┘   └────┬────┘   └────┬─────┘
                     │              │             │
                     ▼              ▼             ▼
              ┌────────────────────────────────────────┐
              │  SQLite state store (single file)       │
              │  jobs(company, job_id) → metadata       │
              └────────────────────────────────────────┘
```

Each run: scrape all companies → produce the current set of open roles → diff against
SQLite → record new/closed roles → notify on genuinely new matches (subject to the
seed rule in §6) → optionally render the discovery view.

## 4. Components

### 4.1 Company configuration
The company list is a list of rows, each:

```
name, careers_url, ats, slug
```

- `ats` and `slug` may be left blank and filled by the detector (§4.2) and written
  back to the source. The human only ever supplies `name` + `careers_url`.
- Example: `Helsing, https://helsing.ai/jobs, greenhouse, helsing`

**The list comes from a pluggable source, selected by config (`companies_source`);
the rest of the pipeline is agnostic to which.** A source exposes two operations:
`load()` (return the rows) and `update(row, ats, slug)` (persist detector output).

- **CSV** (`companies_source: csv`, the default/fallback): a committed
  `companies.example.csv` template plus the user's own untracked copy (see §8).
  Simple, offline, no account — but a friend must understand the column schema.
- **Notion** (`companies_source: notion`, the recommended human surface): the tool
  reads companies from a Notion database and writes detected `ats`/`slug` back into
  the rows. This removes the schema burden — an `ATS` column is a dropdown
  (auto/greenhouse/lever/ashby/personio), `slug` is machine-filled, and a friend
  only types Company + Careers URL. A `setup-notion` command *creates* the database
  (correct columns, example row) so nobody hand-builds a schema; the friend shares a
  page with their integration and runs one command. The Notion API is plain REST,
  accessed via `requests` (no SDK dependency). Token is a per-user secret (§8).
  Notion is also the discovery render target (§4.6), so config-in and roles-out live
  in one workspace.

A row also carries a machine-filled **`status`** column (`Detect Status` in Notion),
written back by `detect` (§4.2). It is the visible signal for the failure mode where a
company is added to the source but never resolved to a fetchable ATS — those rows are
silently skipped by `run`/`discover`, so without a status they look healthy. Sources
that can't render it (CSV) carry it as a plain column. The Notion source adds the
`Detect Status` select to a pre-existing database on first write, so older databases
upgrade in place.

### 4.2 ATS detector (automatic + on-demand)
Most companies do not build their own job boards — they use a small number of
Applicant Tracking Systems (ATS), each with a predictable structure. The detector
takes `name + careers_url` and tries to classify the ATS and extract the slug:

1. If given a homepage rather than a careers page, look for `careers|jobs|join`
   links or try common paths (`/careers`, `/jobs`).
2. Fetch the careers page; scan HTML, `<script>` sources, embedded iframes, and
   (where needed) network calls for known ATS fingerprints:
   - Greenhouse iframe / `boards.greenhouse.io`
   - `jobs.lever.co/{slug}`
   - `{slug}.ashbyhq.com`
   - Personio widget (common in EMEA / German market)
   - Workday tenant URLs
   - SmartRecruiters, Recruitee
3. Emit `ats` + `slug` back into the company config.

If static fingerprinting fails (common for JS-rendered SPA careers pages), the
detector falls back to guessing a slug from the name/domain and probing the
adapter-backed ATS APIs directly. A direct ATS board URL (e.g.
`jobs.ashbyhq.com/{slug}`) therefore resolves most reliably, since the slug is in the
URL itself; a generic `company.com/careers` works only when the page redirects to,
embeds, or name-matches its ATS.

Each row's outcome is written back as a **`status`** (§4.1):
- `✓ ready` — resolved to an ATS we have an adapter for (will be fetched)
- `⚠ no adapter` — a real ATS was detected but isn't fetched yet (e.g. Workday)
- `✗ not detected` — no fingerprint and the slug-guess failed → fix the URL or set
  `ats`/`slug` by hand
- `✗ fetch failed` — the careers URL never loaded

The detector auto-classifies the large majority of a typical list. Stragglers
(bespoke boards, awkward Workday tenants) surface via their status and are hand-fixed
once. This is what makes the tool shareable: a friend pastes names + URLs and runs
detect, rather than understanding any ATS internals.

**Self-healing:** `run` and `discover` auto-detect any row still missing `ats`/`slug`
at the start of the pass, write just those rows back, then proceed. This is what makes
the Notion surface (§4.1) actually work unattended — a company typed into the table is
resolved and fetched on the *next scheduled run*, with no manual `detect`. Already-
resolved rows are never re-probed, so steady-state cost is ~zero; only rows that stay
unresolved (flagged `✗` in their status) are retried each pass, which also means
fixing a bad Careers URL is picked up automatically next run. The standalone `detect`
command remains for eager/forced (`--force`) re-classification of the whole list.

### 4.3 ATS adapters
**One adapter per ATS, not per company.** Each adapter takes a slug and returns a
normalized list of postings. Normalized posting schema:

```
{ job_id, title, location, url, raw (optional) }
```

`job_id` MUST be the ATS's own stable identifier — never derived from the title
(titles get edited; we don't want re-alerts).

Adapters to implement, in priority order:
1. **Greenhouse** — public JSON API:
   `boards-api.greenhouse.io/v1/boards/{slug}/jobs`
2. **Lever** — `api.lever.co/v0/postings/{slug}?mode=json`
3. **Ashby** — GraphQL endpoint (common with startups)
4. **Personio** — EMEA-heavy, queryable endpoint
5. **Workday** — consistent JSON POST pattern, uglier
6. **SmartRecruiters / Recruitee** — queryable endpoints

**Playwright fallback adapter** for JS-rendered pages with no clean API. Heavier and
more fragile — use only for companies that matter enough to justify it. Keep it
optional so the tool runs without a browser dependency for the common case.

Adapters share a common interface so the pipeline is agnostic to which one ran:
```
class ATSAdapter:
    def fetch(self, slug: str) -> list[Posting]: ...
```

### 4.4 Matcher (title + location only)
Tiered, cheap-to-expensive. Each tier handles only what the previous could not.
**All thresholds and term lists are config, not code.**

1. **Include keywords** (regex/substring on title) — curated vocabulary that maps to
   the user, e.g. `operations research`, `\bOR\b`, `optimization`, `decision scien`,
   `data scien`, `forward deployed`, `quant`, plus seniority markers
   `staff|senior|principal|lead|founding`.
2. **Exclude keywords** — kill predictable false positives
   (`intern`, `sales`, `marketing`, `recruiter`, …). Exclusions do a lot of the
   noise reduction.
3. **Geography filter** — location must be in the user's set
   (e.g. `Amsterdam, Berlin, Zurich, Remote EU`). Many ATS APIs also support
   server-side location filtering — use it where available to fetch less.
4. **Scored threshold** — points for title hits, seniority, location; alert/show
   above a configurable cutoff. Gives a tunable knob and lets the discovery list be
   *ranked*, not just filtered.
5. **Optional LLM tier** — applied ONLY to candidates that survive 1–4 (a tiny set),
   passing title (+ location, + description if fetched) to a model:
   "Given this profile, is this a genuine fit? Score 0–1 + one-line reason."
   Cheap because the input set is small; gives fuzzy intelligence + an explanation.
   MUST be a config toggle; the tool works fully without it.

Rationale for title+location only at tiers 1–4: full descriptions are where ATS APIs
get inconsistent and where keyword matching gets noisy (every JD says "Python"/"data").
Title + location is the high-signal, reliably available pair for the cheap tiers.

The LLM tier, by contrast, *does* get a short JD excerpt (capped ~1500 chars of plain
text) so its judgment is grounded in role substance, not just the title — this is what
makes its reasons specific rather than generic. Descriptions are fetched only when the
LLM tier is enabled (`adapter.fetch(slug, with_content=...)`), so the cheap path pays
nothing: Greenhouse adds `?content=true`, Lever/Ashby already include plain-text fields,
Personio pulls `jobDescriptions`. A shared `to_plain_text` normalizes all four shapes.

The LLM tier takes two prose personalization fields, a deliberate **soft/hard split**:
- **`profile`** — who the candidate is + soft preferences. Produces the graded 0–1 fit.
  CV-distillable (`jobradar profile`). This is where nuanced "leans X but could work"
  judgments live.
- **`deal_breakers`** — hard, binary vetoes. Any clear hit caps the score at 0.15 (i.e.
  dropped), no matter how well the role otherwise fits. Keep them binary: a language the
  candidate lacks, missing visa/clearance, on-site-only outside accepted geos, a
  seniority floor. General across users — the language case (`"<lang> speaking"` roles)
  is just the canonical example.

Why split them: a single prose blob makes *everything* soft, so the model treats an
absolute no (can't speak the required language) as a minor deduction and it squeaks past
`min_fit`. Deal-breakers fire regardless of `min_fit`, so soft matching can stay
inclusive while hard no's are reliably removed. Don't push nuanced calls (e.g. pure-SWE
vs forward-deployed) into deal_breakers — leave those to the soft `profile`, which the
model already grades well from the description.

Matcher config surface (all editable): include terms, exclude terms, location set,
score weights, threshold, LLM toggle, profile, deal_breakers, min_fit.

### 4.5 State store
SQLite — see §7 for why. Acts as both the source of truth and the dedup engine.

### 4.6 Discovery / backlog renderer
A re-runnable view, separate from the alert stream, that answers "what is open right
now that fits me?" Queries the store for open + matching roles, **ranked by score**,
showing score + reason + title + location + company + apply link. Render targets are
config-driven via `outputs:` in matcher.yml — any subset of `csv` / `html` / `notion`
(omit the key for all three; a Notion-only setup uses `outputs: [notion]` and writes no
local files). The targets:
- **CSV** (open in Excel/Numbers)
- **HTML table** (sortable, nice to skim)
- **Notion database** (recommended for the human side: add a status column —
  applied / interested / pass — and work through the list there). Each role also
  carries a **`First seen`** date (the store's `first_seen`), so a Notion view sorted
  by it surfaces "what's new and when it appeared". Written idempotently on every
  render, so it backfills rows created before the field existed.

The discovery view is a first-class deliverable, not a side effect of seeding. It can
be re-run anytime — e.g. after tuning filters or adding companies.

### 4.7 Notifier
Swappable interface so the channel is a config choice, not a rewrite:
```
class Notifier:
    def send(self, posting, score, reason) -> None: ...
```
- **Telegram** (primary, for incremental pings): one POST to
  `api.telegram.org/bot{token}/sendMessage`. Good for low-latency, one-role-at-a-time
  alerts with an apply link. Token + chat ID are per-user secrets (§8).
- **Email / others**: future backends behind the same interface.

Channel split by job: Telegram for the incremental "new role dropped" ping; the
rendered digest (§4.6) for the backlog and any roll-up. Don't force the backlog
through push notifications.

On a run with new matches, the alerts are sent as one batch led by a count header
(`🎯 jobradar — N new matching roles`), cards ranked by score — so the thread opens
with the count, not an unbounded stream. The zero-match case still sends the
heartbeat instead. The LLM tier (§4.4) defaults to Haiku for this title-fit
classification — cheap enough to run on every keyword survivor.

### 4.8 Scheduler
- **GitHub Actions (recommended)**: scheduled workflow (cron), runs in the cloud for
  free, nothing local needs to be awake. State persistence between ephemeral runs:
  commit the SQLite file back to the repo, or save/restore it as a workflow artifact.
  Secrets via repo secrets. **Caveat:** GitHub's *own* cron is best-effort — it delays
  scheduled runs by hours or drops them under load, and never back-fills a skip. For
  punctual runs, keep one GitHub cron line as a daily fallback but make the **primary**
  trigger an external cron (e.g. the free cron-job.org) that calls the workflow's
  `workflow_dispatch` endpoint on time. Overlap is harmless: duplicate runs are deduped
  by the DB (notified flag) and serialized by a concurrency group.
- **`launchd` (macOS)**: simplest local option, but only fires when the Mac is awake.
- **Cheap always-on box** (VPS / Fly.io / Raspberry Pi): if fully self-hosted is
  wanted.

Airflow is explicitly *not* used — overkill for a twice-daily single-user job.

### 4.9 User-facing command surface (Claude Code)
Because this is built and run inside Claude Code, expose the common operations as
**slash commands / thin CLI entrypoints** rather than scripts the user has to
remember to invoke. Minimum set:

- **`/setup`** — interactive onboarding interview that *writes the config files for
  the user*. Asks for target roles, seniority, geography, and companies, runs the
  detector (§4.2) on the supplied company URLs, and populates `companies.csv` +
  `matcher.yml`. This is the friendly alternative to hand-editing YAML and is the
  primary reason a non-author (Fenja) can onboard in minutes. (Pattern borrowed from
  prior art — see §13.)
- **`/discover`** — run/refresh the discovery view (§4.6): scrape all, match, render
  the ranked open-roles list. Re-runnable anytime (e.g. after tuning filters).
- **`/add-company`** — append one company, run the detector on it, seed it silently
  (§6) so it never floods.
- **`/run`** — the scheduled steady-state pass (scrape → diff → alert). Same code the
  scheduler calls.

Keep these as thin wrappers over the same underlying functions the scheduler uses —
the commands are ergonomics, not a second code path.

**Design-doc vs CLAUDE.md note:** this document is the spec and should live at
`docs/DESIGN.md`. `CLAUDE.md` is a separate, lean file that Claude Code auto-loads
every turn — keep it to project structure, how to run, conventions, and the hard
"never commit `.env` / the SQLite file" rules, and have it *reference* this doc rather
than duplicate it.

## 5. Data model (SQLite)

Single table is sufficient for the PoC:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    company       TEXT NOT NULL,
    job_id        TEXT NOT NULL,       -- ATS stable ID
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT,
    content_hash  TEXT,                -- to detect edits (usually ignored)
    match_score   REAL,
    match_reason  TEXT,
    status        TEXT DEFAULT 'open', -- 'open' | 'closed'
    first_seen    TEXT NOT NULL,       -- ISO timestamp
    last_seen     TEXT NOT NULL,
    notified      INTEGER DEFAULT 0,   -- 0/1
    PRIMARY KEY (company, job_id)
);
```

The composite primary key `(company, job_id)` is what gives us atomic
"insert-if-new" — the database enforces dedup so we don't hand-roll it. This is the
single biggest simplification in the project.

Optional `runs` table for run metadata (timestamp, counts) — nice for debugging, not
required.

## 6. Lifecycle: seed vs steady state

The cold-start problem: on first contact with a company, every open role is "new"
relative to empty state — a big list would fire hundreds of alerts at once.

**Unified rule (also handles adding companies later):**
Two invariants first, because they prevent subtle bugs:
- **Store every scraped posting**, matching or not. Per-company "seen" state must be
  complete — otherwise a company with no matches at seed time never gets a row, stays
  in seed mode forever, and its *first matching* role later would be wrongly
  suppressed instead of alerted.
- **Always compute `match_score` + `match_reason` on insert.** Scoring is independent
  of alerting; the discovery view ranks by score, so seed entries need it too. The
  seed flag gates *only whether we alert*, nothing else.

On each run, for each scraped posting:
- If `(company, job_id)` already in DB → update `last_seen` (and `content_hash`);
  no alert.
- If new → compute match score, insert the row, then decide alerting:
  - **If this company had no rows in the DB before this run → seed mode for that
    company.** Set `notified = 1` (suppress alert). Matching rows still flow into the
    discovery backlog; they're just not pushed.
  - **Else → steady state.** If the score passes the threshold, send a Telegram alert,
    then set `notified = 1`. (Sub-threshold rows are stored but not alerted.)
- Any DB row for a company not present in the current scrape → mark `status = 'closed'`
  (keep the row; optionally notify on close — default off).

Note the seed test is "had no rows *before this run*" — evaluate it per company at the
start of the run, not per posting, or the first stored posting would flip the company
out of seed mode mid-run and the rest would alert.

This single "has this company been seen before?" check elegantly covers both the
initial seed and the case of adding company #200 next month — newly added companies
are seeded silently, never flooded.

Discovery view (§4.6) reads `status='open'` ordered by `match_score` — independent of
the alert path, re-runnable anytime.

## 7. Why SQLite (not CSV/Excel, not Supabase)

The core operation every run is a keyed lookup-and-compare ("have I seen this job
ID?") across the whole history.

- **CSV/Excel** forces a full in-memory scan, has no real unique key or atomic
  update, and can corrupt on a mid-write crash. Fine as an *export* format, wrong as
  the *state* store.
- **SQLite** is a single portable file (commit it or store as a GH Actions artifact,
  exactly like a CSV), is built into Python (`sqlite3`, no install), and gives a real
  unique key with atomic insert-if-new — which *is* the dedup logic.
- **Supabase/Postgres** solves problems we don't have yet (multi-user, hosted API,
  auth, concurrency). It adds an account, a connection string, secrets, and a network
  dependency, for no PoC payoff. Migration path later is trivial (≈same SQL).

**Storage model in one line:** SQLite as the single-file source of truth and dedup
engine; CSV / HTML / Notion as render targets for the parts a human looks at.

## 8. Shareability requirements (clone → configure → run)

Design goal: a technical friend gets running with **no code edits**. The distribution
model is a public GitHub **Template** repo (the *engine*); each friend makes their own
private repo from it (their *deployment*) and runs it on their own GitHub Actions with
their own keys. The template ships **examples only**; a deployment commits its own
`matcher.yml` (and, in CSV mode, its own `companies.csv` via `git add -f`). Because the
deployment's `matcher.yml` is a file the template never tracked, pulling engine updates
is conflict-free. Three hard rules:

1. **Secrets never in the repo.** Telegram bot token, chat ID, (optional) LLM API key,
   and (optional) Notion integration token are read from env vars / an untracked
   `.env.local`. Commit a placeholder
   `.env.example`; each person copies it to `.env.local` (gitignored) and fills it
   in. Each person makes their own bot via @BotFather — this is the one irreducible
   manual step.
2. **Config via copied example files.** The template commits `companies.example.csv`
   and `matcher.example.yml` only; users copy them to `companies.csv` / `matcher.yml`.
   Never carry your own populated config into the *template* — friends would inherit
   your companies/profile or hit merge conflicts. (A *deployment* does commit its own
   `matcher.yml` so the scheduler can read it from the checkout — see §4.6 / CLAUDE.md;
   that file just never originates in the template or its history.) Likewise secrets:
   commit `.env.example`, copy to the gitignored `.env.local`.
3. **State gitignored and created fresh.** The SQLite file must be gitignored; the
   code initializes an empty DB if none exists. Otherwise a friend inherits your
   "seen" set and her discovery run is silently polluted.

Also required for reproducibility:
- Pin dependencies (`requirements.txt` or `environment.yml`) and document the Python
  version.
- A short README **Setup** section covering: BotFather token + chat ID, copying the
  example configs, `pip install`, run detect, run.

Target onboarding for a friend (the `/setup` command, §4.9, automates steps 3–4 and
the detector — manual editing is the fallback, not the default):
1. `git clone`
2. copy `.env.example` → `.env.local`, add Telegram token + chat ID
3. copy `companies.example.csv` → `companies.csv`, add companies
4. edit `matcher.yml` — titles, geography
5. `pip install -r requirements.txt`
6. run detect, then run — first run = personal discovery backlog

## 9. Tech stack

- **Language:** Python 3.12.
- **HTTP:** `requests`.
- **HTML parsing:** stdlib `re` + `html.unescape` (the detector and the JD
  plain-text extractor are light enough that a parser dependency isn't warranted).
- **JS rendering (optional, not yet used):** `Playwright` — parking-lot item (§Milestone 10).
- **State:** `sqlite3` (stdlib).
- **Config:** CSV / `PyYAML`. `pydantic` is pulled in by the LLM tier (structured output).
- **Secrets:** `python-dotenv`.
- **Telegram:** direct HTTP via `requests` (no heavy library needed).
- **Notion (optional config + render surface):** Notion REST API via `requests` (no
  SDK); token is a per-user secret.
- **LLM tier (optional):** `anthropic` SDK, behind a toggle.
- **Scheduling:** GitHub Actions cron (no app dependency).

Keep external dependencies minimal — every dependency is friction for the friend who
clones it.

## 10. Build milestones (vertical slices)

Build end-to-end thin slices and verify each against real careers pages before
widening. Do **not** implement the whole spec at once.

1. **Walking skeleton:** Greenhouse adapter → matcher (keyword + exclude + location +
   score) → SQLite (schema + insert-if-new + seed rule) → CSV discovery export.
   Verify on 3–5 real companies.
2. **Steady-state diff + Telegram:** implement the new-vs-seen alert path and the
   Telegram notifier. Verify a planted new posting fires exactly one alert.
3. **More adapters:** Lever, Ashby, Personio (then Workday, SmartRecruiters,
   Recruitee as needed by the actual list).
4. **Detector:** auto-classify ATS + slug from name/URL.
5. **Discovery polish:** HTML and/or Notion render, ranked output with reasons.
6. **Optional LLM tier:** behind a config toggle, over survivors only.
7. **Scheduling:** GitHub Actions workflow + state persistence (commit-back or
   artifact).
8. **Shareability pass:** example configs, `.env.example`, gitignore, README setup,
   pinned deps. Test by having a second person clone and run cold.
9. **Command surface:** wrap the operations as `/setup`, `/discover`, `/add-company`,
   `/run` (§4.9); make `/setup` populate configs interactively + run the detector.
10. **Optional:** Playwright fallback for specific bespoke boards that matter.

## 11. Operational notes / etiquette

- Cache responses within a run; modest request interval; honor robots.txt. We hit a
  few sites a few times a day — not a crawler.
- Expect bespoke pages to occasionally break and need a tweak; the ATS-adapter
  approach keeps this rare.
- Match on `job_id`, never title, to avoid re-alerts on edits.

## 12. Future (post-PoC, explicitly deferred)

- Email notifier backend.
- Notion as the primary human-facing surface with status workflow.
- **Optional downstream application layer.** Everything in this doc stops at
  *surfacing* a matching role. A separate, later layer could take a chosen role and
  help *apply* — evaluate fit in depth, tailor a CV, draft a cover letter, prep
  interviews. This is explicitly a different concern from monitoring and out of PoC
  scope; the prior art in §13 is a ready-made reference if it's ever built. The clean
  seam: this tool emits a matched role (URL + metadata); an application layer consumes
  one.
- If multi-device or a web UI is ever wanted: migrate state to Supabase/Postgres and
  build a thin UI. This is the "v2 if I love it" path, not PoC scope.

## 13. Prior art and inspiration

`MadsLorentzen/ai-job-search` (a Claude Code project) was reviewed during design. It
solves the **downstream** half of a job hunt — application assistance — not the
monitoring/discovery problem this tool targets. It has no scheduling, diffing, or
new-posting alerting; its flow is on-demand per known job. The boundary matters:
treat it as a reference for a possible future layer (§12), not a base to fork.

What we deliberately borrow:
- **Claude Code ergonomics** — a project driven by `CLAUDE.md` + slash commands, with
  an interactive `/setup` that *writes the user's config files* instead of making them
  hand-edit. This shaped §4.9 and the §8 onboarding.
- **Fork-and-fill shareability** — profile/config kept strictly separate from logic;
  it independently validates the §8 approach.
- **Drafter-reviewer pattern** (draft → second agent critiques + researches company →
  revise) — noted only as a reference for the optional application layer (§12).

What we deliberately do **not** borrow:
- **Its scraping approach** — bespoke, one-CLI-tool-per-portal, built for specific
  (Danish) job boards. The ATS-adapter design (§4.3) is the intended improvement;
  do not regress to per-source scrapers.

Caveat: it is a very new, single-commit repo — a pattern reference, not a
battle-tested dependency.
