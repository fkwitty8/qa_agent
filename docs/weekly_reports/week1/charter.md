# Project Charter: Software-Engineering QA Agent

**Project Name:** AI-Powered Software Quality Assurance Agent

**Team Members:** [Insert Team Member Names]

**Date:** 31st August 2026

**Version:** 1.0

---

## 1. Problem Statement

### 1.1 Current Pain Point

Software development teams face significant challenges in maintaining code quality while moving quickly. A major bottleneck in the development lifecycle is the manual effort required for writing, executing, and triaging quality assurance (QA) tests. Developers spend countless hours:

- Writing repetitive unit tests for new features
- Manually creating test plans for edge cases and failure scenarios
- Debugging test failures and translating raw logs into actionable fixes
- Creating and tracking issues for test failures

This manual process slows down development velocity, increases the risk of human error, and diverts developers' attention from higher-value tasks like feature development and architectural improvements.

### 1.2 The Problem We Solve

Our system, the Software-Engineering QA Agent, automates the test generation and triage process. It reads project requirements and repository documentation, proposes comprehensive test plans, executes approved tests in a sandboxed environment, summarizes failures, and drafts structured issues or pull-request notes for developers to review and act upon.

---

## 2. Target Users

| User Role | Description | Key Needs |
|-----------|-------------|-----------|
| **Software Developers** | Team members responsible for writing and maintaining code | - Quick, reliable test generation<br>- Clear failure summaries<br>- Reduced manual work |
| **Team Leads / Engineering Managers** | Leaders overseeing code quality and delivery | - Visibility into test coverage<br>- Consistency across test suites<br>- Faster issue resolution |
| **Quality Assurance Engineers** | Team members focused on testing and validation | - Automated test planning<br>- Comprehensive edge case coverage<br>- Reproducible test execution |

---

## 3. AI Value Proposition

### 3.1 Why AI Is Needed

The tasks of test planning, code analysis, and failure summarization require reasoning about complex software systems. Deterministic tools (like static analyzers or test frameworks) can execute tests but cannot:

- Understand the intent of a feature from requirements text
- Generate semantically meaningful test cases that exercise business logic
- Interpret and summarize failure messages in plain English
- Draft structured issue reports with context

AI, specifically Large Language Models (LLMs), excels at these reasoning and generation tasks.

### 3.2 AI Capabilities Used

| AI Capability | Application in Our System |
|---------------|---------------------------|
| **Reasoning** | Analyzing code context, understanding requirements, planning test strategies |
| **Generation** | Writing syntactically correct test code (unit, integration, edge cases) |
| **Summarization** | Translating verbose test logs into concise, actionable failure summaries |
| **Drafting** | Creating structured GitHub issues with titles, descriptions, and labels |

### 3.3 Deterministic Software Responsibility

AI will not be responsible for critical safety-critical actions. The following remain deterministic and under strict software control:

- Test execution (sandboxed environment with timeouts)
- Input validation (syntax checking of generated test code)
- Permission management (API keys, authentication)
- Tool allow-list enforcement
- User approval workflows




## 4. Scope

### 4.1 In Scope

- Reading team-owned codebase and documentation as a controlled RAG corpus
- Retrieving relevant context based on user input (GitHub issue ID or description)
- Proposing test plans (unit, integration, and edge cases)
- Executing generated tests in a sandboxed environment (Docker or subprocess)
- Analyzing test results and summarizing failures
- Drafting structured GitHub issues for human review
- Managing session state and persistent memory of previous runs
- Supporting a bounded agentic loop with iteration/stop limits
- Evaluating system performance against 30+ scenarios

### 4.2 Out of Scope

- Automatic merging of code (requires human approval)
- Production deployment (test runs only in sandbox)
- Access to production secrets or credentials
- Arbitrary shell command execution (restricted to approved tools)
- Modifying live codebases (read-only access to source code)
- Real-time monitoring of production systems
- Performance testing or load testing (focus is on functional testing)
- Security penetration testing (focus is on QA testing)

---

## 5. Assumptions

| # | Assumption |
|---|------------|
| A1 | The team owns or has authorized access to the codebase used for testing. |
| A2 | The codebase and documentation are manageable in size (approximately 10-50 documents or equivalent records). |
| A3 | We can use public, team-created, synthetic, or authorized data that we can obtain in Week 1. |
| A4 | The DeepSeek API free credits (5M tokens) are sufficient for development and evaluation. |
| A5 | A sandboxed environment (Docker) can be set up to execute generated tests safely. |
| A6 | The team has access to a GitHub account for integration. |
| A7 | Team members have i5/8GB RAM laptops, so local deployment of large models is not feasible; API-based approach is required. |
| A8 | A 30-scenario evaluation suite is sufficient to validate the system's performance. |

---

## 6. Constraints

| # | Constraint |
|---|------------|
| C1 | Hardware: Development machines have i5 CPUs and 8GB RAM. No GPUs available. |
| C2 | Budget: The project must be completed at zero cost. Only free API credits may be used. |
| C3 | Time: The project must be completed within 8 weeks (Week 1: Aug 31 - Sept 4, 2026). |
| C4 | Data Security: No confidential, personal, or restricted data may be sent to external AI services. |
| C5 | Safety: No destructive or high-impact actions may be taken without explicit human approval. |
| C6 | Scope: Single primary user workflow, one small document corpus, two to four tools, one bounded agent loop, one persistent-memory use case. |
| C7 | Evaluation: Must include at least 30 final scenarios covering normal, edge, failure, and adversarial cases. |
| C8 | Integration: The system must be testable using a small controlled corpus and scenario set. |

---

## 7. Success Criteria

| # | Success Criterion | Measurement |
|---|-------------------|-------------|
| S1 | Agent can retrieve relevant code and documentation for a given GitHub issue ID. | Successfully retrieves ≥3 relevant files with source grounding. |
| S2 | Agent can generate syntactically correct test code. | Generated tests pass syntax validation and run without errors. |
| S3 | Agent can execute tests in a sandbox environment. | Tests run in isolated container without affecting host system. |
| S4 | Agent can summarize test failures in plain English. | Summary clearly identifies each failure and includes stack trace context. |
| S5 | Agent can draft a GitHub issue from test failures. | Issue includes title, description, and appropriate labels. |
| S6 | Agent loop stops after defined limits (3 iterations). | Does not exceed iteration limit. |
| S7 | Human approval is required before creating GitHub issues. | User must click "Submit" button; issue is not auto-created. |
| S8 | System passes 30 evaluation scenarios. | ≥90% success rate across normal, edge, failure, adversarial cases. |
| S9 | Session state and persistent memory are maintained. | User can query "Show me tests from last week" and get history. |
| S10 | All software engineering artifacts are in place. | Git history, ClickUp tasks, tests, documentation, secure configuration. |



## 13. Project Differentiators

Our Software-Engineering QA Agent is not a simple chatbot. It performs a traceable 7-step agentic workflow that includes RAG retrieval, test planning, sandboxed test execution, failure analysis, and GitHub issue drafting. The system demonstrates engineering beyond text generation through:

- **Vector database integration** (ChromaDB)
- **Docker-based sandbox** for safe code execution
- **GitHub API integration**
- **State management** (session + persistent memory)
- **Guardrails with human approval controls**
- **Comprehensive evaluation** with 30+ scenarios

The project is deliberately scoped to be completable within the semester while meeting all Week 8 engineering requirements.