# Design — ProofOfDone

## M1 one-pager (this section)

**The problem.** AI agents sometimes report a task "done" without producing anything real —
no file written, no deploy live, no command that actually succeeded. The report sounds
confident either way, so the only way to catch it today is a human manually re-checking the
agent's work, whenever they happen to think to. There's no systematic point where a
completion claim gets checked before it's trusted. (This is a real bug observed in my own
JARVIS assistant: a voice command to build a site and save it to disk came back "done" with
no file ever created.)

**Who has this problem.** Anyone running an autonomous or semi-autonomous agent — a
personal assistant, a coding agent, an automation pipeline — where "the agent said it
finished" is currently the only signal available.

**The 10x claim.** Catching a false completion today means manually re-checking a claim by
hand, whenever someone happens to notice. ProofOfDone checks every claim automatically,
within seconds of submission, against real evidence — turning "maybe discovered this
eventually" into "flagged before you ever trusted it."

**The 6 concepts (no swaps needed):**

| # | Concept | Where it lives |
|---|---|---|
| 1 | API endpoints | `app/routes/*.py` — register task, submit claim, query status, list incidents, generate report |
| 2 | Database | `app/db.py` — agents, tasks, claims, verification_attempts, llm_reviews, audit_reports, claim_jobs |
| 3 | Authentication | `app/lib/auth.py` — API-key-gated; an agent can only see/act on its own tasks; a separate admin key gates reporting |
| 4 | Background jobs | `app/lib/verification.py::run_verification_batch()` — the actual evidence checks run off the request path, same durable claim/commit pattern as the metering and social-studio capstones |
| 5 | Reporting (PDF) | `app/lib/reports.py` — a PDF audit report summarizing verified/failed/suspicious claims over a period |
| 6 | LLM integration | `app/judges/*.py` — a narrow Claude API call judges claims with no checkable artifact, with a token/cost log |

Also present, as a *second* background mechanism distinct from #4 (the glossary's own
definition of a cron job — "fixed times, no request involved" — vs. a worker draining a
queue): `app/lib/sweep.py::sweep_stale_claims()`, a scheduled sweep that auto-flags any
claim still unresolved past an SLA window.

**Non-goal.** Not building the agent itself, JARVIS's HUD, voice interface, or any task
*execution* — this is purely the verification layer an agent (or a human) calls *after* a
task is claimed done. No multi-tenant SaaS, no real production scale, no mobile app.

## Data model

```
agents
  id, name, api_key_hash, is_admin, created_at
  -- api_key_hash: sha256 of the real key. The plaintext key is shown ONCE at creation
  -- (seed script / registration response) and never stored or logged again.

tasks
  id, agent_id, title, description, created_at

claims
  id, task_id, agent_id, claim_text,
  evidence_type ('file' | 'url' | 'text' | 'command'),
  evidence_payload (json — shape depends on evidence_type, see below),
  status ('pending' | 'verifying' | 'verified' | 'failed' | 'suspicious' |
          'needs_llm_review' | 'reviewing'),
  created_at, updated_at, resolved_at, claimed_at (worker claim timestamp, same
  conditional-UPDATE pattern as capstone 3/4's durable workers)

verification_attempts
  id, claim_id, method ('file_hash' | 'url_check' | 'llm_review' | 'sla_sweep'),
  outcome ('pass' | 'fail' | 'inconclusive'), detail, checked_at
  -- append-only audit trail; a claim can have more than one attempt (e.g. a
  -- deterministic check that routes to an LLM review afterward)

llm_reviews
  id, claim_id, provider, model, verdict ('plausible' | 'suspicious' | 'insufficient_evidence'),
  confidence (0.0-1.0), reasoning, input_tokens, output_tokens, cost_micro_cents,
  raw_response (json), created_at

audit_reports
  id, period_start, period_end, generated_at, summary_json, pdf_path

claim_jobs
  id, status, total, processed, started_at, finished_at, last_error
  -- one row per verification-worker run, same shape as the prior capstones' batch_jobs
```

## Evidence types and how each is actually checked

The server never just trusts a claim because the text sounds right — every evidence type
maps to a specific, named check, and the check that can't be done deterministically is the
one routed to the LLM, not skipped:

| `evidence_type` | `evidence_payload` | How it's verified |
|---|---|---|
| `file` | `{"path": "...", "expected_sha256": "..."}` | Server checks the file exists under a sandboxed workspace root and (if given) its SHA-256 matches. Deterministic. |
| `url` | `{"url": "...", "expect_status": 200, "expect_contains": "..."}` | Server makes a real HTTP GET and checks status code and (if given) a body substring. Deterministic. |
| `text` | `{"description": "..."}` | No checkable artifact exists — always routed to LLM review. |
| `command` | `{"claimed_exit_code": 0, "log_excerpt": "..."}` | Self-reported and **cannot** be independently verified by the server (the agent could lie about its own exit code) — if no `log_excerpt` is given, auto-`suspicious` immediately (a bare claim is not evidence); if a log excerpt is given, it's routed to the LLM to sanity-check plausibility, explicitly logged as lower-trust than `file`/`url`. |

This is a deliberate design choice, not an oversight: a verification service that silently
trusted self-reported exit codes would be exactly the bug it exists to catch.

## The `LLMJudge` interface

```python
class JudgeResult(TypedDict):
    verdict: str          # 'plausible' | 'suspicious' | 'insufficient_evidence'
    confidence: float     # 0.0-1.0
    reasoning: str
    input_tokens: int
    output_tokens: int
    cost_micro_cents: int
    raw_response: dict

class LLMJudge(Protocol):
    def judge(self, *, task_description: str, claim_text: str,
              evidence_type: str, evidence_payload: dict) -> JudgeResult: ...
```

Two implementations, same shape as the publisher adapters in the social-studio capstone:

- `AnthropicJudge` (`app/judges/anthropic_judge.py`) — real. Calls the Claude Messages API
  (`https://api.anthropic.com/v1/messages`) with a small/cheap model, asks it to classify
  the claim given the task + evidence, and parses token usage into a real cost log.
- `MockJudge` (`app/judges/mock_judge.py`) — a deterministic heuristic (hedge-word
  detection: "maybe", "should have", "probably", "I think" lower confidence; concrete,
  specific detail raises it) used so the whole pipeline is provable in this sandbox without
  a paid API key, and in automated tests.

`app/judges/registry.py` is the only place that picks which one is live — same registry
pattern as the social-studio capstone's `SocialPublisher` adapters, for the same reason:
swapping judges (or adding a second provider) never touches `app/lib/verification.py`.

## Layering

Same split as every prior capstone: `app/routes/*.py` is HTTP-only; all real logic — auth,
verification, LLM judging, the sweep, PDF generation — lives in `app/lib/*.py` /
`app/judges/*.py` against a plain `sqlite3.Connection`, so `tests/` runs the exact same
functions the live API does.

## Known sandbox constraint

Every outbound host tested from this sandbox was blocked by the egress proxy except
`api.anthropic.com` (confirmed directly — `generativelanguage.googleapis.com`,
`api.openai.com`, `api.groq.com`, `openrouter.ai`, `huggingface.co`, `api.mistral.ai` all
return a proxy `403`; `api.anthropic.com` returns a real `405` on a bare `GET /v1/messages`,
meaning the connection itself goes through). `AnthropicJudge`'s request-building and
response-parsing are proven in `tests/` against a fake transport — no API key needed for
that. The literal "a real Claude API call judges a real claim" step needs a real
`ANTHROPIC_API_KEY`, which this sandbox doesn't have one to spend — same handoff pattern as
the Discord bot in the previous capstone, run on a machine with its own key and normal
internet access. See `BUILDLOG.md`.
