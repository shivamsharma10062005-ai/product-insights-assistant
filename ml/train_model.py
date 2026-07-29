"""
ml/train_model.py
-------------------
Trains a churn-risk classifier: "given a user's acquisition channel, plan,
and whether they were still active in week 1, what's the probability they
end up fully churned by week 4+?" This is a realistic "early warning"
framing -- week-1 engagement is a genuinely predictive signal product
teams use for early intervention, not just a restatement of the outcome.

Uses the exact same churn definition as the analytics layer
(analytics.metrics.compute_churn_labels) so the descriptive dashboard and
this predictive model can never silently disagree about what "churned"
means.

Output: ml/churn_model.joblib -- a dict bundle containing the trained
sklearn Pipeline, the feature column list, and evaluation metrics, so
ml/predict.py can load everything it needs from one file.

Run:
  python ml/train_model.py
"""

import os
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics.metrics import load_data, compute_churn_labels

MODEL_PATH = os.path.join(os.path.dirname(__file__), "churn_model.joblib")
SEED = 42
FEATURE_COLUMNS = ["channel", "plan", "week1_active"]
CATEGORICAL_COLUMNS = ["channel", "plan", "week1_active"]


def _build_training_frame(users: pd.DataFrame, activity: pd.DataFrame) -> pd.DataFrame:
    """Joins the churn label with the week-1-active engagement signal."""
    labeled = compute_churn_labels(users, activity, churn_after_week=4)
    week1 = activity.loc[activity["week_number"] == 1, ["user_id", "active"]].rename(
        columns={"active": "week1_active"}
    )
    df = labeled.merge(week1, on="user_id", how="left")
    df["week1_active"] = df["week1_active"].fillna(False)
    return df


def train():
    users, activity = load_data()
    df = _build_training_frame(users, activity)

    X = df[FEATURE_COLUMNS].astype(str)  # cast bool -> str so OneHotEncoder treats it as categorical
    y = df["churned"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=SEED, stratify=y
    )

    preprocessor = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLUMNS),
    ])
    model = Pipeline([
        ("preprocess", preprocessor),
        ("classifier", RandomForestClassifier(
            n_estimators=200, max_depth=6, random_state=SEED, n_jobs=-1
        )),
    ])
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    metrics = {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 4),
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "churn_rate_in_data": round(float(y.mean()), 4),
    }

    # Feature importance, mapped back to human-readable one-hot column names
    ohe = model.named_steps["preprocess"].named_transformers_["cat"]
    feature_names = ohe.get_feature_names_out(CATEGORICAL_COLUMNS)
    importances = model.named_steps["classifier"].feature_importances_
    importance_by_feature = sorted(
        zip(feature_names, importances), key=lambda t: t[1], reverse=True
    )
    top_features = [{"feature": f, "importance": round(float(i), 4)} for f, i in importance_by_feature[:8]]

    bundle = {
        "model": model,
        "feature_columns": FEATURE_COLUMNS,
        "metrics": metrics,
        "top_features": top_features,
        "channels": sorted(users["channel"].unique().tolist()),
        "plans": sorted(users["plan"].unique().tolist()),
    }
    joblib.dump(bundle, MODEL_PATH)

    print(f"Trained churn-risk model on {metrics['n_train']:,} users, tested on {metrics['n_test']:,}.")
    print(f"Accuracy: {metrics['accuracy']}   ROC-AUC: {metrics['roc_auc']}")
    print(f"Baseline churn rate in data: {metrics['churn_rate_in_data']}")
    print("Top feature importances:")
    for f in top_features:
        print(f"  {f['feature']}: {f['importance']}")
    print(f"Saved model bundle to {MODEL_PATH}")


if __name__ == "__main__":
    train()
