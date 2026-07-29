"""
ai/router.py
-------------
Routes a plain-English question to (a) one of the analytics functions and
(b) the arguments to call it with.

Design decision: keyword/intent matching, NOT LLM function-calling.
--------------------------------------------------------------------
An LLM could parse "which channel has the worst churn" into a function
call directly, and that's a legitimate upgrade path (noted in the README).
But it adds a second point where a hallucination or ambiguous parse could
silently produce the wrong analytics call -- and for the ~10 questions a
Product Analyst dashboard actually needs, a keyword router is:
  - 100% predictable (same question always maps to the same computation)
  - free (no API call needed just to figure out what to compute)
  - trivial to unit test and to explain in an interview

The tradeoff: it doesn't generalize to phrasings outside its keyword list.
That's an intentional, documented limitation for a v1 -- see README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

CHANNELS = ["Paid Social", "Organic Search", "Referral", "Content Marketing", "Email"]


@dataclass
class RouteResult:
    intent: str
    func_name: str
    kwargs: dict
    matched_channel: Optional[str] = None


def _find_channel(text: str) -> Optional[str]:
    text_low = text.lower()
    for ch in CHANNELS:
        if ch.lower() in text_low:
            return ch
    # a few common aliases
    aliases = {
        "paid social": "Paid Social", "social ads": "Paid Social", "paid ads": "Paid Social",
        "organic": "Organic Search", "seo": "Organic Search", "search": "Organic Search",
        "referrals": "Referral", "word of mouth": "Referral",
        "content": "Content Marketing", "blog": "Content Marketing",
        "email marketing": "Email", "newsletter": "Email",
    }
    for alias, canonical in aliases.items():
        if alias in text_low:
            return canonical
    return None


def route(question: str) -> RouteResult:
    """
    Returns which analytics function to call and with what arguments.
    Order matters: check more specific intents (funnel, ab-test, ltv)
    before falling back to the general churn/retention intents.
    """
    q = question.lower().strip()

    # --- A/B / statistical significance ---
    if any(kw in q for kw in ["significant", "significance", "a/b", "ab test", "compare", "vs", "versus"]):
        channels_mentioned = [ch for ch in CHANNELS if ch.lower() in q]
        if len(channels_mentioned) >= 2:
            return RouteResult("ab_test", "ab_significance_test",
                                {"channel_a": channels_mentioned[0], "channel_b": channels_mentioned[1]})
        # default comparison: the two channels most likely to be interesting
        return RouteResult("ab_test", "ab_significance_test",
                            {"channel_a": "Paid Social", "channel_b": "Referral"})

    # --- LTV ---
    if any(kw in q for kw in ["ltv", "lifetime value", "revenue", "worth"]):
        return RouteResult("ltv", "ltv_by_channel", {})

    # --- Funnel / drop-off ---
    if any(kw in q for kw in ["funnel", "drop off", "drop-off", "dropoff", "conversion", "stage"]):
        return RouteResult("funnel", "funnel_analysis", {})

    # --- Retention curve ---
    if any(kw in q for kw in ["retention", "retain", "stick", "week over week", "curve"]):
        ch = _find_channel(q)
        return RouteResult("retention", "retention_curve",
                            {"channel": ch}, matched_channel=ch)

    # --- Churn (default catch-all for "worst"/"churn"/"leaving") ---
    if any(kw in q for kw in ["churn", "worst", "leaving", "leave", "losing users", "drop out"]):
        return RouteResult("churn", "churn_by_segment", {"segment_col": "channel"})

    # Fallback: churn by segment is the single most "product analyst" default view
    return RouteResult("churn_default", "churn_by_segment", {"segment_col": "channel"})


EXAMPLE_QUESTIONS = [
    "Which channel has the worst churn?",
    "Show me the activation funnel and where people drop off",
    "What does the retention curve look like for Paid Social?",
    "Is the difference between Paid Social and Referral activation significant?",
    "Which channel has the best LTV?",
]
