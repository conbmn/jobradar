"""Interactive first-run setup (`python -m jobradar setup`) — DESIGN §4.9, §8.

Collapses the manual onboarding chain (copy .env, find your Telegram chat ID,
write matcher.yml, distill a CV, create Notion DBs) into one guided flow. Plain
`input()` prompts; everything it writes is also doable by hand — this is the
friendly default, not a requirement. Safe to re-run: blank answers keep current
values, and it never overwrites an existing file's unrelated lines.
"""
import os
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).parent.parent
_ENV = _ROOT / ".env.local"
_ENV_EXAMPLE = _ROOT / ".env.example"
_CONFIG = _ROOT / "config"
_MATCHER = _CONFIG / "matcher.yml"
_MATCHER_EXAMPLE = _CONFIG / "matcher.example.yml"
_COMPANIES = _CONFIG / "companies.csv"
_COMPANIES_EXAMPLE = _CONFIG / "companies.example.csv"

_PLACEHOLDERS = {"your_bot_token_here", "your_chat_id_here", ""}


# ---------- .env.local read/write (preserve comments + unrelated keys) ----------

def _read_env(path: Path) -> dict:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def _write_env(path: Path, updates: dict) -> None:
    """Update-or-append each KEY=VALUE, leaving comments and other lines intact.
    Seeds the file from .env.example on first run so its guidance comments survive."""
    if not path.exists() and _ENV_EXAMPLE.exists():
        path.write_text(_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, val in remaining.items():  # keys not already present
        out.append(f"{key}={val}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------- prompts ----------

def _mask(val: str) -> str:
    if not val or val in _PLACEHOLDERS:
        return "(unset)"
    return f"…{val[-4:]}" if len(val) > 4 else "(set)"


def _prompt(label: str, current: str = "", secret: bool = False, optional: bool = False) -> str:
    shown = _mask(current) if secret else (current or "")
    suffix = f" [{shown}]" if shown and shown != "(unset)" else (" (optional, Enter to skip)" if optional else "")
    try:
        answer = input(f"  {label}{suffix}: ").strip()
    except EOFError:
        return current
    return answer or current


def _yes(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {label} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


# ---------- Telegram chat-id auto-resolution ----------

def resolve_chat_id(token: str, retries: int = 5) -> str | None:
    """Find the chat ID by reading the bot's recent updates — so the user never
    copies a number by hand. They just message the bot once; we poll getUpdates."""
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    print("\n  To find your chat ID: open Telegram, find your bot, and send it any "
          "message (e.g. 'hi').")
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"  Couldn't reach Telegram ({exc}). Check the bot token.")
            return None
        if not data.get("ok"):
            print(f"  Telegram rejected the token: {data.get('description', 'unknown error')}")
            return None
        chat = _latest_chat(data.get("result", []))
        if chat:
            who = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
            print(f"  ✓ Found chat: {chat['id']}" + (f" ({who})" if who else ""))
            return str(chat["id"])
        if attempt < retries:
            input(f"  No message seen yet (try {attempt}/{retries}). Send your bot a "
                  "message, then press Enter to retry… ")
        else:
            print("  Still no message found. You can set TELEGRAM_CHAT_ID by hand later.")
    return None


def _latest_chat(updates: list) -> dict | None:
    for upd in reversed(updates):
        for kind in ("message", "edited_message", "channel_post"):
            chat = (upd.get(kind) or {}).get("chat")
            if chat and "id" in chat:
                return chat
    return None


# ---------- orchestration ----------

def setup_wizard() -> None:
    print("\njobradar setup — let's get you running.\n"
          "Blank answers keep the current value; re-run any time.\n")

    env = _read_env(_ENV)
    updates: dict[str, str] = {}

    # 1) Telegram (required) ---------------------------------------------------
    print("1) Telegram alerts (required)")
    print("   Create a bot with @BotFather (https://t.me/BotFather) and paste its token.")
    token = _prompt("Bot token", env.get("TELEGRAM_BOT_TOKEN", ""), secret=True)
    if token and token not in _PLACEHOLDERS:
        updates["TELEGRAM_BOT_TOKEN"] = token
        chat_id = env.get("TELEGRAM_CHAT_ID", "")
        if chat_id in _PLACEHOLDERS or _yes("Auto-detect your chat ID now?", default=True):
            found = resolve_chat_id(token)
            if found:
                updates["TELEGRAM_CHAT_ID"] = found
    else:
        print("  Skipped — alerts won't send until TELEGRAM_BOT_TOKEN is set.")

    # 2) Anthropic key (recommended) ------------------------------------------
    print("\n2) Anthropic API key (recommended — powers CV personalization + smart matching)")
    print("   Get one at https://console.anthropic.com. Leave blank to run keyword-only (free).")
    api_key = _prompt("Anthropic API key", env.get("ANTHROPIC_API_KEY", ""), secret=True, optional=True)
    if api_key and api_key not in _PLACEHOLDERS:
        updates["ANTHROPIC_API_KEY"] = api_key

    # 3) Notion token (optional) ----------------------------------------------
    print("\n3) Notion (optional — a nicer triage board for matches)")
    notion_token = _prompt("Notion integration token", env.get("NOTION_TOKEN", ""),
                           secret=True, optional=True)
    if notion_token and notion_token not in _PLACEHOLDERS:
        updates["NOTION_TOKEN"] = notion_token

    # Persist secrets + load into this process so the steps below can use them.
    if updates:
        _write_env(_ENV, updates)
        os.environ.update(updates)
        print(f"\n  ✓ Wrote {len(updates)} value(s) to {_ENV.name}.")

    # 4) matcher.yml -----------------------------------------------------------
    print("\n4) Matching config")
    if not _MATCHER.exists():
        _MATCHER.write_text(_MATCHER_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  ✓ Created {_MATCHER.relative_to(_ROOT)} from the example.")
    else:
        print(f"  {_MATCHER.relative_to(_ROOT)} already exists — keeping it.")

    from jobradar.pipeline import _set_yaml_value  # local import to avoid cycles

    have_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if have_key and _yes("Enable the smart LLM matching tier?", default=True):
        _set_yaml_value(_MATCHER, "enabled", "true", quote=False)
        print("  ✓ llm_tier.enabled: true")

    # 5) CV → profile ----------------------------------------------------------
    if have_key:
        print("\n5) Personalize from your CV (optional)")
        cv = _prompt("Path to your CV (pdf/txt/md)", optional=True)
        if cv:
            try:
                from jobradar.pipeline import generate_profile
                generate_profile(cv)
            except Exception as exc:  # never let onboarding crash on a bad CV path
                print(f"  Couldn't distill that CV ({exc}). Edit `profile:` in matcher.yml by hand.")
    else:
        print("\n5) (CV personalization skipped — needs an Anthropic key.)")

    # 6) Company source --------------------------------------------------------
    print("\n6) Where should your company list live?")
    use_notion = bool(os.environ.get("NOTION_TOKEN")) and _yes(
        "Use Notion for the company list (vs a local CSV)?", default=False)
    if use_notion:
        _set_yaml_value(_MATCHER, "companies_source", "notion", quote=False)
        print("  Share a Notion page with your integration (page → ⋯ → Connections), then")
        page = _prompt("paste that page's URL", optional=True)
        if page:
            try:
                from jobradar.pipeline import setup_notion
                setup_notion(page)
            except Exception as exc:
                print(f"  Notion setup failed ({exc}). Re-run `python -m jobradar setup-notion <url>`.")
        else:
            print("  Skipped — run `python -m jobradar setup-notion <url>` when ready.")
    else:
        _set_yaml_value(_MATCHER, "companies_source", "csv", quote=False)
        if not _COMPANIES.exists():
            _COMPANIES.write_text(_COMPANIES_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  ✓ Created {_COMPANIES.relative_to(_ROOT)} — edit it to add your companies.")
        else:
            print(f"  {_COMPANIES.relative_to(_ROOT)} already exists — edit it to add companies.")

    # Done --------------------------------------------------------------------
    print("\n✓ Setup complete. Next:")
    print("    python -m jobradar detect     # resolve each company's ATS")
    print("    python -m jobradar discover   # scrape + rank once")
    print("    python -m jobradar run        # the steady-state pass (what the scheduler runs)")
    print("\nThen add your secrets as GitHub Actions secrets and enable the workflow "
          "(see README → 'Turn on the scheduler').")
