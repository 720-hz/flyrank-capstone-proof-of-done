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

**Status: pending a real-world run on a machine with normal internet access and a real
`ANTHROPIC_API_KEY`.** This sandbox's egress policy allows `api.anthropic.com` through (see
`DESIGN.md`'s "Known sandbox constraint" section — confirmed directly by testing 12 candidate
providers) but no API key is available here to spend. `AnthropicJudge`'s request-building and
response-parsing are already proven end to end in `tests/test_judges.py` against a fake
`httpx` transport — the forced `submit_verdict` tool call is built correctly, a real
`tool_use` response is parsed correctly, and non-200/connection-error/missing-tool-call cases
all degrade to `insufficient_evidence` instead of crashing. What's not yet proven here is the
literal "a real Claude model looked at a real claim and returned a real verdict" step — same
handoff pattern as the Discord bot in the social-studio capstone: run on a machine with its
own key, not spent here.

<!-- Filled in after running on a real machine with ANTHROPIC_API_KEY set and LLM_JUDGE=anthropic:
     the real request/response pair, the real token usage, and the real computed cost. -->
