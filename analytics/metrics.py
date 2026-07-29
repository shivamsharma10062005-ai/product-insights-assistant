"""
analytics/metrics.py
---------------------
Pure data-analysis layer. No LLM calls, no network calls, no dependency on
the `ai` package. Every function here takes plain pandas DataFrames in and
returns plain dicts/DataFrames out, so it can be unit tested in isolation
and imported by the backend, a notebook, or a CLI with zero side effects.

This separation is the architectural point of the whole project: the AI
layer is only ever allowed to *narrate* the dicts these functions return,
never to compute or invent a number itself.
"""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "product_insights.db")


def load_data(db_path: Optional[str] = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the two source tables from the SQLite database. Raises a clear
    error if the database hasn't been generated yet, instead of a confusing
    low-level sqlite3 error."""
    path = db_path or DB_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Dataset not found. Run `python data/generate_data.py` first "
            "to generate data/product_insights.db."
        )
    conn = sqlite3.connect(path)
    try:
        users = pd.read_sql("SELECT * FROM users", conn, parse_dates=["signup_date"])
        activity = pd.read_sql("SELECT * FROM weekly_activity", conn)
    finally:
        conn.close()
    # SQLite has no native boolean type; generate_data.py stores these as
    # 0/1 integers, so restore proper bool dtype on the way out.
    users["activated"] = users["activated"].astype(bool)
    activity["active"] = activity["active"].astype(bool)
    return users, activity


# ---------------------------------------------------------------------------
# 1. Retention curve
# ---------------------------------------------------------------------------

def retention_curve(users: pd.DataFrame, activity: pd.DataFrame,
                     channel: Optional[str] = None) -> dict:
    """
    % of ACTIVATED users still active in each week (0..max week).
    Week 0 is always 100% by construction (activation = a week-0 event).

    If `channel` is given, restricts to that channel; otherwise computes
    one curve per channel plus an overall curve.
    """
    activated_ids = users.loc[users["activated"], "user_id"]
    act = activity[activity["user_id"].isin(activated_ids)]

    def _curve_for(user_ids: pd.Series) -> list[float]:
        sub = act[act["user_id"].isin(user_ids)]
        n_base = len(user_ids)
        if n_base == 0:
            return []
        by_week = sub.groupby("week_number")["active"].sum()
        max_week = int(activity["week_number"].max())
        return [round(100.0 * by_week.get(w, 0) / n_base, 2) for w in range(max_week + 1)]

    if channel is not None:
        ids = users.loc[(users["activated"]) & (users["channel"] == channel), "user_id"]
        return {
            "scope": channel,
            "n_activated_users": int(len(ids)),
            "curve_pct": _curve_for(ids),
        }

    result = {"scope": "overall", "n_activated_users": int(len(activated_ids)),
              "curve_pct": _curve_for(activated_ids), "by_channel": {}}
    for ch in sorted(users["channel"].unique()):
        ids = users.loc[(users["activated"]) & (users["channel"] == ch), "user_id"]
        result["by_channel"][ch] = {
            "n_activated_users": int(len(ids)),
            "curve_pct": _curve_for(ids),
        }
    return result


# ---------------------------------------------------------------------------
# 2. Funnel with per-stage drop-off
# ---------------------------------------------------------------------------

def funnel_analysis(users: pd.DataFrame, activity: pd.DataFrame) -> dict:
    """
    Signup -> Activated -> Retained week 1 -> Retained week 4, overall and
    broken out by channel, with per-stage counts, absolute drop-off, and
    drop-off rate.
    """
    week1_active_ids = set(activity.loc[(activity["week_number"] == 1) & (activity["active"]), "user_id"])
    week4_active_ids = set(activity.loc[(activity["week_number"] == 4) & (activity["active"]), "user_id"])

    def _funnel_for(df: pd.DataFrame) -> dict:
        n_signup = len(df)
        n_activated = int(df["activated"].sum())
        n_week1 = int(df.loc[df["activated"] & df["user_id"].isin(week1_active_ids)].shape[0])
        n_week4 = int(df.loc[df["activated"] & df["user_id"].isin(week4_active_ids)].shape[0])

        stages = [
            ("Signed up", n_signup),
            ("Activated", n_activated),
            ("Retained (week 1)", n_week1),
            ("Retained (week 4)", n_week4),
        ]
        out_stages = []
        prev = None
        for name, count in stages:
            entry = {"stage": name, "count": count,
                     "pct_of_signups": round(100.0 * count / n_signup, 2) if n_signup else 0.0}
            if prev is not None:
                drop = prev - count
                entry["drop_off_count"] = drop
                entry["drop_off_rate_pct"] = round(100.0 * drop / prev, 2) if prev else 0.0
            out_stages.append(entry)
            prev = count
        return {"stages": out_stages}

    result = {"overall": _funnel_for(users), "by_channel": {}}
    for ch in sorted(users["channel"].unique()):
        result["by_channel"][ch] = _funnel_for(users[users["channel"] == ch])
    return result


# ---------------------------------------------------------------------------
# 3. Churn rate by segment
# ---------------------------------------------------------------------------

def compute_churn_labels(users: pd.DataFrame, activity: pd.DataFrame,
                          churn_after_week: int = 4) -> pd.DataFrame:
    """
    Returns a DataFrame of activated users with a boolean 'churned' column.
    Churn = an activated user who was ever active but is inactive for every
    week from `churn_after_week` onward (i.e. has definitively stopped
    using the product within the observation window, not merely "quiet
    this week").

    This is the single source of truth for what "churned" means in this
    project -- both churn_by_segment() (descriptive analytics) and
    ml/train_model.py (the churn-risk prediction model) call this same
    function, so the analytics dashboard and the ML model can never
    silently disagree about the definition of churn.
    """
    max_week = int(activity["week_number"].max())
    late_weeks = list(range(churn_after_week, max_week + 1))

    activated = users[users["activated"]].copy()
    late = activity[activity["week_number"].isin(late_weeks)]
    active_any_late = set(late.loc[late["active"], "user_id"])

    activated["churned"] = ~activated["user_id"].isin(active_any_late)
    return activated


def churn_by_segment(users: pd.DataFrame, activity: pd.DataFrame,
                      segment_col: str = "channel",
                      churn_after_week: int = 4) -> dict:
    """
    Churn rate per value of `segment_col`, sorted worst-first so the worst
    segment is always result['ranked'][0]. See compute_churn_labels() for
    the churn definition itself.
    """
    if segment_col not in users.columns:
        raise ValueError(f"Unknown segment column: {segment_col}")

    tmp = compute_churn_labels(users, activity, churn_after_week)

    grouped = tmp.groupby(segment_col).agg(
        n_activated_users=("user_id", "count"),
        n_churned=("churned", "sum"),
    ).reset_index()
    grouped["churn_rate_pct"] = (100.0 * grouped["n_churned"] / grouped["n_activated_users"]).round(2)
    grouped = grouped.sort_values("churn_rate_pct", ascending=False)

    overall_rate = round(100.0 * tmp["churned"].sum() / len(tmp), 2) if len(tmp) else 0.0

    return {
        "segment_column": segment_col,
        "churn_after_week": churn_after_week,
        "overall_churn_rate_pct": overall_rate,
        "ranked": grouped.to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# 4. A/B significance test (two-proportion z-test) -- activation rate
# ---------------------------------------------------------------------------

def ab_significance_test(users: pd.DataFrame, channel_a: str, channel_b: str,
                          metric: str = "activated", alpha: float = 0.05) -> dict:
    """
    Two-proportion z-test comparing `metric` (a boolean column, default
    'activated') between two channels. Implemented directly with the
    normal approximation (no scipy dependency) so the analytics layer has
    a minimal, auditable dependency footprint.
    """
    if metric not in users.columns:
        raise ValueError(f"Unknown metric column: {metric}")

    a = users.loc[users["channel"] == channel_a, metric]
    b = users.loc[users["channel"] == channel_b, metric]
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        raise ValueError("One of the channels has zero users in this dataset.")

    p1, p2 = a.mean(), b.mean()
    p_pool = (a.sum() + b.sum()) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se if se > 0 else 0.0

    # standard normal CDF via error function (no scipy needed)
    def _norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    p_value = 2 * (1 - _norm_cdf(abs(z)))

    return {
        "channel_a": channel_a, "rate_a_pct": round(float(p1) * 100, 2), "n_a": int(n1),
        "channel_b": channel_b, "rate_b_pct": round(float(p2) * 100, 2), "n_b": int(n2),
        "metric": metric,
        "z_score": round(float(z), 3),
        "p_value": round(float(p_value), 4),
        "significant_at_alpha": alpha,
        "is_significant": bool(p_value < alpha),
    }


# ---------------------------------------------------------------------------
# 5. LTV estimate by channel
# ---------------------------------------------------------------------------

PRO_PRICE_PER_ACTIVE_WEEK = 999  # INR -- keep in sync with data/generate_data.py


def ltv_by_channel(users: pd.DataFrame, activity: pd.DataFrame) -> dict:
    """
    Simplified LTV estimate per channel:
        LTV = (avg active weeks per activated user) * (Pro-plan share) * price
    This is a back-of-envelope LTV proxy (see README limitations for what a
    production version would add: discounting, non-Pro upsell paths, etc.)
    """
    activated = users[users["activated"]]
    active_weeks = activity[activity["active"]].groupby("user_id").size().rename("active_weeks")
    merged = activated.merge(active_weeks, on="user_id", how="left").fillna({"active_weeks": 0})

    rows = []
    for ch, grp in merged.groupby("channel"):
        avg_active_weeks = grp["active_weeks"].mean()
        pro_share = (grp["plan"] == "Pro").mean()
        ltv = avg_active_weeks * pro_share * PRO_PRICE_PER_ACTIVE_WEEK
        rows.append({
            "channel": ch,
            "avg_active_weeks": round(float(avg_active_weeks), 2),
            "pro_share_pct": round(100 * float(pro_share), 2),
            "estimated_ltv_inr": round(float(ltv), 2),
        })
    rows.sort(key=lambda r: r["estimated_ltv_inr"], reverse=True)
    return {"price_per_active_week_inr": PRO_PRICE_PER_ACTIVE_WEEK, "ranked": rows}
