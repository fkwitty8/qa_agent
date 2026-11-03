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
from src.rag import build_index, _get_collection, index_external_codebase, list_indexed_codebases
from src.tools import call_tool
from src import llm_client

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
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []   # list of {"role": "user"|"assistant", "content": str}
if "chat_session_id" not in st.session_state:
    st.session_state.chat_session_id = None

# ---------------------------------------------------------------------------
# Shared memory store (one instance per session)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_memory() -> MemoryStore:
    return MemoryStore()


@st.cache_resource
def get_chat_store():
    from src.memory import ChatStore
    return ChatStore()


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

    if st.button("💬 Chat", use_container_width=True):
        st.session_state.page = "chat"

    st.divider()
    st.subheader("Recent Runs")

    past_runs = mem.list_runs()
    if not past_runs:
        st.caption("No runs yet.")
    else:
        recent = past_runs[:10]
        options = [r["run_id"] for r in recent]

        def _run_label(rid: str) -> str:
            r = next((x for x in recent if x["run_id"] == rid), None)
            if r is None:
                return rid
            icon = {"passed": "✅", "handoff": "⚠️",
                    "error": "❌", "failed": "❌"}.get(r["status"], "⏳")
            goal = r["goal"]
            goal_short = f"{goal[:35]}…" if len(goal) > 35 else goal
            return f"{icon} {goal_short}"

        def _load_recent_run():
            rid = st.session_state.get("recent_runs_select")
            if not rid:
                return
            full = mem.get_run(rid)
            if full:
                drafts = mem.get_drafts_for_run(rid)
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
                    "run_id":      full["run_id"],
                    "goal":        full["goal"],
                    "status":      full["status"],
                    "trace":       full["trace"],
                    "iterations":  None,
                    "test_results": None,
                    "analysis":    None,
                    "draft":       draft,
                    "handoff_msg": None,
                }
                st.session_state.selected_run_id = rid
                st.session_state.page = "new_run"

        st.selectbox(
            "Load a run",
            options,
            format_func=_run_label,
            index=None,
            key="recent_runs_select",
            placeholder="Load a past run…",
            on_change=_load_recent_run,
        )

    # ── External Codebase ─────────────────────────────────────────
    st.divider()
    st.subheader("🗂 External Codebase")
    st.caption("Index another project's code so the agent can generate tests for it.")

    ext_path = st.text_input(
        "Folder path",
        placeholder="C:/path/to/other-project",
        key="ext_codebase_path",
        help="Absolute path to the root folder of the external codebase.",
    )
    ext_label = st.text_input(
        "Label (optional)",
        placeholder="meeting-planner",
        key="ext_codebase_label",
        help="Short name shown in retrieval results. Defaults to the folder name.",
    )
    ext_exts = st.text_input(
        "Extensions (optional)",
        placeholder=".java .py .md",
        key="ext_codebase_exts",
        help="Space-separated extensions to index. Default: .py .java .js .ts .md .txt",
    )

    # Display one-shot confirmation after auto-clear
    if "_index_confirmation" in st.session_state:
        st.success(st.session_state.pop("_index_confirmation"))

    if st.button("📥 Index Codebase", use_container_width=True,
                 disabled=not ext_path.strip()):
        with st.spinner(f"Indexing {ext_path}…"):
            try:
                exts = (
                    [e if e.startswith(".") else f".{e}"
                     for e in ext_exts.split()]
                    if ext_exts.strip() else None
                )
                label_used = ext_label.strip() or Path(ext_path.strip()).name
                n = index_external_codebase(
                    root=ext_path.strip(),
                    label=ext_label.strip() or None,
                    extensions=exts,
                )
                # Success: clear fields, stash confirmation across rerun
                for key in ("ext_codebase_path", "ext_codebase_label", "ext_codebase_exts"):
                    st.session_state.pop(key, None)
                st.session_state["_index_confirmation"] = (
                    f"✅ Indexed {n} chunks from '{label_used}'."
                )
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Indexing failed: {exc}")

    # Show which codebases are currently indexed
    indexed = list_indexed_codebases()
    if indexed:
        st.caption("Currently indexed:")
        for cb in indexed:
            st.caption(f"  • {cb}")

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

            def _live_record(trace, step, tool, inp, out, dec, extra=None):
                _original_record(trace, step, tool, inp, out, dec, extra=extra)
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

                        # Show test cases inline for PLAN steps
                        test_cases = step.get("test_cases", [])
                        if test_cases:
                            st.markdown("**Proposed test cases:**")
                            for tc in test_cases:
                                with st.expander(
                                    f"`{tc.get('name', '?')}` — {tc.get('type', '')}",
                                    expanded=False,
                                ):
                                    st.caption(tc.get("description", ""))
                                    if tc.get("code"):
                                        st.code(tc["code"], language="python")
            else:
                st.caption("No trace steps recorded.")

            with st.expander("Raw trace JSON"):
                st.json(trace)

        # --- Issue draft + approval ---
        with col2:
            if run.get("pass_summary"):
                st.subheader("Outcome")
                st.success(run["pass_summary"])
            else:
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

# ---------------------------------------------------------------------------
# Chat page
# ---------------------------------------------------------------------------

_CHAT_SYSTEM_PROMPT = """You are a helpful assistant for the SE QA Agent — a \
bounded, agentic software quality tool built as a university capstone project.

You know everything about this system:

WHAT IT DOES:
The SE QA Agent helps software developers automatically generate pytest tests, \
run them in a Docker sandbox, analyse failures with AI, and draft GitHub issues \
for human review. The developer always approves before anything is posted.

THE FIVE LAYERS:
1. Model — Gemini 3.8 Flash (Google AI Studio, free tier) via llm_client.py
2. Prompts — Three versioned prompt files: test_planner, failure_analyzer, issue_drafter
3. RAG — ChromaDB vector store; the agent indexes its own codebase (and any external \
   codebase the user provides) and retrieves relevant code chunks before generating tests
4. Tools — Four safe, allow-listed tools: propose_tests, run_tests, analyze_failures, draft_issue
5. Agent loop — Bounded SENSE→PLAN→ACT→OBSERVE→DECIDE loop, max 3 iterations by default

THE WORKFLOW (when a user enters a goal):
SENSE: retrieve relevant code chunks from ChromaDB
PLAN: call Gemini with the test_planner prompt → get pytest test cases as JSON
ACT: run the test code inside a python:3.11-slim Docker container
OBSERVE: if tests fail, call Gemini with failure_analyzer prompt → root-cause analysis
DECIDE: if all pass or limit reached → call issue_drafter → draft GitHub issue (not posted yet)
The developer then reviews the draft and clicks "Submit to GitHub" to approve posting.

GUARDRAILS:
- Input validation: rejects goals >2000 chars, prompt injection patterns, deployment commands
- Tool allow-list: only 4 tools permitted; any other tool name is rejected immediately
- Syntax gate: test code is validated with ast.parse() before Docker starts
- Docker sandbox: test file mounted read-only; no host filesystem access; always cleaned up
- Output schema validation: tool outputs checked for required keys after every call
- Human approval gate: draft_issue always defaults to approved=False; GitHub API never called automatically
- Iteration hard cap: loop condition cannot be bypassed by tool output

EXTERNAL CODEBASES:
The user can index any external project (Java, Python, JS, etc.) via the sidebar. \
The agent retrieves chunks from it and generates self-contained mocked tests \
(since external code cannot be imported inside the Docker sandbox).

MEMORY:
Run history is stored in SQLite at evidence/qa_agent.db. \
The Streamlit sidebar shows the last 10 runs with status icons. \
Past runs can be clicked to restore their trace and draft.

TECH STACK:
Python 3.11, Gemini 3.8 Flash, gemini-embedding-001, ChromaDB, Docker, \
Streamlit, SQLite, PyGithub, pytest.

Be concise and helpful. If the user asks how to do something in the system, \
give them practical step-by-step guidance. If they ask a general AI or \
software engineering question, answer that too. Always be honest about \
limitations — for example, the agent cannot directly run Java tests, and \
it requires Docker Desktop to be running for test execution."""


if st.session_state.page == "chat":
    chat_store = get_chat_store()
    st.header("💬 Chat with the QA Agent")
    st.caption("Ask anything about the system, how to use it, or general software engineering questions.")

    # Session controls
    col_new, col_clear = st.columns([1, 1])
    if col_new.button("＋ New chat", use_container_width=True):
        st.session_state.chat_session_id = None
        st.session_state.chat_history = []
        st.rerun()

    # Past sessions selector
    sessions = chat_store.list_sessions()
    if sessions:
        options = [s["session_id"] for s in sessions]

        def _session_label(sid):
            s = next((x for x in sessions if x["session_id"] == sid), None)
            if s is None:
                return sid
            msgs = chat_store.get_messages(sid)
            first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
            preview = f"{first_user[:40]}…" if len(first_user) > 40 else first_user
            return f"{preview} · {s['message_count']} msg(s)"

        selected = st.selectbox("Past conversations", options,
                                format_func=_session_label,
                                index=None,
                                placeholder="Continue a past conversation…")
        if selected and selected != st.session_state.chat_session_id:
            msgs = chat_store.get_messages(selected)
            st.session_state.chat_history = [
                {"role": m["role"], "content": m["content"]} for m in msgs
            ]
            st.session_state.chat_session_id = selected

    if col_clear.button("🗑 Clear chat", use_container_width=True,
                        disabled=not st.session_state.chat_history):
        st.session_state.chat_session_id = None
        st.session_state.chat_history = []
        st.rerun()

    # Display chat history
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input
    user_input = st.chat_input("Ask a question…")

    if user_input:
        # Ensure an active session exists (lazy creation)
        if st.session_state.chat_session_id is None:
            st.session_state.chat_session_id = chat_store.create_session()
        sid = st.session_state.chat_session_id

        # Add user message to history and persist
        st.session_state.chat_history.append({"role": "user", "content": user_input})
        try:
            chat_store.add_message(sid, "user", user_input)
        except Exception:
            pass
        with st.chat_message("user"):
            st.markdown(user_input)

        # Build conversation context for Gemini
        history_window = st.session_state.chat_history[-20:]
        conversation_prompt = "\n\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in history_window[:-1]
        )
        if conversation_prompt:
            full_prompt = f"{conversation_prompt}\n\nUser: {user_input}"
        else:
            full_prompt = user_input

        # Call Gemini
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    response = llm_client.generate(
                        prompt=full_prompt,
                        system_prompt=_CHAT_SYSTEM_PROMPT,
                        temperature=0.5,
                    )
                except Exception as exc:
                    response = f"Sorry, I couldn't get a response right now. Error: {exc}"

            st.markdown(response)

        # Store assistant reply
        st.session_state.chat_history.append({"role": "assistant", "content": response})
        try:
            chat_store.add_message(sid, "assistant", response)
        except Exception:
            pass
