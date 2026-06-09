"""Matcher tiers 1–4 (DESIGN §4.4). Pure, no network."""
import pytest

from jobradar.matcher import match

CFG = {
    "include_terms": ["data scien", "optim", r"\bOR\b"],
    "exclude_terms": ["intern", "sales"],
    "seniority_terms": ["senior", "staff"],
    "locations": ["berlin", "remote"],
    "scoring": {"include_match_points": 2, "seniority_bonus": 1, "location_match_bonus": 1},
}


def test_include_plus_location():
    score, reason = match("Data Scientist", "Berlin", CFG)
    assert score == 3.0  # 2 include + 1 location
    assert "data scien" in reason and "location ok" in reason


def test_exclude_beats_include():
    # 'sales' (exclude) wins even though title would otherwise be a no_match
    score, reason = match("Sales Intern", "Berlin", CFG)
    assert score == 0.0 and reason == "excluded"


def test_no_include_is_no_match():
    score, reason = match("Software Engineer", "Berlin", CFG)
    assert score == 0.0 and reason == "no_match"


def test_seniority_bonus_stacks():
    score, _ = match("Senior Data Scientist", "Berlin", CFG)
    assert score == 4.0  # 2 + 1 seniority + 1 location


def test_location_is_a_hard_filter():
    score, reason = match("Data Scientist", "Santiago, Chile", CFG)
    assert score == 0.0 and "location not accepted" in reason


def test_blank_location_kept_without_bonus():
    score, reason = match("Data Scientist", "", CFG)
    assert score == 2.0 and "location unspecified" in reason


def test_multiple_includes_each_score():
    # 'optim' and '\bOR\b' both fire → 2 includes * 2 points + location
    score, reason = match("Optimization OR Specialist", "Remote", CFG)
    assert score == 5.0
    assert reason.count(",") >= 1  # two matched terms listed


@pytest.mark.parametrize("title", ["forecast", "for the win", "corporate"])
def test_word_boundary_or_does_not_match_substrings(title):
    # \bOR\b must not match 'or' inside 'forecast'/'for'/'corporate'
    score, reason = match(title, "Berlin", CFG)
    assert reason == "no_match" and score == 0.0
