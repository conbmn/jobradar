"""Config: the committed example parses, and the in-place YAML editors behave."""
from pathlib import Path

import yaml

from jobradar.pipeline import (
    _set_yaml_value, _set_yaml_block, _coverage_line, _show_threshold,
)

_EXAMPLE = Path(__file__).parent.parent / "config" / "matcher.example.yml"


def test_example_matcher_parses_with_expected_shape():
    cfg = yaml.safe_load(_EXAMPLE.read_text(encoding="utf-8"))
    for key in ("companies_source", "include_terms", "exclude_terms", "locations",
                "scoring", "llm_tier"):
        assert key in cfg, f"missing {key}"
    assert cfg["llm_tier"]["enabled"] is False  # example ships disabled


def test_set_yaml_value_scalar_and_nested(tmp_path):
    p = tmp_path / "m.yml"
    p.write_text("companies_source: csv\nllm_tier:\n  enabled: false\n")
    assert _set_yaml_value(p, "companies_source", "notion", quote=False) == 1
    assert _set_yaml_value(p, "enabled", "true", quote=False) == 1
    cfg = yaml.safe_load(p.read_text())
    assert cfg["companies_source"] == "notion"
    assert cfg["llm_tier"]["enabled"] is True


def test_set_yaml_block_replaces_only_target(tmp_path):
    p = tmp_path / "m.yml"
    p.write_text(
        "profile: >\n  old line one\n  old line two\n"
        "deal_breakers: >\n  keep me\nmin_fit: 0.5\n"
    )
    assert _set_yaml_block(p, "profile", "brand new profile text") is True
    cfg = yaml.safe_load(p.read_text())
    assert "brand new profile" in cfg["profile"]
    assert "old line" not in cfg["profile"]
    assert cfg["deal_breakers"].strip() == "keep me"  # sibling block untouched
    assert cfg["min_fit"] == 0.5


def test_coverage_line():
    assert "12 fetched" in _coverage_line(12, 0, 0)
    line = _coverage_line(5, 2, 1)
    assert "no ats/slug" in line and "unsupported ATS" in line


def test_show_threshold_modes():
    cfg = {"llm_tier": {"enabled": True, "min_fit": 0.42}}
    assert _show_threshold(cfg, llm_matcher=object(), kw_threshold=3.0) == 0.42
    assert _show_threshold({}, llm_matcher=None, kw_threshold=3.0) == 3.0
