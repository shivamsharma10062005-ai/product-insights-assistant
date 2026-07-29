# AI-Powered Product Insights Assistant

Ask a plain-English product question — *"Which channel has the worst
churn?"* — and get back a data-backed, stakeholder-ready answer: a real
number, computed by deterministic Python, explained in plain language by
Claude.

Built as a portfolio project targeting **Product Analyst** roles. It's
meant to demonstrate two skills at once: the analytics (retention, funnel,
churn, significance testing, LTV) and the engineering to wrap that in a
usable tool (API, tests, a UI a non-technical stakeholder could actually
use).

---

## The finding baked into the data

The synthetic dataset (**50,000 users**, 800,000 weekly-activity rows, 5
acquisition channels, 16 weeks of activity, stored in a real SQLite
database) has one deliberate story: **Paid Social is the largest channel
by raw signup volume (~35% of all signups) but has the worst activation
rate, the worst churn rate, and the lowest estimated LTV of any channel.**
Referral, by contrast, is the smallest channel by volume but the
healthiest on every downstream metric. A dashboard that only reports
top-of-funnel signups would call Paid Social the star channel; a Product
Analyst who looks one layer deeper would flag it as the channel quietly
burning the most acquisition spend. That's the gap this tool is built to
surface.

---

## Beyond descriptive analytics: two extensions

### Churn-risk ML model (predictive layer)

The existing analytics answer "what happened" (churn rates, funnels). The
churn-risk model in `ml/` answers "who's likely to churn *next*": a Random
Forest classifier that predicts a user's probability of fully churning by
week 4, using their acquisition channel, plan, and whether they were
active in week 1. Week-1 engagement is a realistic early-warning signal
teams use for targeted intervention — not just a restatement of the
outcome, since a stochastic decay curve still separates week-1 activity
from the final churn label.

Trained on 23,146 users / tested on 5,787: **ROC-AUC 0.857, accuracy
0.794**. It follows the same architectural boundary as the rest of the
project: `ml/predict.py` returns a plain dict (probability, risk band,
feature importances), and `ai/narrator.py` explains it in words — the
model computes the number, Claude never does.

Retrain any time with `python ml/train_model.py` (uses the same
`compute_churn_labels()` function the descriptive churn analytics use, so
the dashboard and the model can never silently disagree about what
"churned" means).

### Daily news digest

A small "Gotham Daily Briefing" panel pulls recent product-analytics/SaaS-
growth headlines via NewsAPI.org and has Claude summarize them into a
3-4 bullet digest. This refreshes automatically once a day via a free
GitHub Actions workflow (`.github/workflows/daily_news.yml`) — Render's
free tier has no built-in cron, so the Action runs `news/fetch_news.py`
on a schedule and commits the updated database back to the repo, which
triggers Render and Streamlit to redeploy with fresh data.

**Setup required:** get a free key at newsapi.org, then add it as a repo
secret named `NEWSAPI_KEY` (Settings → Secrets and variables → Actions →
New repository secret). `ANTHROPIC_API_KEY` as a secret too, if you want
Claude-written digests instead of a plain headline list. See
`DEPLOYMENT.md` for exact steps.

**Honest caveat on NewsAPI's free tier:** it's explicitly licensed "for
development only," with a 24-hour article delay and CORS restricted to
localhost. The CORS restriction only affects direct browser calls; since
this project fetches server-side (a GitHub Actions job, not the user's
browser), it isn't blocked technically — but it's worth knowing this
isn't a production-grade commercial integration, just the intended use
for a personal/portfolio project.

## Architecture

```
┌─────────────────┐      plain-English question
│   Frontend       │ ───────────────────────────────┐
│   (Streamlit)    │                                 │
└────────┬─────────┘                                 ▼
         │ HTTP                              ┌───────────────┐
         ▼                                   │  ai/router.py │
┌──────────────────┐   POST /ask             │  keyword →    │
│    Backend        │◀────────────────────── │  intent +     │
│   (FastAPI)        │                        │  function     │
│                    │                        └───────┬───────┘
│  GET /metrics/*    │                                │
└────────┬───────────┘                                ▼
         │                                   ┌───────────────────┐
         │  calls with plain DataFrames      │ analytics/metrics │
         ├──────────────────────────────────▶│  .py               │
         │                                   │  pure pandas,      │
         │                                   │  no AI, no I/O     │
         │                                   │  beyond CSV load   │
         │                                   └─────────┬──────────┘
         │                                              │ returns a dict
         │                                              │ of already-computed
         │                                              │ numbers
         │                                              ▼
         │                                   ┌────────────────────┐
         └──────────────────────────────────▶│  ai/narrator.py    │
           dict (never raw data) ────────────▶│  LLM narrates the  │
                                              │  dict, OR a rule-  │
                                              │  based fallback if │
                                              │  no API key        │
                                              └────────────────────┘

data/generate_data.py  →  data/raw/{users,weekly_activity}.csv
                          (fixed seed = 42, regenerate any time)
```

**The one architectural fact worth remembering:** `ai/narrator.py` is the
only file in this repo that talks to an LLM, and its function signature is
`dict in → string out`. It is never given a DataFrame, a CSV path, or
credentials to compute anything itself. If a number is wrong, the bug is in
`analytics/metrics.py`, not in a prompt — and that's testable and provable,
not just asserted in this README.

### Repo layout

```
product-insights-assistant/
├── README.md
├── DEPLOYMENT.md              # step-by-step guide to a live public deploy
├── requirements.txt           # full set, for local dev (backend+frontend+tests)
├── requirements-backend.txt   # backend-only, used by Render/Railway
├── requirements-frontend.txt  # frontend-only, used by Streamlit Cloud
├── Procfile                   # start command for Render/Railway
├── render.yaml                # Render Blueprint (one-click backend deploy)
├── runtime.txt / .python-version   # pinned Python version for platform builds
├── .env.example
├── .gitignore
├── .streamlit/
│   ├── config.toml            # theme, applies locally and on Streamlit Cloud
│   └── secrets.toml.example   # template for API_BASE_URL on Streamlit Cloud
├── .github/workflows/tests.yml # CI: runs pytest on every push/PR
├── data/
│   ├── generate_data.py       # reproducible dataset generator, seed=42
│   └── product_insights.db    # 50k-user SQLite database (committed, ~29MB)
├── analytics/
│   ├── metrics.py             # retention, funnel, churn, A/B test, LTV
│   └── tests/
│       └── test_metrics.py    # 16 unit tests, hand-computed fixtures
├── ai/
│   ├── router.py              # plain-English question -> analytics call
│   └── narrator.py            # dict -> 3-4 sentence narrative (LLM or fallback)
├── ml/
│   ├── train_model.py         # trains the churn-risk classifier
│   ├── predict.py             # loads the model, serves predictions
│   └── churn_model.joblib     # trained model bundle (committed, ~440KB)
├── news/
│   └── fetch_news.py          # daily news fetch + Claude-written digest
├── backend/
│   └── main.py                # FastAPI: /ask, /health, /metrics/{...}, /predict/churn-risk, /news/digest
└── frontend/
    └── app.py                 # Streamlit single-page app
```

---

## Deploying it live

Want a public URL to put on a resume or LinkedIn? See **[DEPLOYMENT.md](./DEPLOYMENT.md)**
for a full walkthrough: push to GitHub, deploy the backend to Render
(free, uses the included `render.yaml`), deploy the frontend to Streamlit
Community Cloud (free), then lock down CORS between the two. About
15 minutes end to end, no Docker or credit card required.

---

## Setup (clean clone to running app)

Tested with **Python 3.12** and **Streamlit 1.60 / FastAPI 0.140** (any
recent Python 3.10+ and current versions of these packages should work —
no pinned exact versions, see `requirements.txt`).

```bash
git clone <your-repo-url>
cd product-insights-assistant

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt

# Optional: enable real Claude narration (otherwise runs on rule-based fallback)
cp .env.example .env
# then edit .env and add your ANTHROPIC_API_KEY

# Terminal 1 -- backend (auto-generates data/raw/*.csv on first request)
uvicorn backend.main:app --reload

# Terminal 2 -- frontend
streamlit run frontend/app.py
```

Open the Streamlit URL it prints (default `http://localhost:8501`). The
backend's interactive API docs are at `http://localhost:8000/docs`.

You do **not** need an Anthropic API key to run the full demo — the
`/ask` endpoint and the UI work identically either way, with a small badge
showing whether the current answer came from Claude or the fallback
narrator. If you do want live narration, load `.env` with `python-dotenv`
or `export $(cat .env | xargs)` before starting uvicorn (no dotenv
dependency was added to keep the requirements list minimal).

To regenerate the dataset manually (e.g. after changing a channel
parameter in `generate_data.py`):

```bash
python data/generate_data.py
```

### Running the tests

```bash
pytest analytics/tests/ -v
```

---

## Design decisions

**1. Keyword routing instead of LLM function-calling.**
The AI layer maps a question to an analytics function with a keyword/intent
matcher (`ai/router.py`), not by asking an LLM to pick the function call.
For the ~5-10 question types a Product Analyst dashboard actually needs,
this is more predictable (the same question always maps to the same
computation), free (no API call just to figure out *what* to compute), and
trivially unit-testable. The real tradeoff: it doesn't generalize to
phrasings outside its keyword list, and a genuinely open-ended assistant
would eventually need LLM function-calling or a hybrid (keyword match first,
LLM fallback for anything unmatched). That upgrade path is straightforward
to add on top of the current router without touching the analytics layer.

**2. A no-API-key fallback narrator, not just an error state.**
`ai/narrator.py` has a full rule-based narrator that runs whenever
`ANTHROPIC_API_KEY` is unset *or* the API call fails for any reason
(network, rate limit, bad key). This was a deliberate call: a demo that
returns a 500 error to a reviewer who cloned the repo without setting up
billing is a worse first impression than a demo that quietly narrates with
templates. The UI always shows which mode produced the current answer, so
this isn't hidden — it's disclosed.

**3. Streamlit over React for the frontend.**
This project's evaluation criteria (per the brief) explicitly deprioritize
frontend polish below the analytics, backend, and AI/documentation layers.
Streamlit gets a working, chart-rich UI in a fraction of the code a
React + Vite + component-library stack would need, which means more of the
available time went into getting the retention/churn/funnel math and the
narration layer right instead of writing fetch-and-state boilerplate. The
honest cost: Streamlit's default look is recognizable, and a
production-facing tool for external stakeholders would likely warrant React
for finer control over interaction design. For an internal analyst tool or
a portfolio piece, that tradeoff favors Streamlit.

**4. Churn defined as "no activity in any week from week 4 onward," not "inactive this week."**
A single quiet week is normal usage, not churn. Defining churn as
sustained inactivity through the end of the observation window
(`churn_after_week=4` by default) avoids overcounting people who are still
coming back periodically. This is a modeling choice, not a fact, and it's
parameterized (`churn_after_week` is an argument to
`churn_by_segment()`) precisely so it can be argued with in an interview.

---

## Known limitations / what I'd add next

Ordered from most to least important to fix:

- **No survival-analysis / censoring handling.** Every synthetic user has a
  full 12-week observation window baked in by the data generator. A real
  product dataset has users who signed up last week and simply haven't had
  time to reach week 4 yet — that requires cohort-aware censoring (e.g.
  Kaplan-Meier) that this v1 deliberately skips to keep the analytics layer
  readable.
- **Router is keyword-based, not LLM function-calling.** Documented as a
  design decision above, but the honest limitation is that a sufficiently
  creatively-phrased question (e.g. "are we bleeding users from ads?") may
  not match any keyword and will silently fall through to the churn
  default rather than erroring loudly. A hybrid router (keyword match →
  LLM function-calling fallback for unmatched questions) is the natural v2.
- **LTV model is a back-of-envelope proxy**, not a real LTV model: no
  discounting, no expansion revenue, no non-Pro monetization paths (ads,
  add-ons), and it treats "active weeks so far" as a stand-in for
  lifetime rather than modeling forward churn probability.
- **Frontend polish was intentionally descoped** per the stated priority
  order: no animation, no mobile-specific layout testing, no auth. This
  was a conscious tradeoff to protect time for the analytics/backend/AI
  layers, per this project's own priority order.
- **No persistence/database.** Data lives in two CSVs loaded into memory
  once per backend process. Fine for a synthetic demo dataset; a real
  version would need a proper warehouse query layer (e.g. dbt + a
  Postgres/warehouse table) instead of `pandas.read_csv`.
- **CORS defaults to wide open (`allow_origins=["*"]`)** for local-demo
  convenience, but is configurable via the `ALLOWED_ORIGINS` env var
  (comma-separated origins) for any real deployment -- see `DEPLOYMENT.md`
  step 4 for how this gets locked down to just the deployed frontend's URL.

---

## Why this maps to Product Analyst skills

- **Retention curves, funnel drop-off, churn segmentation, and a two-
  proportion significance test** are the exact toolkit a Product Analyst
  uses to answer "is this channel/feature/cohort actually different, or
  is that noise?" — not just dashboard-building, but the statistical
  judgment behind it (see `ab_significance_test`, implemented directly with
  the normal approximation rather than an opaque library call).
- **Stating assumptions and defaults explicitly** (the churn-after-week
  parameter, the funnel stage definitions, the LTV proxy formula) mirrors
  the real job: most "what's our churn rate?" questions from a stakeholder
  require an analyst to first decide what churn *means* before computing
  anything.
- **Building the "AI narrates, never invents" boundary into the code
  structure** (not just describing it) reflects a skill increasingly
  relevant to analyst roles at banks and fintechs: knowing where an LLM is
  safe to use (turning numbers into stakeholder language) and where it
  categorically isn't (deciding what the numbers are).
