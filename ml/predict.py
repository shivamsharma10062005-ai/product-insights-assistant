"""
ml/predict.py
---------------
Loads the trained churn-risk model bundle and serves predictions. Like
analytics/metrics.py, this module returns a plain dict -- the AI narration
layer (ai/narrator.py) explains the prediction in words, it never computes
one itself. Same "AI narrates, never invents" boundary, extended to the
predictive layer.
"""

import os
from functools import lru_cache

import joblib
import pandas as pd

MODEL_PATH = os.path.join(os.path.dirname(__file__), "churn_model.joblib")


class ModelNotTrainedError(Exception):
    pass


@lru_cache(maxsize=1)
def _load_bundle():
    if not os.path.exists(MODEL_PATH):
        raise ModelNotTrainedError(
            "Churn-risk model not found. Run `python ml/train_model.py` first "
            "to generate ml/churn_model.joblib."
        )
    return joblib.load(MODEL_PATH)


def model_info() -> dict:
    """Metadata about the currently loaded model -- metrics, valid input
    values, and feature importances -- so the frontend can build dropdowns
    and show model quality without hardcoding anything."""
    bundle = _load_bundle()
    return {
        "metrics": bundle["metrics"],
        "top_features": bundle["top_features"],
        "channels": bundle["channels"],
        "plans": bundle["plans"],
    }


def predict_churn_risk(channel: str, plan: str, week1_active: bool) -> dict:
    bundle = _load_bundle()
    if channel not in bundle["channels"]:
        raise ValueError(f"Unknown channel '{channel}'. Valid channels: {bundle['channels']}")
    if plan not in bundle["plans"]:
        raise ValueError(f"Unknown plan '{plan}'. Valid plans: {bundle['plans']}")

    X = pd.DataFrame([{
        "channel": channel,
        "plan": plan,
        "week1_active": str(week1_active),
    }]).astype(str)

    proba = float(bundle["model"].predict_proba(X)[0, 1])
    if proba >= 0.66:
        risk_band = "High"
    elif proba >= 0.33:
        risk_band = "Medium"
    else:
        risk_band = "Low"

    return {
        "channel": channel,
        "plan": plan,
        "week1_active": week1_active,
        "churn_probability_pct": round(proba * 100, 1),
        "risk_band": risk_band,
        "model_roc_auc": bundle["metrics"]["roc_auc"],
        "top_features": bundle["top_features"][:3],
    }
