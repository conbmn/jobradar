# jobradar — Claude Code context

## What this project is
A personal job-posting monitor. See `docs/DESIGN.md` for the full spec and rationale.
See `docs/BUILD_PLAN.md` for the staged execution plan and current progress.

This is the **engine**: a public GitHub **Template** repo that ships **examples only
— never a real `matcher.yml`/`companies.csv`**. The organizing idea is the split
between the **engine** (the code here, shared by everyone) and a **deployment** (your
own private repo made from this template, holding your config + secrets). To run your
own, see the README ("Deploy your own").

## How to work on this project

The tool is **built and operational** (all milestones in `docs/BUILD_PLAN.md` are done;
it runs twice daily via GitHub Actions). Work now is maintenance and tuning, not
greenfield milestones. `docs/DESIGN.md` is the source of truth for behaviour — if you
change behaviour, update DESIGN.md in the same change. Most matching/quality tuning is
**config in `config/matcher.yml`** (profile, deal_breakers, min_fit, term lists), not
code — reach for that first before touching the matcher.

## Hard rules — never break these

- **Never commit `.env` or `.env.local`** — secrets live there and are gitignored.
- **Never commit `jobradar.db` or any `*.db` file** — state is personal and gitignored.
- **Never commit a real `config/companies.csv` to the public template** — only
  `companies.example.csv` belongs there. A *deployment* using CSV mode commits its own
  list to its own private repo via `git add -f` (see README); it's gitignored by
  default so it can't leak into the template by accident.
- **`config/matcher.yml`: deployment commits it, the template never does.** In a
  private *deployment* repo it's committed and read straight from the checkout by the
  scheduler (.github/workflows/monitor.yml) — edit + commit to change behaviour; it
  holds no secrets (those are env/repo-secrets). This template repo ships only
  `matcher.example.yml`; a real `matcher.yml` must never be committed here.
- If you think the spec in `docs/DESIGN.md` is wrong, raise it and fix DESIGN.md
  *before* coding around it.

## Package layout

```
jobradar/
├── CLAUDE.md               ← this file
├── README.md               ← "deploy your own" guide (the page friends read)
├── LICENSE                 ← MIT
├── docs/DESIGN.md          ← spec (source of truth for what/why)
├── docs/BUILD_PLAN.md      ← staged plan (living progress doc)
├── pyproject.toml          ← packaging + `jobradar` console script
├── requirements.txt        ← pinned deps, Python 3.12
├── .env.example            ← secrets template (committed)
├── .gitignore
├── config/
│   ├── companies.example.csv   ← committed template
│   ├── matcher.example.yml     ← committed template (the only one in the public template repo)
│   └── matcher.yml             ← real config; committed in a deployment, never in the template
├── jobradar/               ← Python package
│   ├── store.py            ← SQLite state store (DESIGN §5, §7)
│   ├── sources/            ← company list source: CSV or Notion (DESIGN §4.1)
│   ├── adapters/           ← one adapter per ATS (DESIGN §4.3)
│   ├── detector.py         ← ATS classifier (DESIGN §4.2)
│   ├── matcher.py          ← title+location keyword scoring (DESIGN §4.4 tiers 1–4)
│   ├── matcher_llm.py      ← optional LLM fit tier (DESIGN §4.4 tier 5)
│   ├── notify/             ← Telegram + future backends (DESIGN §4.7)
│   ├── render/             ← CSV/HTML/Notion outputs (DESIGN §4.6)
│   ├── pipeline.py         ← orchestration (detect / discover / run / review)
│   ├── cli.py              ← entrypoints (DESIGN §4.9)
│   └── __main__.py         ← `python -m jobradar`
└── .github/workflows/      ← GH Actions scheduler (monitor.yml)
```

## Running

```bash
pip install -r requirements.txt          # Python 3.12
cp .env.example .env.local               # then fill in Telegram token + chat ID
cp config/companies.example.csv config/companies.csv
cp config/matcher.example.yml config/matcher.yml

# Initialize DB (and verify it starts clean):
python -m jobradar
```

Run from the repo root. CLI: `python -m jobradar <command>`:
`setup-notion` (create the Notion DBs), `profile <cv>` (distill a CV into the matcher
profile), `detect` (fill ats/slug), `discover` (scrape → rank → CSV/HTML/Notion),
`run` (the scheduled steady-state pass + alerts), `review` (CSV of shown-vs-dropped
roles with reasons, for QA). The LLM tier (Haiku) needs `ANTHROPIC_API_KEY` in
`.env.local` and `pip install -e ".[llm]"`.

## Config / secrets / state separation (DESIGN §8)

| Kind    | Committed?      | File(s)                              |
|---------|-----------------|--------------------------------------|
| Secrets | No (.gitignored) | `.env.local` — copy from committed `.env.example`, fill in place |
| Config  | Mixed            | `config/matcher.yml` **committed in a deployment** (matching behaviour, no secrets), example-only in the template; `config/companies.csv` gitignored (CSV-mode users `git add -f` into their own private repo, or use Notion). Both start as `*.example.*` copies. |
| State   | No (.gitignored) | `jobradar.db` — created fresh by `store.py`; in CI it's an actions/cache |
