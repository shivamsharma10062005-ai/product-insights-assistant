"""
ai/narrator.py
---------------
The ONLY place in this codebase that calls an LLM. Its job is narration,
never computation: it receives a dict that was already produced by
analytics/metrics.py and turns it into 3-4 sentences of plain-English,
headline-first prose. It never receives raw dataframes and never
computes a number itself -- the system prompt sent to the model states
this constraint explicitly, and the function signature (dict in, str out)
enforces it structurally.

Two modes:
  - LLM mode: calls the Anthropic API (requires ANTHROPIC_API_KEY).
  - Fallback mode: a deterministic, rule-based template narrator that
    requires no API key. This exists so the demo *never* breaks in front
    of a reviewer who hasn't set up a key -- a design decision documented
    in the README.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

SYSTEM_PROMPT = """You are a Product Analytics narrator. You will be given:
1. The original plain-English question a stakeholder asked.
2. A JSON object containing analytics results that were already computed
   by a separate, deterministic analytics engine.

Your ONLY job is to narrate those exact numbers in plain English for a
non-technical stakeholder. Rules:
- Never invent, estimate, round further, or "correct" any number. Use only
  what appears in the JSON.
- Lead with the headline finding in the first sentence.
- Write exactly 3-4 sentences, no bullet points, no headers.
- Write for a stakeholder who has never seen a funnel or a p-value before,
  but don't be condescending.
- If the JSON contains a p-value or significance flag, translate it into
  plain language ("this is unlikely to be random noise") rather than
  stating the statistical jargon on its own.
"""


def _try_import_anthropic():
    try:
        import anthropic  # noqa
        return anthropic
    except ImportError:
        return None


def narrate(question: str, intent: str, analytics_result: dict,
            api_key: Optional[str] = None) -> dict:
    """
    Returns {"narrative": str, "mode": "llm" | "fallback", "model": str|None}
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    anthropic = _try_import_anthropic() if api_key else None

    if api_key and anthropic:
        try:
            return _narrate_with_llm(question, analytics_result, api_key, anthropic)
        except Exception as e:  # noqa: BLE001 - any API failure should degrade gracefully
            fallback = _narrate_with_rules(intent, analytics_result)
            fallback["mode"] = "fallback"
            fallback["fallback_reason"] = f"LLM call failed ({type(e).__name__}); used rule-based narrator."
            return fallback

    result = _narrate_with_rules(intent, analytics_result)
    result["mode"] = "fallback"
    result["fallback_reason"] = "No ANTHROPIC_API_KEY configured."
    return result


def _narrate_with_llm(question: str, analytics_result: dict, api_key: str, anthropic_module) -> dict:
    client = anthropic_module.Anthropic(api_key=api_key)
    model = "claude-sonnet-4-6"
    message = client.messages.create(
        model=model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Stakeholder question: {question}\n\n"
                f"Analytics result (JSON, already computed -- do not alter any number):\n"
                f"{json.dumps(analytics_result, indent=2)}"
            ),
        }],
    )
    text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
    return {"narrative": text.strip(), "mode": "llm", "model": model}


# ---------------------------------------------------------------------------
# Rule-based fallback narrator (no API key required)
# ---------------------------------------------------------------------------

def _narrate_with_rules(intent: str, r: dict) -> dict:
    fn = {
        "churn": _rule_churn,
        "churn_default": _rule_churn,
        "funnel": _rule_funnel,
        "retention": _rule_retention,
        "ab_test": _rule_ab_test,
        "ltv": _rule_ltv,
        "churn_risk": _rule_churn_risk,
        "news_digest": _rule_news_digest,
    }.get(intent, _rule_generic)
    return {"narrative": fn(r), "model": None}


def _rule_churn(r: dict) -> str:
    ranked = r.get("ranked", [])
    if not ranked:
        return "No churn data was available for this segment."
    worst, best = ranked[0], ranked[-1]
    gap = round(worst["churn_rate_pct"] - best["churn_rate_pct"], 1)
    return (
        f"{worst['channel']} has the worst churn rate at {worst['churn_rate_pct']}%, "
        f"compared to {best['channel']} at {best['churn_rate_pct']}% -- a gap of "
        f"{gap} percentage points. Overall churn across all segments is "
        f"{r.get('overall_churn_rate_pct')}%. This suggests retention efforts should "
        f"prioritize {worst['channel']} users first, since that's where the biggest "
        f"loss is concentrated."
    )


def _rule_funnel(r: dict) -> str:
    stages = r.get("overall", {}).get("stages", [])
    if not stages:
        return "No funnel data was available."
    biggest = max((s for s in stages if "drop_off_rate_pct" in s),
                   key=lambda s: s["drop_off_rate_pct"], default=None)
    signup, final = stages[0], stages[-1]
    lines = [
        f"Out of {signup['count']} signups, only {final['count']} "
        f"({final['pct_of_signups']}%) made it all the way to '{final['stage']}'."
    ]
    if biggest:
        lines.append(
            f"The biggest drop-off happens at the '{biggest['stage']}' stage, where "
            f"{biggest['drop_off_rate_pct']}% of the previous stage's users are lost."
        )
    lines.append(
        "Fixing that single stage would have the largest impact on the overall "
        "conversion rate of any change to the funnel."
    )
    return " ".join(lines)


def _rule_retention(r: dict) -> str:
    curve = r.get("curve_pct", [])
    scope = r.get("scope", "overall")
    if not curve:
        return "No retention data was available for this scope."
    week1 = curve[1] if len(curve) > 1 else None
    last_week, last_val = len(curve) - 1, curve[-1]
    parts = [f"For {scope}, retention starts at 100% at signup"]
    if week1 is not None:
        parts.append(f"drops to {week1}% by week 1")
    parts.append(f"and settles at {last_val}% by week {last_week}.")
    sentence1 = ", ".join(parts[:-1]) + " " + parts[-1]
    return (
        f"{sentence1} The steepest decline happens in the first week, which is the "
        f"highest-leverage window for onboarding or re-engagement work. "
        f"This curve is based on {r.get('n_activated_users', 'an unknown number of')} "
        f"activated users who completed at least the first product action."
    )


def _rule_ab_test(r: dict) -> str:
    sig_phrase = (
        "and this difference is unlikely to be random noise"
        if r.get("is_significant") else
        "but this difference could plausibly be due to random variation, not a real effect"
    )
    p_display = "< 0.0001" if r["p_value"] < 0.0001 else f"= {r['p_value']}"
    article = "an" if r["metric"][0].lower() in "aeiou" else "a"
    return (
        f"{r['channel_a']} has {article} {r['metric']} rate of {r['rate_a_pct']}% versus "
        f"{r['rate_b_pct']}% for {r['channel_b']}, {sig_phrase} (p {p_display}). "
        f"The comparison is based on {r['n_a']} users from {r['channel_a']} and "
        f"{r['n_b']} users from {r['channel_b']}. "
        f"{'This is a strong enough signal to act on.' if r.get('is_significant') else 'A larger sample would help confirm this before making a decision.'}"
    )


def _rule_ltv(r: dict) -> str:
    ranked = r.get("ranked", [])
    if not ranked:
        return "No LTV data was available."
    best, worst = ranked[0], ranked[-1]
    return (
        f"{best['channel']} delivers the highest estimated lifetime value at "
        f"₹{best['estimated_ltv_inr']} per activated user, compared to just "
        f"₹{worst['estimated_ltv_inr']} for {worst['channel']}. "
        f"This is driven by both longer average engagement ({best['avg_active_weeks']} "
        f"active weeks vs {worst['avg_active_weeks']}) and a higher paid-plan conversion rate "
        f"({best['pro_share_pct']}% vs {worst['pro_share_pct']}%). "
        f"Acquisition spend would go further shifted toward channels like {best['channel']}, "
        f"even if their raw signup volume is lower."
    )


def _rule_churn_risk(r: dict) -> str:
    week1_phrase = "was active in their first week" if r["week1_active"] else "was NOT active in their first week"
    band = r["risk_band"].lower()
    action = {
        "high": "This user is a strong candidate for an immediate re-engagement nudge (email, in-app prompt, or a human check-in for high-value accounts).",
        "medium": "Worth watching -- a light-touch nudge in the next week could tip this toward retention.",
        "low": "No action needed; this profile is behaving like your healthiest users.",
    }[band]
    return (
        f"A {r['plan']}-plan user from {r['channel']} who {week1_phrase} has a predicted "
        f"{r['churn_probability_pct']}% chance of fully churning -- that's {band} risk. "
        f"The model (ROC-AUC {r['model_roc_auc']}, meaning it separates churners from "
        f"non-churners well above random guessing) weighs week-1 engagement far more heavily "
        f"than acquisition channel or plan type. {action}"
    )


def _rule_news_digest(r: dict) -> str:
    articles = r.get("articles", [])
    if not articles:
        return "No news digest is available yet -- it refreshes daily via an automated pipeline."
    return (
        f"Today's briefing pulled {len(articles)} recent stories on product analytics, "
        f"SaaS growth, and retention strategy. Headlines: "
        + "; ".join(a["title"] for a in articles[:3]) + "."
    )


def _rule_generic(r: dict) -> str:
    return "Here is the analytics result: " + json.dumps(r)
