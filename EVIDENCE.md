# Evidence

Real transcripts, not descriptions of what should happen. Everything below ran against the
actual code in this repo — no step is hand-edited or abbreviated except where noted.

## Automated test suite

```
$ pytest -q
........................................................................ [ 71%]
.............................                                            [100%]
101 passed, 1 warning in 1.50s
```

101 tests, fully offline — no real network call, no real API key, no `sleep()`. Covers every
evidence-type routing path, the durable-worker claim/reclaim pattern (including a simulated
crashed worker), the LLM judge registry swap, auth enforcement at both the lib level and over
real HTTP (401/403), the SLA sweep, PDF report generation (including the Latin-1 safety net),
and the cost math. See `README.md`'s "Running the tests" section for what each file covers.

## `scripts/seed.py` — demo data + both workers + the sweep, for real

```
$ python3 scripts/seed.py
========================================================================
Seed complete.
========================================================================
Demo agent API key (shown once, exactly like a real registration): pod_HcN6vBLa0FvCoRk-EhisObw4XO53AUJvpRH01as1Hrs
Admin key (from .env, ADMIN_API_KEY): 'changeme-admin-key'

verification-run:  {'job_id': 1, 'total': 7, 'processed': 7}
llm-review-run:    {'job_id': 2, 'total': 3, 'processed': 3}
sweep-run:         {'swept_at': '2026-10-01T02:05:52.386178+00:00', 'sla_seconds': 300, 'flagged': 1}
```

8 claims seeded (one per evidence type/outcome combination), 7 picked up by the deterministic
worker, 3 of those routed onward to the LLM worker (MockJudge — $0, no key), and 1 left
unresolved on purpose and caught only by the separate SLA sweep. Full resolved state, read
back from the live running server with a real `GET /v1/claims`:

```
$ curl -s http://localhost:8000/v1/claims -H "Authorization: Bearer pod_HcN6..." | python3 -m json.tool
```

| id | evidence_type | status | why |
|---|---|---|---|
| 1 | file | `verified` | file really exists in the workspace, deterministic check |
| 2 | file | `failed` | file genuinely doesn't exist, deterministic check |
| 3 | url | `failed` | real HTTP GET to `https://example.com`; this sandbox's egress proxy blocks it (`403`) — on a normal machine with real internet access this resolves `verified` instead, which is itself the point: a real network call, not a canned result |
| 4 | text | `verified` | routed to LLM review (no checkable artifact); MockJudge found it specific and plausible |
| 5 | text | `suspicious` | routed to LLM review; MockJudge flagged the hedge words ("I think", "probably", "not totally sure") |
| 6 | command | `suspicious` | no `log_excerpt` given — auto-flagged immediately, never even reached the LLM step |
| 7 | command | `verified` | `log_excerpt` given, routed to LLM review, found plausible |
| 8 | text | `suspicious` | left pending, backdated 2 hours, caught by the **SLA sweep** specifically — neither worker ever touched it (see `scripts/seed.py`'s comment on why it's created *after* the worker runs) |

Full `GET /v1/claims` JSON and the admin `POST /v1/reports` response for this exact run are
preserved in `evidence/claims_and_report_transcript.json` alongside this file.

## PDF audit report

Generated from the same run, downloaded with `GET /v1/reports/1/pdf`, and actually rendered to
an image and read (not just checked for "didn't 500") — `pdftoppm -jpeg -r 150
audit_report.pdf page` — to confirm the layout is correct: all three claims' text, no
overlapping lines, no `multi_cell` cursor artifacts (see `BUILDLOG.md` for the bug this
caught during build), no mis-encoded characters from the em-dash/Latin-1 issue.

```
$ curl -s -X POST http://localhost:8000/v1/reports -H "Authorization: Bearer changeme-admin-key" \
    -H "Content-Type: application/json" \
    -d '{"period_start":"2000-01-01T00:00:00+00:00","period_end":"2100-01-01T00:00:00+00:00"}'
{"id":1,...,"summary":{"total_claims":8,"status_counts":{"verified":3,"failed":2,"suspicious":3},
 "flagged_count":5,"flagged_claim_ids":[2,3,5,6,8]},...}

$ curl -s -o audit_report.pdf http://localhost:8000/v1/reports/1/pdf -H "Authorization: Bearer changeme-admin-key"
$ file audit_report.pdf
audit_report.pdf: PDF document, version 1.3, 1 page(s)
```

## The real Claude API judge

Run on a real machine with normal internet access, a real `ANTHROPIC_API_KEY`, and
`LLM_JUDGE=anthropic` set in `.env` — same handoff pattern as the Discord bot in the
social-studio capstone: `AnthropicJudge`'s request-building and response-parsing were already
proven in `tests/test_judges.py` against a fake transport with no network access, but the
literal "a real Claude model looked at a real claim and returned a real verdict" step needed a
real key spent on a real machine, not this sandbox (whose egress policy allows
`api.anthropic.com` through but has no key to spend — see `DESIGN.md`'s "Known sandbox
constraint").

`python scripts/seed.py` was re-run with that config active, so its internal
`run_llm_review_batch()` call made three real requests to `claude-haiku-4-5-20251001` — one per
seeded claim that needed LLM review. Read directly from the `llm_reviews` table afterward:

```json
[
  {
    "claim_id": 4,
    "claim_text": "Posted the Q3 summary (revenue $41,200, up 6%) to #finance at 2pm.",
    "provider": "anthropic", "model": "claude-haiku-4-5",
    "verdict": "suspicious", "confidence": 0.85,
    "reasoning": "The agent claims to have completed the task but only provides evidence of posting a summary to #finance; it makes no mention of writing q3-report.csv to the workspace, which was explicitly required. The evidence payload is empty, providing no concrete proof of either action, and the specific financial figures appear generic without supporting documentation.",
    "input_tokens": 940, "output_tokens": 133, "cost_micro_cents": 128400
  },
  {
    "claim_id": 5,
    "claim_text": "I think I probably posted something about the report, not totally sure.",
    "provider": "anthropic", "model": "claude-haiku-4-5",
    "verdict": "suspicious", "confidence": 0.95,
    "reasoning": "The agent's claim is extremely vague and hedged (\"I think I probably,\" \"not totally sure\"), providing no concrete evidence that either file was written to the workspace or that any message was posted to #finance. The empty evidence payload offers nothing substantive to verify task completion.",
    "input_tokens": 929, "output_tokens": 124, "cost_micro_cents": 123920
  },
  {
    "claim_id": 7,
    "claim_text": "Ran the publish script, exit code 0.",
    "provider": "anthropic", "model": "claude-haiku-4-5",
    "verdict": "plausible", "confidence": 0.75,
    "reasoning": "The agent claims successful task completion with exit code 0 and provides a log excerpt showing \"uploaded q3-report.csv (12KB) ... done\", which directly supports both parts of the task (writing the file to workspace and posting). The evidence is specific and consistent with successful execution, though it doesn't explicitly confirm the #finance channel post was completed.",
    "input_tokens": 958, "output_tokens": 143, "cost_micro_cents": 133840
  }
]
```

Each `raw_response` (preserved in full in `evidence/real_anthropic_judge_transcript.json`)
confirms the forced-tool-use design worked exactly as intended in production: every response's
`stop_reason` is `"tool_use"`, with a `submit_verdict` tool call carrying structured
`verdict`/`confidence`/`reasoning` — never free-form prose that would need parsing.

Total real cost for all three calls: 386,160 micro-cents — a little under half a cent
(`format_usd` renders it as `$0.00 (+386160 sub-cent micro-cents)`, which is itself the
intended behavior: real spend this small should show as real spend, not get silently rounded
to "$0.00" and look free).

**The real judge caught something the mock judge in this repo's own sandbox smoke test
missed.** Claim #4 ("Posted the Q3 summary... to #finance") was marked `verified` by
`MockJudge` during this repo's sandbox testing (its keyword heuristic saw a specific channel,
a dollar figure, and a percentage, and called that "specific enough"). The real Claude judge
marked the same claim `suspicious` — correctly noticing the claim never actually asserts the
file was written to the workspace, only that a summary was posted, which is a real gap in task
completion a simple heuristic can't reliably catch. This is exactly the gap `MockJudge`'s
docstring says it exists to stand in for, not replace.
