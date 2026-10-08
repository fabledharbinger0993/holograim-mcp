"""Tests for measured value development. Run: python -m pytest tests/"""
import os
import sys
import tempfile

os.environ["HOLOGRAIM_DATA_DIR"] = tempfile.mkdtemp(prefix="holograim-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.schema import init_db  # noqa: E402
from identity import values as v  # noqa: E402
from identity.self_model import open_session  # noqa: E402

init_db()
v.seed_defaults()
open_session()

OK = dict(
    reasoning="In this exchange I chose to state the risk plainly before offering comfort.",
    evidence="I'll be straight with you about where it runs into what I recommended earlier.",
    provenance="own_decision",
    framing="collaborative",
)


def weight(key):
    return {d["key"]: d["weight"] for d in v.get_dispositions()}[key]


def test_small_step_applies_and_is_logged():
    r = v.adjust_value("directness", 0.05, **OK)
    assert r["applied"] and weight("directness") == 0.55
    assert v.get_changes()[0]["framing"] == "collaborative"


def test_step_too_large_rejected():
    assert not v.adjust_value("curiosity", 0.2, **OK)["applied"]


def test_external_provenance_rejected():
    r = v.adjust_value("curiosity", 0.01, **{**OK, "provenance": "web_page"})
    assert not r["applied"] and "not valid grounds" in r["error"]


def test_session_cap_then_new_session():
    open_session()
    for _ in range(3):
        assert v.adjust_value("curiosity", 0.01, **OK)["applied"]
    assert "Session limit" in v.adjust_value("curiosity", 0.01, **OK)["error"]
    open_session()
    assert v.adjust_value("curiosity", 0.01, **OK)["applied"]


def test_drift_past_threshold_waits_for_human_then_approve_and_revert():
    for _ in range(4):
        open_session()
        assert v.adjust_value("gentleness", 0.05, **OK)["applied"]
    open_session()
    r = v.adjust_value("gentleness", 0.05, **OK)
    assert r.get("pending") and weight("gentleness") == 0.7
    assert v.human_approve_change(r["change_id"][:8])["ok"] and weight("gentleness") == 0.75
    assert v.human_revert_change(r["change_id"][:8])["ok"] and weight("gentleness") == 0.7


def test_new_value_capped_low_and_anchor_untouchable():
    open_session()
    assert not v.introduce_value("candor", "x", 0.5, **OK)["applied"]
    assert v.introduce_value("candor", "Say what I notice.", 0.2, **OK)["applied"]
    assert not v.introduce_value("honesty", "x", 0.1, **OK)["applied"]
    before = v.get_anchors()
    p = v.propose_anchor_change("honesty", "Rewritten.", "I think this anchor is worded too narrowly for the work.")
    assert p["recorded"] and v.get_anchors() == before
