"""
Unit tests for analytics/metrics.py.

Deliberately uses small, hand-built fixtures (not the generated dataset) so
expected values can be computed by hand and the tests stay fast and fully
deterministic regardless of the data generator's random seed.

Fixture story (5 users, weeks 0-4):
  uid1  channel A  activated  Pro   active every week (loyal)
  uid2  channel A  activated  Free  active weeks 0-1 only, then churns
  uid3  channel A  NOT activated      never active (nothing to retain)
  uid4  channel B  activated  Pro   active every week (loyal)
  uid5  channel B  activated  Free  active weeks 0-2, then churns
"""

import math
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from analytics.metrics import (
    retention_curve,
    funnel_analysis,
    churn_by_segment,
    ab_significance_test,
    ltv_by_channel,
    PRO_PRICE_PER_ACTIVE_WEEK,
)


@pytest.fixture
def users():
    return pd.DataFrame({
        "user_id": [1, 2, 3, 4, 5],
        "channel": ["A", "A", "A", "B", "B"],
        "activated": [True, True, False, True, True],
        "plan": ["Pro", "Free", "Free", "Pro", "Free"],
    })


@pytest.fixture
def activity():
    # (user_id, week_number, active)
    rows = []
    active_weeks = {
        1: {0, 1, 2, 3, 4},
        2: {0, 1},
        3: set(),
        4: {0, 1, 2, 3, 4},
        5: {0, 1, 2},
    }
    for uid, weeks in active_weeks.items():
        for w in range(5):
            rows.append((uid, w, w in weeks))
    return pd.DataFrame(rows, columns=["user_id", "week_number", "active"])


# ---------------------------------------------------------------------------
# retention_curve
# ---------------------------------------------------------------------------

def test_retention_curve_overall_week0_is_100pct(users, activity):
    result = retention_curve(users, activity)
    assert result["curve_pct"][0] == 100.0


def test_retention_curve_by_channel_values(users, activity):
    result = retention_curve(users, activity, channel="A")
    assert result["n_activated_users"] == 2
    # week0=100, week1=100, week2=50 (only uid1 left), week3=50, week4=50
    assert result["curve_pct"] == [100.0, 100.0, 50.0, 50.0, 50.0]


def test_retention_curve_excludes_unactivated_users(users, activity):
    # uid3 (channel A, not activated) must never count toward the base
    result = retention_curve(users, activity, channel="A")
    assert result["n_activated_users"] == 2  # not 3


def test_retention_curve_channel_b(users, activity):
    result = retention_curve(users, activity, channel="B")
    # week2: both uid4 & uid5 active -> 100; week3: only uid4 -> 50
    assert result["curve_pct"] == [100.0, 100.0, 100.0, 50.0, 50.0]


# ---------------------------------------------------------------------------
# funnel_analysis
# ---------------------------------------------------------------------------

def test_funnel_overall_stage_counts(users, activity):
    result = funnel_analysis(users, activity)
    stages = {s["stage"]: s["count"] for s in result["overall"]["stages"]}
    assert stages["Signed up"] == 5
    assert stages["Activated"] == 4
    assert stages["Retained (week 1)"] == 4  # all 4 activated users active wk1
    assert stages["Retained (week 4)"] == 2  # only uid1 and uid4


def test_funnel_drop_off_rate_computed_correctly(users, activity):
    result = funnel_analysis(users, activity)
    stages = {s["stage"]: s for s in result["overall"]["stages"]}
    # Activated (4) -> Retained wk1 (4): 0 drop-off
    assert stages["Retained (week 1)"]["drop_off_count"] == 0
    # Retained wk1 (4) -> Retained wk4 (2): 50% drop-off
    assert stages["Retained (week 4)"]["drop_off_count"] == 2
    assert stages["Retained (week 4)"]["drop_off_rate_pct"] == 50.0


def test_funnel_by_channel_matches_overall_sum(users, activity):
    result = funnel_analysis(users, activity)
    a_signups = result["by_channel"]["A"]["stages"][0]["count"]
    b_signups = result["by_channel"]["B"]["stages"][0]["count"]
    assert a_signups + b_signups == result["overall"]["stages"][0]["count"]


# ---------------------------------------------------------------------------
# churn_by_segment
# ---------------------------------------------------------------------------

def test_churn_by_segment_rates(users, activity):
    # churn_after_week=3 -> late weeks = [3, 4]
    result = churn_by_segment(users, activity, segment_col="channel", churn_after_week=3)
    rates = {r["channel"]: r["churn_rate_pct"] for r in result["ranked"]}
    # uid2 (A) and uid5 (B) are both inactive in weeks 3 and 4 -> churned
    assert rates["A"] == 50.0
    assert rates["B"] == 50.0
    assert result["overall_churn_rate_pct"] == 50.0


def test_churn_by_segment_ranked_worst_first(users, activity):
    result = churn_by_segment(users, activity, segment_col="channel", churn_after_week=3)
    rates = [r["churn_rate_pct"] for r in result["ranked"]]
    assert rates == sorted(rates, reverse=True)


def test_churn_by_segment_invalid_column_raises(users, activity):
    with pytest.raises(ValueError):
        churn_by_segment(users, activity, segment_col="not_a_real_column")


# ---------------------------------------------------------------------------
# ab_significance_test
# ---------------------------------------------------------------------------

def test_ab_significance_test_basic_shape(users, activity):
    result = ab_significance_test(users, "A", "B", metric="activated")
    assert result["channel_a"] == "A"
    assert result["channel_b"] == "B"
    assert result["rate_a_pct"] == pytest.approx(66.67, abs=0.01)
    assert result["rate_b_pct"] == 100.0
    assert isinstance(result["p_value"], float)
    assert 0.0 <= result["p_value"] <= 1.0


def test_ab_significance_test_swapping_channels_flips_z_sign(users, activity):
    ab = ab_significance_test(users, "A", "B", metric="activated")
    ba = ab_significance_test(users, "B", "A", metric="activated")
    assert ab["z_score"] == pytest.approx(-ba["z_score"], abs=1e-9)


def test_ab_significance_test_unknown_metric_raises(users, activity):
    with pytest.raises(ValueError):
        ab_significance_test(users, "A", "B", metric="not_a_column")


def test_ab_significance_test_identical_rates_not_significant():
    identical = pd.DataFrame({
        "channel": ["X"] * 100 + ["Y"] * 100,
        "activated": [True, False] * 100,
    })
    result = ab_significance_test(identical, "X", "Y", metric="activated")
    assert result["is_significant"] is False
    assert result["z_score"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# ltv_by_channel
# ---------------------------------------------------------------------------

def test_ltv_by_channel_values(users, activity):
    result = ltv_by_channel(users, activity)
    by_channel = {r["channel"]: r for r in result["ranked"]}

    # Channel A: uid1 active 5 weeks, uid2 active 2 weeks -> avg 3.5
    #            pro_share = 1/2 = 0.5 -> ltv = 3.5 * 0.5 * 999
    assert by_channel["A"]["avg_active_weeks"] == pytest.approx(3.5)
    assert by_channel["A"]["pro_share_pct"] == pytest.approx(50.0)
    expected_ltv_a = 3.5 * 0.5 * PRO_PRICE_PER_ACTIVE_WEEK
    assert by_channel["A"]["estimated_ltv_inr"] == pytest.approx(expected_ltv_a, abs=0.01)

    # Channel B: uid4 active 5 weeks, uid5 active 3 weeks -> avg 4.0
    assert by_channel["B"]["avg_active_weeks"] == pytest.approx(4.0)
    expected_ltv_b = 4.0 * 0.5 * PRO_PRICE_PER_ACTIVE_WEEK
    assert by_channel["B"]["estimated_ltv_inr"] == pytest.approx(expected_ltv_b, abs=0.01)


def test_ltv_ranked_descending(users, activity):
    result = ltv_by_channel(users, activity)
    values = [r["estimated_ltv_inr"] for r in result["ranked"]]
    assert values == sorted(values, reverse=True)
