"""to_plain_text: normalize the four ATS description shapes (adapters/base.py)."""
from jobradar.adapters.base import to_plain_text, Posting


def test_none_and_empty():
    assert to_plain_text(None) == ""
    assert to_plain_text("") == ""


def test_entity_encoded_html_greenhouse_shape():
    assert to_plain_text("&lt;p&gt;Hello &amp; bye&lt;/p&gt;") == "Hello & bye"


def test_raw_html_personio_shape():
    assert to_plain_text("<p>Hi <b>there</b></p>") == "Hi there"


def test_whitespace_collapsed():
    assert to_plain_text("a\n\n  b\t c") == "a b c"


def test_max_len_cap():
    assert to_plain_text("x" * 5000, max_len=100) == "x" * 100


def test_posting_defaults():
    p = Posting(job_id="1", title="t", location="Berlin", url="u")
    assert p.company == "" and p.description == "" and p.raw is None
