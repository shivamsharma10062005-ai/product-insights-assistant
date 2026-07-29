"""
generate_data.py
-----------------
Produces a reproducible synthetic dataset for a SaaS-style product with a
weekly-active-usage model, stored in a real SQLite database (not flat
CSVs) so the project has an actual queryable database backing it.
Fixed random seed => identical output every run.

Baked-in finding (see README "The finding" section for the full story):
Paid Social is the *largest* acquisition channel by raw signup volume, which
is exactly why a top-of-funnel-only dashboard would flag it as the star
channel. But its post-signup activation rate and week-1 retention are the
worst of all five channels, and its churn compounds fastest. This is a
classic "vanity metric vs. real signal" trap that a Product Analyst is
expected to catch.

Output: data/product_insights.db (SQLite), containing two tables:
  users            one row per user
  weekly_activity  one row per (user, week_number), for weeks 0-15

Run:
  python data/generate_data.py
"""

import os
import sqlite3

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

SEED = 42
N_USERS = 50_000              # "large" dataset: 50k users
N_COHORT_WEEKS = 26            # signup cohorts span 26 weeks (half a year)
N_OBSERVED_WEEKS = 16           # every user is tracked for 16 weeks post-signup
DB_PATH = os.path.join(os.path.dirname(__file__), "product_insights.db")

# Channel parameters are the single source of truth for the baked-in finding.
# signup_share:      relative share of new signups (must sum to 1.0)
# activation_prob:   probability a user completes the key activation event
#                     (e.g. "created first project") in week 0
# week1_retention:   probability an *activated* user is still active in week 1
# weekly_decay:      multiplicative retention decay applied per additional
#                     week (models a geometric/leaky-bucket churn curve)
# pro_conversion:    probability a user is on the paid "Pro" plan
CHANNEL_PARAMS = {
    "Paid Social":       dict(signup_share=0.35, activation_prob=0.50, week1_retention=0.45, weekly_decay=0.80, pro_conversion=0.14),
    "Organic Search":    dict(signup_share=0.25, activation_prob=0.62, week1_retention=0.75, weekly_decay=0.90, pro_conversion=0.22),
    "Referral":          dict(signup_share=0.15, activation_prob=0.70, week1_retention=0.80, weekly_decay=0.92, pro_conversion=0.26),
    "Content Marketing": dict(signup_share=0.15, activation_prob=0.55, week1_retention=0.68, weekly_decay=0.88, pro_conversion=0.19),
    "Email":             dict(signup_share=0.10, activation_prob=0.58, week1_retention=0.70, weekly_decay=0.89, pro_conversion=0.20),
}

PRO_PRICE_PER_ACTIVE_WEEK = 999  # INR, arbitrary but realistic SaaS price point


def generate():
    rng = np.random.default_rng(SEED)

    channels = list(CHANNEL_PARAMS.keys())
    shares = np.array([CHANNEL_PARAMS[c]["signup_share"] for c in channels])
    assert abs(shares.sum() - 1.0) < 1e-9, "signup_share must sum to 1.0"

    user_channel = rng.choice(channels, size=N_USERS, p=shares)
    signup_week = rng.integers(0, N_COHORT_WEEKS, size=N_USERS)
    base_date = datetime(2025, 1, 6)  # an arbitrary Monday
    signup_date = [base_date + timedelta(weeks=int(w)) for w in signup_week]

    activated = np.zeros(N_USERS, dtype=bool)
    plan = np.empty(N_USERS, dtype=object)
    for i, ch in enumerate(user_channel):
        p = CHANNEL_PARAMS[ch]
        activated[i] = rng.random() < p["activation_prob"]
        plan[i] = "Pro" if rng.random() < p["pro_conversion"] else "Free"

    users = pd.DataFrame({
        "user_id": np.arange(1, N_USERS + 1),
        "signup_date": [d.strftime("%Y-%m-%d") for d in signup_date],
        "signup_week": signup_week,
        "channel": user_channel,
        "plan": plan,
        "activated": activated.astype(int),  # SQLite has no native bool; store as 0/1
    })

    # --- weekly activity (vectorized for speed at 50k users x 16 weeks) ---
    # An unactivated user never becomes active (there's nothing to retain).
    # An activated user is active in week 0 by definition (activation *is*
    # a week-0 event), then survives week-to-week per a geometric decay
    # curve seeded by the channel's week1_retention / weekly_decay params.
    # Once churned, a user stays churned (no resurrection) -- a simplifying
    # assumption documented in the README limitations.
    activity_matrix = np.zeros((N_USERS, N_OBSERVED_WEEKS), dtype=bool)
    for i in range(N_USERS):
        if not activated[i]:
            continue
        p = CHANNEL_PARAMS[user_channel[i]]
        activity_matrix[i, 0] = True
        alive = True
        for w in range(1, N_OBSERVED_WEEKS):
            retention_p = p["week1_retention"] if w == 1 else p["weekly_decay"]
            alive = alive and (rng.random() < retention_p)
            activity_matrix[i, w] = alive

    # Reshape the (N_USERS x N_OBSERVED_WEEKS) matrix into a long/tidy table
    user_ids_rep = np.repeat(users["user_id"].values, N_OBSERVED_WEEKS)
    week_numbers_rep = np.tile(np.arange(N_OBSERVED_WEEKS), N_USERS)
    weekly_activity = pd.DataFrame({
        "user_id": user_ids_rep,
        "week_number": week_numbers_rep,
        "active": activity_matrix.reshape(-1).astype(int),
    })

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)  # always regenerate clean, since the seed makes this deterministic anyway

    conn = sqlite3.connect(DB_PATH)
    try:
        users.to_sql("users", conn, if_exists="replace", index=False)
        weekly_activity.to_sql("weekly_activity", conn, if_exists="replace", index=False)
        # Indexes matter at this scale (50k users x 16 weeks = 800k activity rows)
        conn.execute("CREATE INDEX idx_users_channel ON users(channel)")
        conn.execute("CREATE INDEX idx_activity_user ON weekly_activity(user_id)")
        conn.execute("CREATE INDEX idx_activity_week ON weekly_activity(week_number)")
        conn.commit()
    finally:
        conn.close()

    print(f"Wrote {len(users):,} users and {len(weekly_activity):,} activity rows to {DB_PATH}")
    print("\nSignup share by channel:")
    print(users["channel"].value_counts(normalize=True).round(3))
    print("\nActivation rate by channel:")
    print(users.groupby("channel")["activated"].mean().round(3))


if __name__ == "__main__":
    generate()
