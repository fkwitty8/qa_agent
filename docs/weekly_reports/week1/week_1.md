# Week 1 Progress Report
**Project:** Software Engineering QA Agent  
**Group:** Group N  
**Week ending:** 4th September 2026

---

## Work Completed Against Weekly Objectives

- Selected and scoped the QA Agent use case: a developer-facing agent that reads requirements, proposes traceable tests, runs approved tests in a sandbox, and drafts failure summaries and issue/PR notes.
- Drafted the Project Charter, 12 user stories with acceptance criteria, and the AI Boundary Matrix.
- Produced an initial architecture/context diagram covering the read → propose → approve → execute → summarize → draft loop.
- GitHub repository created.
- ClickUp board set up.
- Week 1 tasks assigned.

---

## Key Engineering Decisions

**Test execution and artefact submission are both placed behind a deterministic human-approval gate**, so the LLM component can only propose and draft, never act unilaterally — this directly satisfies the course's bounded-agency requirement.

**Traceability from every proposed test back to a requirement/doc section was made a first-class acceptance criterion (US-9)** so coverage claims can be checked, not just trusted.

---

## Failures / Challenges and Current Response

None.

---

## Links

| Artefact | Link |
|---|---|
| GitHub repo | [link to repo] |
| ClickUp board | https://app.clickup.com/90141606668/v/li/901420225013 |

---

## Individual Contribution Summary

| Role | Member | Week 1 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Owns the Project Charter and the 12 user stories/acceptance criteria; selects and documents which team codebase the agent will target; sets up ClickUp with Week 1 tasks and due dates. |
| Application/Integration Lead | Fahad | Owns GitHub repo setup and folder structure; produces the architecture/context diagram; writes the README and run instructions; commits Week 1 evidence. |
| AI Engineering Lead | Brenda | Owns the AI Boundary Matrix; researches and documents candidate foundation models/sandboxing approaches to shortlist for Week 2; drafts the 'why AI adds value' framing. |
| Quality/Security Lead | Dorcus | Owns Scope/Assumptions/Constraints and reviews the AI Boundary Matrix and user stories for gaps (especially the test-sandbox and no-auto-merge boundary); defines the allow-listed test-command list; sets up `evidence/` folder conventions and the Week 1 progress report. |

---

## Plan for Next Week (Week 2 — Foundation-Model Engineering and Prompting)

- Select and document the model to be used (capability, cost, latency, privacy, access).
- Integrate the model for the test-proposal capability.
- Write Prompt Specification v1.0 and at least 10 test cases with expected vs. actual behaviour.
- Version at least two meaningful prompt iterations.
