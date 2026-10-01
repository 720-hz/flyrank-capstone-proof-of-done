# My 10x Solution — Ziad Khaled Shatah

**Project: ProofOfDone** — a verification backend that checks an AI agent's "task complete"
claims against real evidence before trusting them.

**Repo:** https://github.com/720-hz/flyrank-capstone-proof-of-done

## What problem are you solving

I run a personal AI assistant (JARVIS) that occasionally reports a task "done" with no real
output behind it — a documented, observed bug: a voice command to build a website and save it
to disk once came back "done" with no file ever created. The agent isn't lying on purpose; it
just has no mechanism that forces it to prove completion before reporting it, so a confident
"done" and an honest one look identical from the outside. The only way to catch this today is
a human manually re-checking the agent's work, whenever they happen to think to — there's no
systematic point where a completion claim actually gets checked.

Anyone running an autonomous or semi-autonomous agent has this same problem: a coding agent,
an automation pipeline, a personal assistant — anywhere "the agent said it finished" is
currently the only signal available. Catching a false completion today means noticing it by
hand, after the fact, if ever. ProofOfDone checks every claim automatically, within seconds of
submission, against real evidence — turning "maybe discovered this eventually" into "flagged
before you ever trusted it."

This submission is a standalone, appropriately-scoped backend service built from scratch for
this capstone — not a port of the real JARVIS codebase, which lives outside this project —
but it targets the exact bug above, scoped down to something a 3-week part-time build could
actually finish and run.

## How did you implement it

An agent (or a human) registers, creates a task, and submits a **claim** that the task is
done, along with whatever **evidence** it has. The server never just trusts the claim text —
every evidence type maps to a specific, named check, and the server is honest about which
checks it actually *can* do:

- `file` and `url` evidence get a real deterministic check right there — does the file exist
  (optionally with a SHA-256 match), does the URL return the expected status and content.
- `text` evidence has no checkable artifact at all, and `command` evidence is self-reported
  (the agent could lie about its own exit code) — both get routed to an LLM review step
  instead of being silently trusted or silently rejected. A `command` claim with no supporting
  log excerpt is flagged `suspicious` immediately, before it even reaches the LLM: a bare
  claimed exit code, alone, is not evidence.

Verification runs as two separate durable background workers, not inline on the request path
— a crashed worker leaves a claim safely reclaimable rather than silently lost (a conditional
`UPDATE ... WHERE status='pending'` is the atomic claim step, same pattern I've used in every
prior capstone's background job). They're kept as two workers specifically so a slow or
failing LLM call can never block the fast deterministic checks. A separate, genuinely
different mechanism — a cron-style SLA sweep — catches anything that sits unresolved too long
regardless of why, matching the scheduled/"fixed times, no request involved" definition of a
cron job rather than a worker draining a queue.

Two-tier auth is actually enforced, not just "logged in or not": a regular agent's API key can
only act on its own tasks and claims (reading another agent's task is a real 403, proven in
the test suite, not just documented), and a separate admin key exclusively gates audit-report
generation. The LLM judge itself is swappable by one config line — a deterministic $0 mock
judge for development and tests, or a real Claude API call with forced structured tool output
— through a registry, the same adapter pattern I used for pluggable publishers in an earlier
capstone, so swapping the live judge never touches the verification logic.

### The 6 concepts used (no swaps)

| Concept | Implementation |
|---|---|
| API endpoints | FastAPI routes for agents, tasks, claims, verification/review/sweep runs, and reports |
| Database | SQLite — 7 tables: agents, tasks, claims, verification_attempts, llm_reviews, audit_reports, claim_jobs |
| Authentication | Per-agent API keys (hashed, never stored in plaintext) + a separate admin key, both enforced at every route that touches real data |
| Background jobs | Two durable, crash-safe workers (deterministic verification, LLM review) plus a distinct cron-style SLA sweep |
| Reporting (PDF) | A PDF audit report summarizing verified/failed/suspicious claims over a period |
| LLM integration | A real Claude API call (forced structured tool-use response) judges claims with no checkable artifact, with a real token/cost log |

### Verification

101 automated tests, fully offline, covering every evidence-routing path, the durable-worker
claim/reclaim pattern (including a simulated crashed worker), the LLM judge adapter swap, real
401/403 auth enforcement over HTTP, the SLA sweep, and PDF generation. A seed script
(`scripts/seed.py`) creates one claim per evidence-type/outcome combination and actually runs
both workers and the sweep against them, so a stranger can see every resolved status for real
within a minute of cloning the repo. See `README.md`, `DESIGN.md`, `EVIDENCE.md`, and
`BUILDLOG.md` in the repo for the full write-up, real transcripts, and what broke during the
build.
