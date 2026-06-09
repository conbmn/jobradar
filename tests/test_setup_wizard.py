"""Setup-wizard pure helpers (no prompts, no network)."""
from jobradar import setup_wizard as w


def test_read_env_skips_comments_and_blanks(tmp_path):
    p = tmp_path / ".env.local"
    p.write_text("# comment\n\nTOKEN=abc\nEMPTY=\n")
    vals = w._read_env(p)
    assert vals == {"TOKEN": "abc", "EMPTY": ""}


def test_write_env_updates_in_place_and_preserves_rest(tmp_path):
    p = tmp_path / ".env.local"
    p.write_text("# header\nTELEGRAM_BOT_TOKEN=old\nNOTION_TOKEN=keep\n")
    w._write_env(p, {"TELEGRAM_BOT_TOKEN": "new", "TELEGRAM_CHAT_ID": "999"})
    out = p.read_text()
    vals = w._read_env(p)
    assert "# header" in out                      # comment preserved
    assert vals["TELEGRAM_BOT_TOKEN"] == "new"     # updated in place
    assert vals["NOTION_TOKEN"] == "keep"          # untouched
    assert vals["TELEGRAM_CHAT_ID"] == "999"       # appended


def test_latest_chat_picks_most_recent():
    updates = [
        {"message": {"chat": {"id": 111, "first_name": "Old"}}},
        {"message": {"chat": {"id": 222, "type": "private"}}},
    ]
    assert w._latest_chat(updates)["id"] == 222


def test_latest_chat_handles_channel_post_and_misses():
    assert w._latest_chat([{"channel_post": {"chat": {"id": 7}}}])["id"] == 7
    assert w._latest_chat([{"my_chat_member": {}}]) is None
    assert w._latest_chat([]) is None


def test_mask():
    assert w._mask("") == "(unset)"
    assert w._mask("your_bot_token_here") == "(unset)"  # placeholder treated as unset
    assert w._mask("abcdef1234") == "…1234"
    assert w._mask("abc") == "(set)"
