"""
frontend/app.py
-----------------
Single-page Streamlit app, Gotham-themed. Talks to the FastAPI backend over
HTTP only -- no analytics logic lives here. This keeps the "AI never
invents a number" guarantee visible in the code structure: this file only
renders whatever JSON the backend returns.

Design note on the theme: the 3D hero and decorative bat motifs use an
original, hand-authored bat silhouette (a generic winged-bat shape), not
DC Comics' trademarked bat-signal logo or any copyrighted artwork -- so
the aesthetic is Batman-*inspired* without reproducing licensed IP.

Framework choice: Streamlit over React. See README "Design decisions" for
the full justification.
"""

import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components


def _get_api_base() -> str:
    """
    Resolution order: env var (local dev, Docker, most PaaS) -> Streamlit
    secrets.toml (Streamlit Community Cloud's config mechanism) -> localhost
    default. Wrapped in try/except because st.secrets raises if no
    secrets.toml exists at all, which is the normal case for local dev.
    """
    env_val = os.environ.get("API_BASE_URL")
    if env_val:
        return env_val
    try:
        return st.secrets["API_BASE_URL"]
    except Exception:
        return "http://127.0.0.1:8000"


API_BASE = _get_api_base()

# --- Gotham palette (token system) ---------------------------------------
INK = "#07070A"          # near-black page background
PANEL = "#131318"        # card / panel background
PANEL_BORDER = "#2A2A33"  # default card border
GOLD = "#F2C94C"         # bat-signal yellow -- headline accent
STEEL = "#8D99AE"        # steel-grey secondary accent
ALARM = "#E63946"        # alert red -- worst metrics
FOG = "#6E7180"          # muted text
PAPER = "#E7E7EC"        # primary text on dark

st.set_page_config(page_title="Gotham Product Insights", page_icon="\U0001F987", layout="wide")

# ---------------------------------------------------------------------------
# Global CSS: dark Gotham theme + pure-CSS 3D tilt on cards (no cross-iframe
# JS needed -- Streamlit's markdown script execution is unreliable across
# versions, but CSS transforms on elements we render ourselves work every
# time).
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
.stApp {{
    background: radial-gradient(ellipse 120% 80% at 50% -10%, #1a1a22 0%, {INK} 55%);
    color: {PAPER};
}}
[data-testid="stHeader"] {{ background-color: transparent; }}
h1, h2, h3 {{
    color: {PAPER};
    font-family: 'IBM Plex Sans', 'Segoe UI', sans-serif;
    letter-spacing: -0.01em;
}}
h1 {{ text-shadow: 0 0 18px rgba(242, 201, 76, 0.25); }}

/* pulsing bat-signal glow, pure CSS, no JS needed */
@keyframes signal-pulse {{
    0%, 100% {{ opacity: 0.35; }}
    50% {{ opacity: 0.65; }}
}}
.bat-watermark {{
    position: fixed; top: -80px; right: -80px; width: 420px; height: 420px;
    opacity: 0.06; z-index: 0; pointer-events: none;
    animation: signal-pulse 6s ease-in-out infinite;
}}

.headline-number {{
    font-size: 2.6rem; font-weight: 700; color: {GOLD};
    font-family: 'IBM Plex Mono', monospace;
}}

/* the 3D tilt card -- perspective lives on the wrapper, rotation on hover */
.card-wrap {{ perspective: 900px; margin-bottom: 1rem; }}
.card {{
    background: linear-gradient(160deg, {PANEL} 0%, #0e0e13 100%);
    border: 1px solid {PANEL_BORDER};
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    transform-style: preserve-3d;
    transition: transform 0.35s cubic-bezier(0.2, 0.8, 0.2, 1), border-color 0.35s, box-shadow 0.35s;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}}
.card:hover {{
    transform: perspective(900px) rotateX(3deg) rotateY(-2deg) translateY(-4px) scale(1.01);
    border-color: rgba(242, 201, 76, 0.5);
    box-shadow: 0 16px 40px rgba(0,0,0,0.55), 0 0 24px rgba(242, 201, 76, 0.12);
}}
.eyebrow {{
    color: {FOG}; text-transform: uppercase; font-size: 0.75rem;
    letter-spacing: 0.12em; margin-bottom: 0.35rem; font-weight: 600;
}}
.narrative-text {{ font-size: 1.05rem; line-height: 1.6; color: {PAPER}; }}
.mode-badge {{
    display: inline-block; padding: 0.15rem 0.65rem; border-radius: 999px;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; margin-left: 0.6rem;
    text-transform: uppercase;
}}
.mode-llm {{ background: rgba(242, 201, 76, 0.15); color: {GOLD}; border: 1px solid rgba(242,201,76,0.35); }}
.mode-fallback {{ background: rgba(141, 153, 174, 0.15); color: {STEEL}; border: 1px solid rgba(141,153,174,0.35); }}

div[data-testid="stChatInput"] {{
    background-color: {PANEL}; border: 1px solid {PANEL_BORDER}; border-radius: 10px;
}}

.stButton button {{
    background-color: {PANEL} !important;
    border: 1px solid {PANEL_BORDER} !important;
    color: {PAPER} !important;
    border-radius: 8px !important;
    transition: all 0.25s ease !important;
}}
.stButton button:hover {{
    border-color: {GOLD} !important;
    color: {GOLD} !important;
    box-shadow: 0 0 16px rgba(242, 201, 76, 0.25) !important;
}}

.stTabs [data-baseweb="tab"] {{ color: {FOG}; }}
.stTabs [aria-selected="true"] {{ color: {GOLD} !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {GOLD} !important; }}

hr {{ border-color: {PANEL_BORDER} !important; }}
</style>

<svg class="bat-watermark" viewBox="0 0 200 100" xmlns="http://www.w3.org/2000/svg">
    <path fill="{GOLD}" d="M100,38
        C96,28 90,20 82,16 C84,24 84,30 86,36
        C68,20 46,14 20,16 C34,22 46,30 54,40
        C36,36 18,38 2,48 C20,46 36,50 48,58
        C34,60 22,68 14,80 C28,72 42,68 56,68
        C52,76 52,84 56,92 C60,82 66,74 74,70
        C80,78 88,84 100,88
        C112,84 120,78 126,70
        C134,74 140,82 144,92
        C148,84 148,76 144,68
        C158,68 172,72 186,80
        C178,68 166,60 152,58
        C164,50 180,46 198,48
        C182,38 164,36 146,40
        C154,30 166,22 180,16
        C154,14 132,20 114,36
        C116,30 116,24 118,16
        C110,20 104,28 100,38 Z"/>
</svg>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 3D hero: a rotating extruded bat silhouette rendered with Three.js inside
# its own sandboxed component. This is the one place real WebGL/JS makes
# sense here, since the component doesn't need to reach outside its iframe.
# ---------------------------------------------------------------------------
def render_hero():
    html = f"""
    <div id="hero" style="width:100%; height:300px; position:relative; overflow:hidden; border-radius:14px;
         background: radial-gradient(ellipse at 50% 30%, #1c1c24 0%, #07070a 75%); border:1px solid {PANEL_BORDER};">
      <canvas id="bat-canvas" style="width:100%; height:100%; display:block;"></canvas>
      <div style="position:absolute; bottom:18px; left:0; right:0; text-align:center; pointer-events:none;">
        <div style="font-family: 'IBM Plex Mono', monospace; letter-spacing: 0.3em; color:{GOLD};
             font-size: 0.8rem; text-shadow: 0 0 18px rgba(242,201,76,0.6);">GOTHAM PRODUCT INSIGHTS DIVISION</div>
      </div>
    </div>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script>
      (function() {{
        const canvas = document.getElementById('bat-canvas');
        const container = document.getElementById('hero');
        const renderer = new THREE.WebGLRenderer({{ canvas: canvas, alpha: true, antialias: true }});
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
        camera.position.set(0, 0, 34);

        function resize() {{
          const w = container.clientWidth, h = container.clientHeight;
          renderer.setSize(w, h, false);
          camera.aspect = w / h;
          camera.updateProjectionMatrix();
        }}

        // Build an original bat silhouette (generic winged shape, not any
        // trademarked logo) as a 2D shape, then extrude it into 3D.
        const shape = new THREE.Shape();
        shape.moveTo(0, 6);
        shape.bezierCurveTo(-1.5, 10, -4, 13, -7, 15);
        shape.bezierCurveTo(-6, 11, -6, 8, -5, 5);
        shape.bezierCurveTo(-16, 11, -28, 12, -40, 8);
        shape.bezierCurveTo(-30, 3, -20, -1, -12, -3);
        shape.bezierCurveTo(-24, -5, -34, -10, -42, -19);
        shape.bezierCurveTo(-28, -16, -16, -13, -6, -8);
        shape.bezierCurveTo(-9, -13, -10, -19, -9, -25);
        shape.bezierCurveTo(-4, -18, -1, -12, 0, -6);
        shape.bezierCurveTo(1, -12, 4, -18, 9, -25);
        shape.bezierCurveTo(10, -19, 9, -13, 6, -8);
        shape.bezierCurveTo(16, -13, 28, -16, 42, -19);
        shape.bezierCurveTo(34, -10, 24, -5, 12, -3);
        shape.bezierCurveTo(20, -1, 30, 3, 40, 8);
        shape.bezierCurveTo(28, 12, 16, 11, 5, 5);
        shape.bezierCurveTo(6, 8, 6, 11, 7, 15);
        shape.bezierCurveTo(4, 13, 1.5, 10, 0, 6);

        const geometry = new THREE.ExtrudeGeometry(shape, {{
          depth: 3, bevelEnabled: true, bevelThickness: 0.6, bevelSize: 0.5, bevelSegments: 3, curveSegments: 12
        }});
        geometry.center();

        const material = new THREE.MeshStandardMaterial({{
          color: 0x101013, metalness: 0.55, roughness: 0.35, emissive: 0x030302
        }});
        const bat = new THREE.Mesh(geometry, material);
        scene.add(bat);

        scene.add(new THREE.AmbientLight(0x404050, 1.2));
        const rim = new THREE.PointLight(0xf2c94c, 2.4, 120);
        rim.position.set(-30, 20, 30);
        scene.add(rim);
        const rim2 = new THREE.PointLight(0x8d99ae, 1.0, 120);
        rim2.position.set(30, -10, 20);
        scene.add(rim2);

        let targetX = 0, targetY = 0;
        container.addEventListener('mousemove', (e) => {{
          const rect = container.getBoundingClientRect();
          targetX = ((e.clientX - rect.left) / rect.width - 0.5) * 0.6;
          targetY = ((e.clientY - rect.top) / rect.height - 0.5) * 0.4;
        }});

        resize();
        window.addEventListener('resize', resize);

        function animate() {{
          requestAnimationFrame(animate);
          bat.rotation.y += 0.006;
          bat.rotation.x += (targetY - bat.rotation.x) * 0.03;
          bat.rotation.z += (-targetX - bat.rotation.z) * 0.03 - bat.rotation.z * 0.0 + 0;
          renderer.render(scene, camera);
        }}
        animate();
      }})();
    </script>
    """
    components.html(html, height=300)


def call_api(method: str, path: str, **kwargs):
    try:
        resp = requests.request(method, f"{API_BASE}{path}", timeout=90, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.ConnectionError:
        return None, (
            f"Can't reach the backend at {API_BASE}. Start it with:\n\n"
            f"`uvicorn backend.main:app --reload`"
        )
    except requests.exceptions.Timeout:
        return None, (
            "The backend is taking a while to respond -- likely waking up "
            "from sleep on a free hosting tier. Please wait a few seconds "
            "and try again."
        )
    except requests.exceptions.HTTPError as e:
        # The server may return an HTML error page (e.g. a host's own
        # "waking up" or "suspended" page) instead of JSON -- .json() would
        # raise its own exception in that case, so fall back safely.
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            status = e.response.status_code if e.response is not None else "?"
            detail = f"HTTP {status} (non-JSON response -- the server may be restarting; try again shortly)"
        return None, f"API error: {detail}"
    except Exception as e:  # noqa: BLE001
        return None, f"Unexpected error: {e}"


CHART_COLORS = [GOLD, STEEL, "#5B8DEF", ALARM, "#9B7FD6"]


def render_narrative_card(data: dict):
    mode = data.get("narration_mode", "fallback")
    badge_class = "mode-llm" if mode == "llm" else "mode-fallback"
    badge_text = f"Claude ({data.get('model')})" if mode == "llm" else "rule-based fallback"
    st.markdown(f"""
    <div class="card-wrap"><div class="card">
        <div class="eyebrow">Case File <span class="mode-badge {badge_class}">{badge_text}</span></div>
        <div class="narrative-text">{data['narrative']}</div>
    </div></div>
    """, unsafe_allow_html=True)
    if data.get("fallback_reason"):
        st.caption(f"\U0001F987 {data['fallback_reason']}")


def render_chart_for(intent: str, result: dict):
    if intent in ("churn", "churn_default"):
        df = pd.DataFrame(result["ranked"])
        fig = go.Figure(go.Bar(
            x=df["churn_rate_pct"], y=df["channel"], orientation="h",
            marker_color=[ALARM if v == df["churn_rate_pct"].max() else STEEL for v in df["churn_rate_pct"]],
            text=df["churn_rate_pct"].astype(str) + "%", textposition="outside",
        ))
        fig.update_layout(title="Churn rate by channel", xaxis_title="Churn rate (%)")
        _themed(fig)
        st.plotly_chart(fig, use_container_width=True)

    elif intent == "funnel":
        stages = result["overall"]["stages"]
        df = pd.DataFrame(stages)
        fig = go.Figure(go.Funnel(y=df["stage"], x=df["count"], marker={"color": CHART_COLORS}))
        fig.update_layout(title="Overall activation funnel")
        _themed(fig)
        st.plotly_chart(fig, use_container_width=True)

    elif intent == "retention":
        if "by_channel" in result:
            fig = go.Figure()
            for i, (ch, chd) in enumerate(result["by_channel"].items()):
                fig.add_trace(go.Scatter(y=chd["curve_pct"], mode="lines+markers",
                                          name=ch, line=dict(color=CHART_COLORS[i % len(CHART_COLORS)])))
            fig.update_layout(title="Retention curve by channel", xaxis_title="Week", yaxis_title="% still active")
        else:
            fig = go.Figure(go.Scatter(y=result["curve_pct"], mode="lines+markers", line=dict(color=GOLD)))
            fig.update_layout(title=f"Retention curve — {result['scope']}", xaxis_title="Week", yaxis_title="% still active")
        _themed(fig)
        st.plotly_chart(fig, use_container_width=True)

    elif intent == "ab_test":
        fig = go.Figure(go.Bar(
            x=[result["channel_a"], result["channel_b"]],
            y=[result["rate_a_pct"], result["rate_b_pct"]],
            marker_color=[GOLD, STEEL],
            text=[f"{result['rate_a_pct']}%", f"{result['rate_b_pct']}%"], textposition="outside",
        ))
        fig.update_layout(title=f"{result['metric'].title()} rate: {result['channel_a']} vs {result['channel_b']}",
                           yaxis_title="Rate (%)")
        _themed(fig)
        st.plotly_chart(fig, use_container_width=True)

    elif intent == "ltv":
        df = pd.DataFrame(result["ranked"])
        fig = go.Figure(go.Bar(
            x=df["channel"], y=df["estimated_ltv_inr"],
            marker_color=[GOLD if v == df["estimated_ltv_inr"].max() else STEEL for v in df["estimated_ltv_inr"]],
            text="₹" + df["estimated_ltv_inr"].astype(str), textposition="outside",
        ))
        fig.update_layout(title="Estimated LTV by channel", yaxis_title="Estimated LTV (INR)")
        _themed(fig)
        st.plotly_chart(fig, use_container_width=True)


def _themed(fig: go.Figure):
    fig.update_layout(
        paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        font=dict(color=PAPER), margin=dict(t=50, l=10, r=10, b=10),
        xaxis=dict(gridcolor=PANEL_BORDER), yaxis=dict(gridcolor=PANEL_BORDER),
    )


# --- header ---------------------------------------------------------------
render_hero()

st.title("Product Insights Assistant")
st.caption(
    "Ask a plain-English product question. Every number below comes straight "
    "from a deterministic analytics engine, running on a 50,000-user dataset — "
    "Claude only explains it in words, never invents it."
)

if "history" not in st.session_state:
    st.session_state.history = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

examples_resp, err = call_api("GET", "/")
example_questions = examples_resp.get("example_questions", []) if examples_resp else []

with st.container():
    st.markdown("##### \U0001F987 Open a case file:")
    cols = st.columns(len(example_questions)) if example_questions else []
    for c, q in zip(cols, example_questions):
        with c:
            if st.button(q, key=f"ex_{q}", use_container_width=True):
                st.session_state.pending_question = q

typed = st.chat_input("Ask a product question, e.g. 'Which channel has the worst churn?'")
if typed:
    st.session_state.pending_question = typed

if st.session_state.pending_question:
    question = st.session_state.pending_question
    st.session_state.pending_question = None
    with st.spinner("Consulting the Bat-Computer..."):
        data, err = call_api("POST", "/ask", json={"question": question})
    if err:
        st.error(err)
    else:
        st.session_state.history.insert(0, data)

for data in st.session_state.history:
    st.markdown(f"**Q: {data['question']}**")
    render_narrative_card(data)
    render_chart_for(data["intent"], data["analytics_result"])
    with st.expander("Raw analytics JSON (what the AI was given — nothing more)"):
        st.json(data["analytics_result"])
    st.divider()

# --- explore-all-metrics section ------------------------------------------
st.header("\U0001F987 Explore all metrics")
tabs = st.tabs(["Retention", "Funnel", "Churn", "LTV", "Churn Risk Predictor", "Daily Briefing"])

with tabs[0]:
    data, err = call_api("GET", "/metrics/retention")
    if err:
        st.error(err)
    else:
        render_chart_for("retention", data)

with tabs[1]:
    data, err = call_api("GET", "/metrics/funnel")
    if err:
        st.error(err)
    else:
        render_chart_for("funnel", data)

with tabs[2]:
    data, err = call_api("GET", "/metrics/churn")
    if err:
        st.error(err)
    else:
        render_chart_for("churn", data)

with tabs[3]:
    data, err = call_api("GET", "/metrics/ltv")
    if err:
        st.error(err)
    else:
        render_chart_for("ltv", data)

with tabs[4]:
    info, err = call_api("GET", "/predict/churn-risk/info")
    if err:
        st.error(err)
    else:
        m = info["metrics"]
        st.markdown(
            f"**Model:** Random Forest churn-risk classifier &nbsp;·&nbsp; "
            f"**ROC-AUC:** {m['roc_auc']} &nbsp;·&nbsp; **Accuracy:** {m['accuracy']} "
            f"&nbsp;·&nbsp; trained on {m['n_train']:,} users, tested on {m['n_test']:,}"
        )
        st.caption(
            "Predicts the probability a user fully churns by week 4, using their "
            "acquisition channel, plan, and whether they were active in week 1 -- "
            "a realistic early-warning signal, not a restatement of the outcome."
        )
        c1, c2, c3 = st.columns(3)
        with c1:
            channel = st.selectbox("Acquisition channel", info["channels"])
        with c2:
            plan = st.selectbox("Plan", info["plans"])
        with c3:
            week1_active = st.selectbox("Active in week 1?", ["Yes", "No"]) == "Yes"

        if st.button("\U0001F987 Predict churn risk", type="primary"):
            with st.spinner("Consulting the Bat-Computer..."):
                pred_data, pred_err = call_api("POST", "/predict/churn-risk", json={
                    "channel": channel, "plan": plan, "week1_active": week1_active,
                })
            if pred_err:
                st.error(pred_err)
            else:
                p = pred_data["prediction"]
                band_color = {"High": ALARM, "Medium": GOLD, "Low": "#5BC46D"}[p["risk_band"]]
                st.markdown(f"""
                <div class="card-wrap"><div class="card">
                    <div class="eyebrow">Prediction</div>
                    <div class="headline-number" style="color:{band_color};">
                        {p['churn_probability_pct']}% churn risk
                        <span class="mode-badge" style="background: rgba(0,0,0,0.3); color:{band_color}; border:1px solid {band_color};">{p['risk_band']} RISK</span>
                    </div>
                </div></div>
                """, unsafe_allow_html=True)
                render_narrative_card(pred_data)

with tabs[5]:
    data, err = call_api("GET", "/news/digest")
    if err:
        st.error(err)
    elif not data.get("available"):
        st.info(data.get("message", "No digest available yet."))
    else:
        st.caption(f"Last refreshed: {data['fetched_date']} (auto-updates daily)")
        st.markdown(f"""
        <div class="card-wrap"><div class="card">
            <div class="eyebrow">\U0001F987 Gotham Daily Briefing</div>
            <div class="narrative-text">{data['narrative']}</div>
        </div></div>
        """, unsafe_allow_html=True)
        with st.expander("Full digest and source articles"):
            st.markdown(data["digest_text"])
            st.divider()
            for a in data["articles"]:
                st.markdown(f"- [{a['title']}]({a['url']}) — *{a['source']}*")

st.caption(
    "Built as a portfolio project: synthetic data (50,000 users), a real SQLite "
    "database, deterministic analytics, a churn-risk ML model, a daily news "
    "pipeline, and an AI narration layer with a no-API-key fallback. See README "
    "for architecture."
)
