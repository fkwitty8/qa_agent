"""
main.py — Streamlit UI for the QA Agent.

Run with:
    streamlit run src/main.py

Pages:
  • New Run    — enter a goal, run the agent, inspect trace and draft issue
  • Past Runs  — table of all past runs with status; click to reload any run

Session state keys:
  current_run     dict | None   — result dict from the last QAAgent.run()
  selected_run_id str | None    — run_id loaded from history
  page            str           — "new_run" | "past_runs"
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `from src.*` imports work when
# Streamlit launches the script directly (e.g. `streamlit run src/main.py`).
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import json
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src.agents import QAAgent
from src.memory import MemoryStore
from src.rag import build_index, _get_collection
from src.tools import call_tool

load_dotenv()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SE QA Agent",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Auto-build RAG index on first load (runs once per server session)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Building RAG index…")
def _ensure_rag_index():
    """Build the RAG index if the collection is empty."""
    try:
        col = _get_collection()
        if col.count() == 0:
            total = build_index()
            return f"Index built: {total} chunks"
        return f"Index ready: {col.count()} chunks"
    except Exception as exc:
        return f"RAG index warning: {exc}"

_rag_status = _ensure_rag_index()

# ---------------------------------------------------------------------------
# Initialise session state
# ---------------------------------------------------------------------------
if "current_run" not in st.session_state:
    st.session_state.current_run = None
if "selected_run_id" not in st.session_state:
    st.session_state.selected_run_id = None
if "page" not in st.session_state:
    st.session_state.page = "new_run"

# ---------------------------------------------------------------------------
# Shared memory store (one instance per session)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_memory() -> MemoryStore:
    return MemoryStore()


mem = get_memory()

# ---------------------------------------------------------------------------
# Sidebar — navigation + run history
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🔬 QA Agent")
    st.divider()

    if st.button("＋ New Run", use_container_width=True):
        st.session_state.page = "new_run"
        st.session_state.current_run = None
        st.session_state.selected_run_id = None

    if st.button("📋 Past Runs", use_container_width=True):
        st.session_state.page = "past_runs"

    st.divider()
    st.subheader("Recent Runs")

    past_runs = mem.list_runs()
    if not past_runs:
        st.caption("No runs yet.")
    else:
        for run in past_runs[:10]:
            status_icon = {
                "passed":  "✅",
                "handoff": "⚠️",
                "error":   "❌",
                "failed":  "❌",
            }.get(run["status"], "⏳")

            label = f"{status_icon} {run['goal'][:35]}…" if len(run["goal"]) > 35 else f"{status_icon} {run['goal']}"
            if st.button(label, key=f"hist_{run['run_id']}", use_container_width=True):
                st.session_state.selected_run_id = run["run_id"]
                full = mem.get_run(run["run_id"])
                if full:
                    # Reconstruct a display-compatible result dict from stored data
                    st.session_state.current_run = {
                        "run_id":      full["run_id"],
                        "goal":        full["goal"],
                        "status":      full["status"],
                        "trace":       full["trace"],
                        "iterations":  None,
                        "test_results": None,
                        "analysis":    None,
                        "draft":       None,
                        "handoff_msg": None,
                    }
                    # Restore draft if available
                    drafts = mem.get_drafts_for_run(run["run_id"])
                    if drafts:
                        d = drafts[0]
                        st.session_state.current_run["draft"] = {
                            "title":      d["title"],
                            "body":       d["body"],
                            "labels":     ["bug", "qa-agent"],
                            "github_url": d["github_url"],
                        }
                st.session_state.page = "new_run"
                st.rerun()

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

if st.session_state.page == "new_run":
    st.header("Run the QA Agent")

    goal = st.text_area(
        "Goal",
        placeholder="e.g. test that rag.retrieve() handles an empty corpus gracefully",
        height=80,
        help="Describe the behaviour or module to test. Max 2 000 characters.",
    )

    run_btn = st.button("▶ Run Agent", type="primary", disabled=not goal.strip())

    # --- Run the agent ---
    if run_btn and goal.strip():
        st.session_state.current_run = None
        st.session_state.selected_run_id = None

        progress_placeholder = st.empty()
        status_placeholder   = st.empty()

        with st.spinner("Agent running…"):
            agent = QAAgent()

            # Monkey-patch _record to stream step progress live
            import src.agents as _agents_mod
            _original_record = _agents_mod._record
            steps_so_far = []

            def _live_record(trace, step, tool, inp, out, dec):
                _original_record(trace, step, tool, inp, out, dec)
                steps_so_far.append(f"**{step}** › {tool or '—'}: {dec}")
                progress_placeholder.markdown("\n\n".join(steps_so_far))

            _agents_mod._record = _live_record
            try:
                result = agent.run(goal)
            finally:
                _agents_mod._record = _original_record

        progress_placeholder.empty()
        st.session_state.current_run = result

        # Persist to memory
        mem.save_run(
            result["run_id"],
            result["goal"],
            result["trace"],
            result["status"],
        )
        if result.get("draft") and result["draft"].get("title"):
            issue_id = uuid.uuid4().hex[:12]
            mem.save_draft(
                issue_id,
                result["run_id"],
                result["draft"]["title"],
                result["draft"]["body"],
            )
            # Store issue_id in draft for later url update
            result["draft"]["_issue_id"] = issue_id

    # --- Display current run results ---
    run = st.session_state.current_run
    if run:
        status = run.get("status", "")
        status_map = {
            "passed":  ("✅ Passed", "success"),
            "handoff": ("⚠️ Hand-off Required", "warning"),
            "error":   ("❌ Error", "error"),
            "failed":  ("❌ Failed", "error"),
        }
        label, kind = status_map.get(status, (status, "info"))

        if kind == "success":
            st.success(label)
        elif kind == "warning":
            st.warning(label)
        else:
            st.error(label)

        if run.get("handoff_msg"):
            st.info(run["handoff_msg"])

        # Iterations
        if run.get("iterations"):
            st.caption(f"Completed in {run['iterations']} iteration(s) · run_id: `{run['run_id']}`")

        st.divider()

        col1, col2 = st.columns([1, 1])

        # --- Trace viewer ---
        with col1:
            st.subheader("Execution Trace")
            trace = run.get("trace", [])
            if trace:
                for i, step in enumerate(trace):
                    with st.expander(
                        f"{i+1}. **{step['step']}** › {step.get('tool') or '—'}",
                        expanded=(i == 0),
                    ):
                        st.markdown(f"**Input:** {step.get('input_summary', '')}")
                        st.markdown(f"**Output:** {step.get('output_summary', '')}")
                        st.markdown(f"**Decision:** {step.get('decision', '')}")
                        st.caption(step.get("timestamp_utc", ""))
            else:
                st.caption("No trace steps recorded.")

            with st.expander("Raw trace JSON"):
                st.json(trace)

        # --- Issue draft + approval ---
        with col2:
            st.subheader("Issue Draft")
            draft = run.get("draft")
            if draft and draft.get("title"):
                st.markdown(f"**{draft['title']}**")
                st.markdown("---")
                st.markdown(draft["body"])

                if draft.get("github_url"):
                    st.success(f"Posted: [{draft['github_url']}]({draft['github_url']})")
                else:
                    with st.form("approve_form"):
                        st.caption("Review the draft above before submitting.")
                        submitted = st.form_submit_button(
                            "Submit to GitHub", type="primary"
                        )
                        if submitted:
                            with st.spinner("Posting to GitHub…"):
                                post_result = call_tool(
                                    "draft_issue",
                                    {
                                        "analysis":  run.get("analysis") or {},
                                        "approved":  True,
                                        "goal":      run["goal"],
                                    },
                                )
                            if post_result.get("github_url"):
                                st.success(f"Posted: [{post_result['github_url']}]({post_result['github_url']})")
                                # Update memory with URL
                                issue_id = draft.get("_issue_id")
                                if issue_id:
                                    mem.update_draft_url(issue_id, post_result["github_url"])
                                st.session_state.current_run["draft"]["github_url"] = post_result["github_url"]
                            else:
                                st.error(f"Post failed: {post_result.get('error', 'Unknown error')}")
            else:
                st.caption("No draft generated for this run.")

# ---------------------------------------------------------------------------
# Past Runs page
# ---------------------------------------------------------------------------
elif st.session_state.page == "past_runs":
    st.header("Past Runs")

    past_runs = mem.list_runs()
    if not past_runs:
        st.info("No runs recorded yet. Go to New Run to start the agent.")
    else:
        import pandas as pd

        df = pd.DataFrame(past_runs)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M")
        df["goal_short"] = df["goal"].str[:60] + "…"
        df = df[["run_id", "timestamp", "goal_short", "status"]]
        df.columns = ["Run ID", "Time (UTC)", "Goal", "Status"]

        st.dataframe(df, use_container_width=True, hide_index=True)

        st.divider()
        selected = st.selectbox(
            "Load a run to view its trace and draft",
            options=[r["run_id"] for r in past_runs],
            format_func=lambda rid: next(
                (f"{r['goal'][:50]} [{r['status']}]" for r in past_runs if r["run_id"] == rid), rid
            ),
        )
        if st.button("Load selected run"):
            full = mem.get_run(selected)
            if full:
                drafts = mem.get_drafts_for_run(selected)
                draft = None
                if drafts:
                    d = drafts[0]
                    draft = {
                        "title":      d["title"],
                        "body":       d["body"],
                        "labels":     ["bug", "qa-agent"],
                        "github_url": d["github_url"],
                        "_issue_id":  d["issue_id"],
                    }
                st.session_state.current_run = {
                    "run_id":       full["run_id"],
                    "goal":         full["goal"],
                    "status":       full["status"],
                    "trace":        full["trace"],
                    "iterations":   None,
                    "test_results": None,
                    "analysis":     None,
                    "draft":        draft,
                    "handoff_msg":  None,
                }
                st.session_state.page = "new_run"
                st.rerun()
