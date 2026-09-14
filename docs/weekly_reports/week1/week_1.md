# Week 1 Progress Report

## Project
**Software Engineering QA Agent**

**Group:** Group N

---

## 1. Work Completed Against Weekly Objectives

- Selected and scoped the QA Agent use case.
- Drafted the Project Charter.
- Drafted the user stories and acceptance criteria.
- Created the AI Boundary Matrix.
- Produced the initial architecture/context diagram.
- Created the GitHub repository.
- Set up the ClickUp board.
- Assigned Week 1 tasks.

---

## 2. Key Engineering Decisions and Why They Were Made

- Test execution and artefact submission are placed behind a deterministic human-approval gate.
- The LLM component can propose and draft, but cannot act unilaterally.
- Traceability from every proposed test back to a requirement or document section was made a first-class acceptance criterion.
- These decisions help satisfy the project's bounded-agency requirement.

---

## 3. Failures / Challenges and Current Response

**None reported during Week 1.**

---

## 4. Links

- **GitHub Repository:** https://github.com/fkwitty8/qa_agent.git
- **ClickUp Board:** https://app.clickup.com/90141606668/v/li/901420225013

---

## 5. Individual Contribution Summary

| Role | Member | Week 1 Responsibility |
|---|---|---|
| Project/Requirements Lead | Jim | Owns the Project Charter and user stories/acceptance criteria; selects and documents the team codebase; sets up ClickUp with Week 1 tasks and due dates. |
| Application/Integration Lead | Fahad | Owns GitHub repository setup and folder structure; produces the architecture/context diagram; writes the README and run instructions; commits Week 1 evidence. |
| AI Engineering Lead | Brenda | Owns the AI Boundary Matrix; researches candidate foundation models and sandboxing approaches; drafts the "why AI adds value" framing. |
| Quality/Security Lead | Dorcus | Owns Scope/Assumptions/Constraints; reviews the AI Boundary Matrix and user stories; defines the allow-listed test-command list; sets up evidence folder conventions and the Week 1 progress report. |

---

## 6. Plan for Next Week

### Week 2 — Foundation-Model Engineering and Prompting

- Select and document the model to be used.
- Evaluate the model based on capability, cost, latency, privacy, and access.
- Integrate the model for the test-proposal capability.
- Write Prompt Specification v1.0.
- Create at least 10 test cases with expected vs. actual behaviour.
- Version at least two meaningful prompt iterations.