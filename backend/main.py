"""
backend/main.py
-----------------
FastAPI service exposing the analytics layer directly (for charts) and the
AI-narrated /ask endpoint (for the chat-style question box).

Architecture note: this file contains NO analytics logic and NO prompt
text of its own. It only validates input, calls into analytics/ and ai/,
and shapes HTTP responses/errors. If a reviewer wants to check "does the
LLM ever compute a number," this file (plus ai/narrator.py) is all they
need to read.
"""

import json
import os
import sqlite3
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics.metrics import (
    load_data,
    retention_curve,
    funnel_analysis,
    churn_by_segment,
    ab_significance_test,
    ltv_by_channel,
    DB_PATH,
)
from ai.router import route, CHANNELS, EXAMPLE_QUESTIONS
from ai.narrator import narrate
from ml.predict import predict_churn_risk, model_info, ModelNotTrainedError

app = FastAPI(
    title="AI-Powered Product Insights Assistant",
    description=(
        "Ask a plain-English product question and get back a data-backed "
        "answer. The analytics are always computed by deterministic Python "
        "(see /analytics source); the AI layer only narrates the result -- "
        "it never invents a number. See README for the full architecture."
    ),
    version="1.0.0",
)

# CORS: comma-separated list of allowed origins via env var, e.g.
#   ALLOWED_ORIGINS=https://your-app.streamlit.app,http://localhost:8501
# Defaults to "*" for zero-friction local development. Set this explicitly
# in any real deployment -- see DEPLOYMENT.md.
_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "*")
_allowed_origins = (
    ["*"] if _allowed_origins_env.strip() == "*"
    else [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_users_cache = None
_activity_cache = None


def get_data():
    """
    Lazily load + cache the CSVs so every request doesn't re-read disk.
    If the dataset hasn't been generated yet (e.g. fresh clone, no manual
    setup step run), generate it once automatically with the documented
    fixed-seed script -- this is what gives the "zero setup friction"
    clone-and-run experience without silently hiding the data pipeline.
    """
    global _users_cache, _activity_cache
    if _users_cache is None:
        try:
            _users_cache, _activity_cache = load_data()
        except FileNotFoundError:
            from data.generate_data import generate
            generate()
            _users_cache, _activity_cache = load_data()
    return _users_cache, _activity_cache


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    question: str
    intent: str
    matched_function: str
    analytics_result: dict
    narrative: str
    narration_mode: str
    model: Optional[str] = None
    fallback_reason: Optional[str] = None


@app.get("/health")
def health():
    """
    Lightweight liveness check for platform health checks (Render, Railway,
    etc.) that doesn't touch the dataset -- so it responds instantly even
    before the first /ask request triggers data generation.
    """
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "AI-Powered Product Insights Assistant",
        "docs": "/docs",
        "example_questions": EXAMPLE_QUESTIONS,
        "channels": CHANNELS,
    }


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    """
    Main chat-style endpoint: plain-English question in, data-backed
    narrated answer out. Internally: route() -> analytics function ->
    narrate(). The AI never sees raw data, only the analytics function's
    already-computed dict.
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty.")
    if len(question) > 500:
        raise HTTPException(status_code=400, detail="Question is too long (max 500 characters).")

    users, activity = get_data()
    rr = route(question)

    try:
        if rr.func_name == "retention_curve":
            result = retention_curve(users, activity, channel=rr.kwargs.get("channel"))
        elif rr.func_name == "funnel_analysis":
            result = funnel_analysis(users, activity)
        elif rr.func_name == "churn_by_segment":
            result = churn_by_segment(users, activity, segment_col=rr.kwargs.get("segment_col", "channel"))
        elif rr.func_name == "ab_significance_test":
            result = ab_significance_test(users, rr.kwargs["channel_a"], rr.kwargs["channel_b"])
        elif rr.func_name == "ltv_by_channel":
            result = ltv_by_channel(users, activity)
        else:
            raise HTTPException(status_code=500, detail=f"Unknown route target: {rr.func_name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    narration = narrate(question, rr.intent, result)

    return AskResponse(
        question=question,
        intent=rr.intent,
        matched_function=rr.func_name,
        analytics_result=result,
        narrative=narration["narrative"],
        narration_mode=narration["mode"],
        model=narration.get("model"),
        fallback_reason=narration.get("fallback_reason"),
    )


@app.get("/metrics/retention")
def get_retention(channel: Optional[str] = Query(None, description="Filter to one channel; omit for all channels")):
    users, activity = get_data()
    if channel and channel not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"Unknown channel. Valid channels: {CHANNELS}")
    return retention_curve(users, activity, channel=channel)


@app.get("/metrics/funnel")
def get_funnel():
    users, activity = get_data()
    return funnel_analysis(users, activity)


@app.get("/metrics/churn")
def get_churn(segment_col: str = Query("channel", description="Column to segment churn by")):
    users, activity = get_data()
    try:
        return churn_by_segment(users, activity, segment_col=segment_col)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/metrics/ltv")
def get_ltv():
    users, activity = get_data()
    return ltv_by_channel(users, activity)


@app.get("/metrics/ab_test")
def get_ab_test(
    channel_a: str = Query(..., description="First channel"),
    channel_b: str = Query(..., description="Second channel"),
    metric: str = Query("activated", description="Boolean column to compare, e.g. 'activated'"),
):
    users, _ = get_data()
    if channel_a not in CHANNELS or channel_b not in CHANNELS:
        raise HTTPException(status_code=400, detail=f"Unknown channel. Valid channels: {CHANNELS}")
    try:
        return ab_significance_test(users, channel_a, channel_b, metric=metric)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# ML: churn-risk prediction
# ---------------------------------------------------------------------------

class ChurnRiskRequest(BaseModel):
    channel: str
    plan: str
    week1_active: bool


@app.get("/predict/churn-risk/info")
def get_model_info():
    """Model metadata (accuracy, ROC-AUC, valid channels/plans, feature
    importances) so the frontend can build its form without hardcoding
    anything about the model."""
    try:
        return model_info()
    except ModelNotTrainedError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/predict/churn-risk")
def post_churn_risk(req: ChurnRiskRequest):
    try:
        prediction = predict_churn_risk(req.channel, req.plan, req.week1_active)
    except ModelNotTrainedError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    narration = narrate(
        f"Churn risk for a {req.plan} user from {req.channel}",
        "churn_risk",
        prediction,
    )
    return {
        "prediction": prediction,
        "narrative": narration["narrative"],
        "narration_mode": narration["mode"],
        "model": narration.get("model"),
    }


# ---------------------------------------------------------------------------
# News: daily digest (populated by news/fetch_news.py, run on a schedule
# by .github/workflows/daily_news.yml -- this endpoint only ever reads
# what's already in the database, it never calls NewsAPI itself)
# ---------------------------------------------------------------------------

@app.get("/news/digest")
def get_news_digest():
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=404, detail="Dataset not generated yet.")
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT fetched_date, digest_text, articles_json, created_at "
            "FROM news_digest ORDER BY fetched_date DESC LIMIT 1"
        )
        row = cur.fetchone()
    except sqlite3.OperationalError:
        row = None  # table doesn't exist yet -- fetch_news.py hasn't run
    finally:
        conn.close()

    if row is None:
        return {
            "available": False,
            "message": "No news digest yet. It refreshes daily via an automated "
                       "pipeline -- check back tomorrow, or run `python news/fetch_news.py` locally.",
        }

    fetched_date, digest_text, articles_json, created_at = row
    articles = json.loads(articles_json)
    narration = narrate("What's today's product/growth news briefing?", "news_digest",
                         {"articles": articles})
    return {
        "available": True,
        "fetched_date": fetched_date,
        "created_at": created_at,
        "digest_text": digest_text,
        "articles": articles,
        "narrative": narration["narrative"],
    }
