# Prompt Evaluation Table

**Date:** 2026-09-14  
**Evaluator:** Manual — prompts sent to `gemini-2.0-flash` via `src/llm_client.py`  
**Prompts evaluated:** `test_planner` v1.0, `failure_analyzer` v1.0  
**Total cases:** 10 (5 per prompt)  
**Pass criteria:** Output is valid JSON matching the specified schema with no extra text.

---

## test_planner v1.0 — 5 Cases

### TP-01

| Field | Value |
|---|---|
| **ID** | TP-01 |
| **Prompt version** | test_planner v1.0 |
| **Input (GOAL)** | "Verify that `llm_client.generate()` raises `EnvironmentError` when `GEMINI_API_KEY` is not set." |
| **Input (CONTEXT)** | `src/llm_client.py` — `_get_client()` function body |
| **Expected output** | Valid JSON with `"goal"` string and `"test_cases"` list; each case has `name`, `type`, `description`, `code`; all code contains `assert`; no markdown fences |
| **Actual output** | Valid JSON, 3 test cases generated: missing key raises `EnvironmentError`, whitespace-only key raises `EnvironmentError`, env var present does not raise. All code valid Python. No extra text. |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### TP-02

| Field | Value |
|---|---|
| **ID** | TP-02 |
| **Prompt version** | test_planner v1.0 |
| **Input (GOAL)** | "Test that `fill_template()` in `prompt_loader.py` replaces all `{{KEY}}` occurrences and leaves unknown placeholders intact." |
| **Input (CONTEXT)** | `src/prompt_loader.py` — `fill_template()` function body |
| **Expected output** | Valid JSON; test cases cover: single placeholder, multiple same placeholder, unknown placeholder untouched, empty variables dict |
| **Actual output** | Valid JSON, 4 test cases. Covered all expected scenarios. Code uses `assert` correctly. No markdown fences. |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### TP-03

| Field | Value |
|---|---|
| **ID** | TP-03 |
| **Prompt version** | test_planner v1.0 |
| **Input (GOAL)** | "Verify the agent loop stops after `MAX_AGENT_ITERATIONS` even when tests keep failing." |
| **Input (CONTEXT)** | (empty — no source yet) |
| **Expected output** | Valid JSON; model should still generate a plan based on the goal description alone, acknowledging missing context |
| **Actual output** | Valid JSON with 3 test cases. However, generated code imports `from src.agents import QAAgent` which does not exist yet — tests would fail at import. |
| **Status** | ⚠️ PARTIAL |
| **Notes** | Output is valid JSON and valid Python syntax, but tests are not runnable against current codebase. Acceptable at this stage — agents.py is a future task. Counted as pass for prompt schema compliance; flagged for re-evaluation in Week 6. |

---

### TP-04

| Field | Value |
|---|---|
| **ID** | TP-04 |
| **Prompt version** | test_planner v1.0 |
| **Input (GOAL)** | "Test that `load_versioned_prompt()` raises `FileNotFoundError` when neither the versioned file nor a matching current version exists." |
| **Input (CONTEXT)** | `src/prompt_loader.py` — `load_versioned_prompt()` full body |
| **Expected output** | Valid JSON; test cases use `pytest.raises(FileNotFoundError)`, use `tmp_path` or `monkeypatch` to isolate filesystem |
| **Actual output** | Model wrapped the entire JSON in a ```json ... ``` markdown fence despite the constraint "Output ONLY the JSON object". Required stripping before `json.loads()` could parse it. |
| **Status** | ❌ FAIL |
| **Notes** | This is the primary v1.0 failure. The constraint states "Do not include prose, markdown fences, or any text outside the JSON" but the model ignored it on a longer context input. Fix in v1.1: add an explicit negative example showing the disallowed format immediately before the output format section. |

---

### TP-05

| Field | Value |
|---|---|
| **ID** | TP-05 |
| **Prompt version** | test_planner v1.0 |
| **Input (GOAL)** | "Verify `embed()` in `llm_client.py` raises `RuntimeError` when the underlying API call throws an exception." |
| **Input (CONTEXT)** | `src/llm_client.py` — `embed()` function body |
| **Expected output** | Valid JSON; test patches `client.models.embed_content` to raise, asserts `RuntimeError` with message containing "embed_content failed" |
| **Actual output** | Valid JSON, 3 test cases. Correct use of `unittest.mock.patch`. All assert statements present. No extra text. |
| **Status** | ✅ PASS |
| **Notes** | — |

**test_planner v1.0 result: 4/5 passing (80%).** One failure (TP-04) due to markdown fence wrapping on longer context inputs.

---

## failure_analyzer v1.0 — 5 Cases

### FA-01

| Field | Value |
|---|---|
| **ID** | FA-01 |
| **Prompt version** | failure_analyzer v1.0 |
| **Input (TEST_RESULTS)** | `FAILED tests/test_llm_client.py::test_generate_missing_api_key - AssertionError: assert "GEMINI_API_KEY is not set" in "Gemini authentication failed"` |
| **Input (SOURCE_CONTEXT)** | `src/llm_client.py` — `_get_client()` and exception handling block |
| **Expected output** | Valid JSON; `summary` identifies wrong error message; `root_cause` names `_get_client()` or the `EnvironmentError` raise; `affected_files` includes `src/llm_client.py`; `confidence` is "high" |
| **Actual output** | Valid JSON. `summary`: "The test expects the error message to contain 'GEMINI_API_KEY is not set' but the actual exception path raises 'Gemini authentication failed' instead, because the missing-key check and the auth-failure check are in different code paths." `root_cause` correctly named `_get_client()`. `confidence`: "high". |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### FA-02

| Field | Value |
|---|---|
| **ID** | FA-02 |
| **Prompt version** | failure_analyzer v1.0 |
| **Input (TEST_RESULTS)** | `FAILED tests/test_prompt_loader.py::test_load_prompt_missing_raises - FileNotFoundError: [Errno 2] No such file or directory: '/real/prompts/nonexistent_prompt.txt'` (no "not found" in message) |
| **Input (SOURCE_CONTEXT)** | `src/prompt_loader.py` — `load_prompt()` function body |
| **Expected output** | Valid JSON; identifies that the `FileNotFoundError` is raised by `path.read_text()` before the custom message check, or that the custom message is present — whichever is the actual cause |
| **Actual output** | Valid JSON. Correctly identified that `path.read_text()` would never be reached because the `if not path.is_file()` guard raises first. Identified that the raw OS error propagated instead of the custom message — a test setup issue (wrong patch target). `confidence`: "medium". |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### FA-03

| Field | Value |
|---|---|
| **ID** | FA-03 |
| **Prompt version** | failure_analyzer v1.0 |
| **Input (TEST_RESULTS)** | Three unrelated failures: `ImportError: cannot import name 'QAAgent' from 'src.agents'`, `AttributeError: 'NoneType' has no attribute 'retrieve'`, `AssertionError: assert 3 == 5` |
| **Input (SOURCE_CONTEXT)** | (empty) |
| **Expected output** | Valid JSON; `root_cause` uses numbered list for three separate causes; `confidence` is "low" given no source context |
| **Actual output** | Valid JSON. `root_cause` contained a numbered list addressing all three failures. `confidence`: "low" with note "no source context provided". `affected_files` was an empty list `[]` which is correct given no context. |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### FA-04

| Field | Value |
|---|---|
| **ID** | FA-04 |
| **Prompt version** | failure_analyzer v1.0 |
| **Input (TEST_RESULTS)** | `PASSED` — all 6 tests passed, no failures |
| **Input (SOURCE_CONTEXT)** | `src/llm_client.py` full source |
| **Expected output** | Valid JSON; `summary` should indicate no failures; `root_cause` should be "none" or equivalent; `suggested_fix` should be "none required" |
| **Actual output** | Valid JSON. `summary`: "All tests passed. No failures to analyse." `root_cause`: "None identified." `suggested_fix`: "No fix required." `confidence`: "high". |
| **Status** | ✅ PASS |
| **Notes** | — |

---

### FA-05

| Field | Value |
|---|---|
| **ID** | FA-05 |
| **Prompt version** | failure_analyzer v1.0 |
| **Input (TEST_RESULTS)** | `FAILED tests/test_prompt_loader.py::test_fill_template_replaces_repeated_placeholder - AssertionError: assert "target is the target" == "{{GOAL}} is the {{GOAL}}"` (note: assert is backwards — test itself is wrong) |
| **Input (SOURCE_CONTEXT)** | `src/prompt_loader.py` — `fill_template()` body; `tests/test_prompt_loader.py` — the failing test |
| **Expected output** | Valid JSON; model should identify that the test assertion is inverted (comparing filled result against the un-filled template), not that `fill_template()` is broken |
| **Actual output** | Valid JSON. Correctly identified the test assertion was backwards: "The test asserts `filled == '{{GOAL}} is the {{GOAL}}'` but `fill_template()` correctly replaces the placeholder. The assertion should compare against `'target is the target'`." `suggested_fix` described swapping the expected value. `confidence`: "high". |
| **Status** | ✅ PASS |
| **Notes** | — |

**failure_analyzer v1.0 result: 5/5 passing (100%).**

---

## Summary

| Prompt | Version | Cases | Passed | Score |
|---|---|---|---|---|
| test_planner | v1.0 | 5 | 4 | 80% |
| failure_analyzer | v1.0 | 5 | 5 | 100% |
| **Total** | | **10** | **9** | **90%** |

### Failures Identified

| Case | Prompt | Root Cause | Fix Version |
|---|---|---|---|
| TP-04 | test_planner | Model wraps JSON output in markdown fences on longer context inputs despite explicit constraint | v1.1 |

### Changes Applied in v1.1

**test_planner v1.1** — added a "NEGATIVE EXAMPLE" block immediately before the OUTPUT FORMAT section:

```
### NEGATIVE EXAMPLE — do not do this:
```json
{ "goal": "...", "test_cases": [...] }
```

The above is wrong because it wraps the JSON in a markdown code fence.
Output the raw JSON object only, with no surrounding text.
```

The explicit counter-example is more reliable than a constraint statement alone for
preventing fence-wrapping, based on the observed model behaviour.
