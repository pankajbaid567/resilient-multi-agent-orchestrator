# Hackathon Demo Script (5 Minutes)

## Objective
Deliver a fast, convincing walkthrough that proves the agent is both capable and reliable under real execution uncertainty.

## Pre-Demo Setup (Before Going On Stage)
1. Start services: `docker compose up --build`.
2. Open frontend dashboard: `http://localhost:5173`.
3. Confirm backend health once: `http://localhost:8000/health`.
4. Keep [scenarios.json](scenarios.json) open for quick copy/paste.
5. Keep one completed run in history if possible for instant fallback storytelling.

## Run of Show (Total 5:00)

| Time | What To Say | What To Click | What To Point Out |
| --- | --- | --- | --- |
| 0:00-0:30 | "This is a reliability-first AI agent, not just a single prompt response app." | Show dashboard header and status badge. | Sticky status, chaos toggle, and live observability panels. |
| 0:30-1:50 | "First, a clean happy-path research task to show planning and synthesis quality." | Paste Happy Path input and submit. | Step decomposition in DAG, live logs, and trace events updating in real time. |
| 1:50-2:50 | "Now we deliberately inject faults with Chaos Mode to prove retry and fallback behavior." | Turn Chaos Mode ON. Paste Failure Recovery input and submit. | Retry events, fallback transitions, and continued forward progress despite injected issues. |
| 2:50-3:50 | "Finally, a code-heavy task where quality matters and reflection can self-correct." | Turn Chaos Mode OFF. Paste Reflection Demo input and submit. | Validation/reflect signals in timeline and improved downstream output quality. |
| 3:50-4:30 | "The result modal gives final synthesis, confidence, and a downloadable trace artifact." | Open final result panel/modal. Click Download trace JSON. | Confidence badge, execution summary, and auditability. |
| 4:30-5:00 | "Same architecture handles both best-case throughput and worst-case turbulence." | Return to main layout, briefly show all panes. | Reliability story: plan, execute, recover, explain. |

## Scenario-Specific Narration Cues

### 1) Happy Path
1. Say: "Notice how one broad prompt is decomposed into clear operational steps."
2. Say: "Web search and synthesis are chained, not blended into one fragile call."
3. Say: "The final report quality is a product of orchestrated steps, not luck."

### 2) Failure Recovery
1. Say: "This run has Chaos Mode enabled, so failures are expected by design."
2. Say: "Retries and fallback are automatic; the user does not babysit execution."
3. Say: "Even degraded runs still produce useful output with visible reliability telemetry."

### 3) Reflection Demo
1. Say: "For code tasks, correctness matters more than first-attempt fluency."
2. Say: "When validation is weak, reflection adapts the plan or step definition."
3. Say: "The timeline gives a transparent why behind every correction."

## Backup Plans (If Something Goes Wrong Live)

### Backup for Happy Path
If planning latency is high, narrate the architecture while the DAG appears, then switch to a previously completed run and show the result + trace timeline.

### Backup for Failure Recovery
If no retries appear quickly, keep Chaos Mode ON and rerun once. If still clean, explain probabilistic injection and show existing retry/fallback events from a stored prior run.

### Backup for Reflection Demo
If reflection does not trigger naturally, use a slightly stricter follow-up prompt such as "include edge-case delete behavior and failing test analysis" and narrate the validation-first path.

## Fast Demo Control Tips
1. If you are behind schedule, skip waiting for full completion and focus on live events + one completed result card.
2. Keep one hand on Chaos Mode and one on tabs (Execution/Trace) to steer the story quickly.
3. Use the logs panel for quick proof and timeline panel for deep proof.

## Q&A Preparation (Top 5)

| Question | Suggested Answer |
| --- | --- |
| How is this different from a normal chatbot? | "A chatbot usually does one-shot generation. This system plans tasks, executes step-by-step, validates quality, retries/fallbacks on failure, and exposes traceability for every decision." |
| What happens when providers fail repeatedly? | "Retries are attempted with backoff, then fallback providers are used. If all providers fail, the task still records structured failure state and trace metadata for recovery and diagnosis." |
| How do you prevent silent failures? | "Every step emits execution events and trace entries. Failures are explicit in timeline/logs, and confidence + summary are attached to final outputs." |
| Can this run in production with compliance requirements? | "Yes. It already produces auditable traces, checkpointed state, and deterministic reliability hooks. Governance controls can be layered on top of this observable execution model." |
| How do you test resilience, not just happy paths? | "Chaos mode injects latency, empty responses, rate limits, and corrupt outputs. We use it to validate retry, fallback, and recovery logic under controlled failure conditions." |

## Closing Line
"This demo shows an agent that does not just answer, it executes responsibly: it plans, monitors itself, recovers from faults, and explains what happened."
