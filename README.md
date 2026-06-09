# jobradar

A personal job-posting monitor. It watches the careers pages of companies you care
about and pings you on Telegram when a new role matching your profile appears — twice
a day, automatically, for free, on your own GitHub account.

It scrapes the common ATS platforms (Greenhouse, Lever, Ashby, Personio), ranks each
new posting against *your* profile, and (optionally) uses an LLM to read the job
description and judge fit. Matches land in Telegram and an optional Notion board.

---

## Deploy your own

This is a **self-hosted** tool: you run your own copy on your own GitHub Actions, with
your own keys. It's free to run. Budget ~15 minutes.

### What you'll need

| | Required? | Notes |
|---|---|---|
| **GitHub account** | yes | Free Actions minutes cover twice-daily runs. |
| **Telegram bot** | yes | Created via [@BotFather](https://t.me/BotFather) in 1 minute. |
| **Anthropic API key** | recommended | Powers the CV→profile magic *and* the smart description-aware matching. Costs **pennies/month** (Haiku, runs only on a handful of survivors). Get one at [console.anthropic.com](https://console.anthropic.com). **Without it**, the tool still runs in keyword-only mode (less precise, no CV personalization, $0). |
| **Notion** | optional | A nicer triage board for matches. CSV works fine instead. |

### 1. Make your own copy

Click **“Use this template” → Create a new repository**, and make it **private**
(your profile and company list live here). Then clone it and install:

```bash
git clone https://github.com/<you>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt          # Python 3.12
```

### 2. Run the setup wizard (recommended)

```bash
python -m jobradar setup
```

This walks you through it: it writes `.env.local`, **auto-detects your Telegram chat
ID** (just message your bot once — no copying numbers), optionally distils your CV into
your profile, and sets up CSV or Notion. Re-runnable any time. Prefer to do it by hand?
The manual equivalent is below.

<details>
<summary>Manual setup (instead of the wizard)</summary>

```bash
cp .env.example .env.local                # then edit .env.local
```

Fill in:
- `TELEGRAM_BOT_TOKEN` — from @BotFather.
- `TELEGRAM_CHAT_ID` — your chat ID. Easiest way: message your new bot once, then open
  `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy the `chat.id`.
- `ANTHROPIC_API_KEY` — if you want smart matching (recommended).
- `NOTION_TOKEN` — only if you choose Notion below.

**Tell it who you are:**

```bash
cp config/matcher.example.yml config/matcher.yml
```

Open `config/matcher.yml` and set your `profile`, term lists, and locations. With an
Anthropic key you can auto-generate the profile from your CV (it stays local, only the
distilled paragraph is written into the config):

```bash
pip install -e ".[llm]"                   # installs the Anthropic SDK + PDF reader
python -m jobradar profile path/to/your-cv.pdf
```

</details>

### 3. Choose your company source

Where your **company list** lives — two options (the wizard asks; here's the decision):

**Option A — CSV (simplest, no accounts):**
```bash
cp config/companies.example.csv config/companies.csv
# edit it: one row per company (name + careers_url is enough)
```
> ⚠️ `companies.csv` is gitignored by default. For the scheduler to see it, commit it
> to **your own private repo**: `git add -f config/companies.csv`. (It's only your
> company list, and your repo is private.)

**Option B — Notion (nicer triage board):** set `companies_source: notion` in
`matcher.yml`, add `NOTION_TOKEN` to `.env.local`, then let jobradar build the
databases for you:
```bash
python -m jobradar setup-notion <a-notion-page-url-you-shared-with-your-integration>
```
This creates a Companies table (fill it in) and a Roles board, and wires their ids
into `matcher.yml`. Companies then live in Notion — nothing to commit.

### 4. Try it locally

```bash
python -m jobradar                        # initialize the local DB
python -m jobradar detect                 # fill in each company's ATS + slug
python -m jobradar discover               # scrape + rank everything once
```

### 5. Turn on the scheduler

Push your changes, then in your repo on GitHub:
1. **Settings → Secrets and variables → Actions** → add `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`, and (if used) `ANTHROPIC_API_KEY`, `NOTION_TOKEN`.
2. **Actions** tab → enable workflows. The monitor runs twice daily and on demand
   (“Run workflow”). The first run seeds your backlog silently; new postings after
   that ping you.

The schedule lives in [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml)
(cron is in **UTC** — the comments show how to shift it to your timezone).

---

## Commands

CLI is `python -m jobradar <command>` (or the `jobradar` console script after
`pip install -e .`). Run from the repo root.

| Command | What it does |
|---------|--------------|
| `setup` | Interactive first-run wizard: secrets, Telegram chat-ID auto-detection, CV→profile, CSV/Notion. |
| `setup-notion <page-url>` | Create the Companies + Roles Notion databases and wire their ids into `matcher.yml`. |
| `profile <cv.pdf\|.txt>` | Distil a CV into your matching profile (stays local). Needs `ANTHROPIC_API_KEY`. |
| `detect` | Fill in each company's ATS + slug (writes back to the source). |
| `discover` | Scrape all companies, rank matches, write the configured outputs (CSV/HTML/Notion). |
| `run` | The scheduled steady-state pass: scrape → diff → alert new matches via Telegram. |
| `review` | Write `review.csv` — every keyword-relevant role with score, reason, and a shown/dropped flag (for tuning your filter). |

## How matching works (the short version)

Each posting passes through keyword tiers (title include/exclude, seniority, and a
**hard** location filter), and survivors are optionally handed to an LLM that reads the
description and your profile to score fit 0–1. Two knobs in `matcher.yml`:
- `profile` — *soft* fit: who you are and what you want (graded).
- `deal_breakers` — *hard* vetoes: any clear hit drops the role regardless of fit
  (e.g. a language you don't speak, a clearance you lack).

All of it is config, not code. See [`docs/DESIGN.md`](docs/DESIGN.md) for the full
design and rationale.

## Development

```bash
pip install -e ".[dev]"     # adds pytest
pytest -q                   # pure/offline suite — no network, secrets, or LLM
```

CI runs the same suite on every push to `main` and every PR
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## License

MIT — see [`LICENSE`](LICENSE). Shared as-is for friends to self-host; no warranty.
