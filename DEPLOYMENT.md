# Deployment Guide

This gets the project from "runs on my laptop" to two live public URLs:

- **Backend (FastAPI)** on Render (free tier)
- **Frontend (Streamlit)** on Streamlit Community Cloud (free)

Both platforms deploy straight from a GitHub repo with no credit card and
no Docker required. Total time: ~15 minutes, most of it waiting for builds.

If you'd rather use Railway, Fly.io, or your own VPS instead of Render, skip
to [Alternative backend hosts](#alternative-backend-hosts) — the code
doesn't change, only the platform-specific setup steps do.

---

## 0. Prerequisites

- A GitHub account (free)
- A Render account (free, sign up with GitHub at render.com)
- A Streamlit Community Cloud account (free, sign up with GitHub at
  share.streamlit.io)
- Optional: an Anthropic API key from console.anthropic.com, if you want
  live Claude narration instead of the rule-based fallback

---

## 1. Push the code to GitHub

```bash
cd product-insights-assistant
git init
git add .
git commit -m "Initial commit: AI-powered product insights assistant"
```

Create a new **empty** repo on GitHub (no README/license, so there's
nothing to conflict with), then:

```bash
git branch -M main
git remote add origin https://github.com/<your-username>/product-insights-assistant.git
git push -u origin main
```

Double-check `.env` and `.streamlit/secrets.toml` are **not** in the repo
(`git status` should not show them — both are in `.gitignore`, and only
the `.example`/`.example` template versions should appear).

---

## 2. Deploy the backend (Render)

**Fastest path — Blueprint (uses the included `render.yaml`):**

1. In the Render dashboard, click **New +** → **Blueprint**.
2. Connect your GitHub account if you haven't, then select the
   `product-insights-assistant` repo.
3. Render detects `render.yaml` and shows a preview of one service,
   `product-insights-backend`. Click **Apply**.
4. Once it's provisioned, open the service → **Environment** tab and set:
   - `ANTHROPIC_API_KEY` — your key, if you want live narration (optional)
   - `ALLOWED_ORIGINS` — leave blank for now; you'll set this in step 4
     once you know your Streamlit URL
5. Wait for the first deploy to finish (2-4 minutes on the free tier).
   Render gives you a URL like `https://product-insights-backend.onrender.com`.

**Manual path (if you'd rather not use the Blueprint):**

1. **New +** → **Web Service** → select your repo.
2. Runtime: **Python 3**. Build command: `pip install -r requirements-backend.txt`.
   Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
3. Under **Health Check Path**, set `/health`.
4. Add the same environment variables as above.
5. Create Web Service.

**Verify it's live:**

```bash
curl https://<your-backend>.onrender.com/health
# {"status": "ok"}

curl -X POST https://<your-backend>.onrender.com/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Which channel has the worst churn?"}'
```

The first request after any period of inactivity will be slow (10-30s) —
this is Render's free-tier cold start, not a bug in the app. See
[Notes on free-tier limitations](#notes-on-free-tier-limitations) below.

---

## 3. Deploy the frontend (Streamlit Community Cloud)

1. Go to share.streamlit.io → **New app**.
2. Pick your repo, branch `main`, and set **Main file path** to
   `frontend/app.py`.
3. Before deploying, click **Advanced settings** and:
   - Set **Python version** to `3.12`.
   - Paste into the **Secrets** box:
     ```toml
     API_BASE_URL = "https://<your-backend>.onrender.com"
     ```
     (This is exactly the content of `.streamlit/secrets.toml.example`
     with the placeholder filled in — see that file for the format.)
4. Click **Deploy**. Streamlit installs from `requirements-frontend.txt`
   automatically if present at the repo root (it checks for this file
   before falling back to `requirements.txt`).
5. You'll get a URL like `https://your-app-name.streamlit.app`.

---

## 4. Lock down CORS (recommended, takes 1 minute)

Now that you know your Streamlit URL, go back to the Render dashboard →
your backend service → **Environment**, and set:

```
ALLOWED_ORIGINS=https://your-app-name.streamlit.app
```

This restricts the backend to only accept browser requests from your
deployed frontend instead of any origin (`*`, the local-dev default).
Save — Render will redeploy automatically. Confirm it took effect:

```bash
# Should show the access-control-allow-origin header:
curl -i -X POST https://<your-backend>.onrender.com/ask \
  -H "Content-Type: application/json" \
  -H "Origin: https://your-app-name.streamlit.app" \
  -d '{"question": "worst churn"}' | grep -i access-control

# Should show NO access-control-allow-origin header (request from an
# arbitrary site is not granted CORS access, even though curl itself can
# still reach the endpoint -- CORS is enforced by browsers, not curl):
curl -i -X POST https://<your-backend>.onrender.com/ask \
  -H "Content-Type: application/json" \
  -H "Origin: https://some-random-site.com" \
  -d '{"question": "worst churn"}' | grep -i access-control
```

---

## 4.5. Set up the daily news digest (optional)

The "Gotham Daily Briefing" panel refreshes once a day via a GitHub
Actions workflow, not by calling any API from the live app itself. Two
repo secrets make it work:

1. Get a free API key at **newsapi.org/register** (no credit card
   needed).
2. On your GitHub repo, go to **Settings → Secrets and variables →
   Actions → New repository secret**.
3. Add a secret named `NEWSAPI_KEY` with that key as the value.
4. Add a second secret named `ANTHROPIC_API_KEY` with your Anthropic key
   (optional — without it, the digest falls back to a plain headline
   list instead of a Claude-written summary).
5. To test it immediately instead of waiting for the next scheduled run:
   go to the **Actions** tab on your repo → click **daily-news-digest**
   in the left sidebar → click **Run workflow** → **Run workflow** again
   to confirm. Watch it run; when it finishes, it'll have committed an
   updated `data/product_insights.db` with today's digest, which
   triggers Render and Streamlit to redeploy automatically.
6. Check the **Daily Briefing** tab in your live app a couple of minutes
   later.

If a run fails, click into it in the Actions tab to see exactly which
step failed and why — most commonly a missing/incorrect `NEWSAPI_KEY`.

## 5. Done — verify the whole thing end to end

Open your Streamlit URL in a browser, click one of the example question
buttons, and confirm:
- A narrative answer appears (badge shows "rule-based fallback" unless you
  set `ANTHROPIC_API_KEY`).
- The chart beneath it renders.
- The "Explore all metrics" tabs at the bottom load charts too.

If something doesn't load, check the Streamlit app's logs (via "Manage
app" in the Streamlit Cloud dashboard) and the Render service's logs —
the error message from `call_api()` in `frontend/app.py` is designed to
tell you directly if it can't reach the backend at all.

---

## Notes on free-tier limitations

- **Render free tier spins down after 15 minutes of inactivity** and takes
  ~10-30 seconds to wake back up on the next request. This is normal —
  don't mistake it for the app being broken. For a resume/portfolio demo
  link you plan to share, consider pinging it (e.g. a free
  uptimerobot.com monitor hitting `/health` every 10 minutes) so it's
  warm when a recruiter clicks it, or upgrade to Render's cheapest paid
  tier if you want zero cold starts.
- **Streamlit Community Cloud apps sleep after a period of no visitors**
  too, and wake on the next visit with a similar delay.
- **Data resets on every Render redeploy/restart** (in-memory cache, CSVs
  regenerated fresh each cold start) since there's no persistent volume on
  the free tier. The fixed seed means the numbers are always identical, so
  this is invisible in practice — just worth knowing this isn't a
  database-backed service.

---

## Alternative backend hosts

The backend has no Render-specific code — `render.yaml` and the `Procfile`
are just platform config, not application logic. To use something else:

**Railway:** Railway auto-detects `requirements-backend.txt` and the
`Procfile`. Just connect the repo, set the same two environment variables
in Railway's dashboard, and deploy — no extra config file needed.

**Fly.io:** Fly.io wants a `fly.toml` and typically a Dockerfile. This
project intentionally has no Dockerfile (per the project brief, to avoid
unneeded complexity), so if you go this route, a minimal Dockerfile would
be:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements-backend.txt .
RUN pip install --no-cache-dir -r requirements-backend.txt
COPY . .
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

**Your own VPS:** `pip install -r requirements-backend.txt`, then run
under a process manager (systemd, supervisor, or `pm2`) with:
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```
and put it behind nginx/Caddy for TLS.

---

## Alternative frontend hosts

Streamlit apps can also run anywhere that hosts a long-lived Python
process (Render, Railway, a VPS) using:
```bash
streamlit run frontend/app.py --server.port $PORT --server.address 0.0.0.0
```
Streamlit Community Cloud is recommended here specifically because it's
purpose-built for exactly this kind of app and is free with no cold-start
cost tradeoffs beyond the sleep-on-inactivity behavior noted above.
