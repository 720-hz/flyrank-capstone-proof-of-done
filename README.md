# ProofOfDone

A verification backend that sits between an AI agent and "done." An agent (or a human)
submits a claim that a task was completed, along with whatever evidence it has. ProofOfDone
checks it automatically — deterministically where that's possible (a file really exists, a
URL really returns what's claimed), by LLM review where it isn't (there's no checkable
artifact, or the evidence is just self-reported) — and only then marks it resolved.

This exists because of a real, observed bug: an AI agent reporting "done" on a task with no
real output produced. See [`DESIGN.md`](./DESIGN.md) for the full problem statement, data
model, and the evidence-routing design (the actual interesting part of this project).
[`BUILDLOG.md`](./BUILDLOG.md) has what broke during the build and how it got fixed.
[`EVIDENCE.md`](./EVIDENCE.md) has the real end-to-end proof this runs, including the one
piece (a real Claude API call) that needed a real API key run on a normal machine.

## Quickstart

Requires Python 3.11+. $0 to run — the default LLM judge is a deterministic mock, no API key
needed.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # defaults work as-is; see the file for what each setting does

python3 scripts/seed.py          # creates a demo agent + one claim per evidence type/outcome,
                                  # and actually runs the two workers + the sweep against them
```

The seed script prints a demo agent API key and the admin key, plus two ready-to-paste curl
commands. Then start the server in another terminal:

```bash
source .venv/bin/activate
uvicorn app.main:app --port 8000
```

```bash
# list the seeded claims and their real resolved statuses
curl -s http://localhost:8000/v1/claims -H "Authorization: Bearer <agent key from seed output>" | python3 -m json.tool

# generate a PDF audit report covering every seeded claim, admin-only
curl -s -X POST http://localhost:8000/v1/reports \
  -H "Authorization: Bearer <ADMIN_API_KEY from .env>" -H "Content-Type: application/json" \
  -d '{"period_start":"2000-01-01T00:00:00+00:00","period_end":"2100-01-01T00:00:00+00:00"}'
# -> {"id": 1, ...}; download it:
curl -s -o audit.pdf http://localhost:8000/v1/reports/1/pdf -H "Authorization: Bearer <ADMIN_API_KEY>"
```

Interactive API docs (Swagger UI) are at `http://localhost:8000/docs` once the server is running.

## Try it from scratch (not the seed script)

Claims and tasks are owned by whichever agent created them — an agent can only act on its
own (a cross-agent request is a 403, proven in `tests/test_api.py`). The IDs below are
captured from each response rather than hardcoded, so this works whether or not you've
already run `scripts/seed.py` (which creates its own agent + task #1):

```bash
# 1. register an agent — the API key is shown exactly once, right here
KEY=$(curl -s -X POST http://localhost:8000/v1/agents -H "Content-Type: application/json" \
  -d '{"name": "my-agent"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")

# 2. create a task
TASK_ID=$(curl -s -X POST http://localhost:8000/v1/tasks -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d '{"title": "write hello.txt", "description": "write hello.txt into the workspace root"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 3. submit a claim with no real evidence behind it (deliberately false)
CLAIM_ID=$(curl -s -X POST http://localhost:8000/v1/claims -H "Content-Type: application/json" \
  -H "Authorization: Bearer $KEY" \
  -d "{\"task_id\": $TASK_ID, \"claim_text\": \"I wrote hello.txt\", \"evidence_type\": \"file\", \"evidence_payload\": {\"path\": \"hello.txt\"}}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 4. run the verification worker (normally this runs on a schedule, not per-request)
curl -s -X POST http://localhost:8000/v1/verification-runs -H "Authorization: Bearer $KEY"

# 5. check the claim — it's 'failed', with a real reason, because the file never existed
curl -s http://localhost:8000/v1/claims/$CLAIM_ID -H "Authorization: Bearer $KEY"
curl -s http://localhost:8000/v1/claims/$CLAIM_ID/attempts -H "Authorization: Bearer $KEY"
```

## Running with the real LLM judge

By default `LLM_JUDGE=mock` in `.env` — a deterministic heuristic judge, $0, no network call,
used so the whole pipeline is provable without a paid key. To use a real Claude API call
instead:

```
# in .env
ANTHROPIC_API_KEY=sk-ant-...
LLM_JUDGE=anthropic
```

No code change — `app/judges/registry.py` is the only place that picks the live judge, and
`app/lib/llm_review.py` only ever calls through the shared `LLMJudge` interface. See
`EVIDENCE.md` for a real transcript of this.

## API reference

| Method | Path | Auth | What |
|---|---|---|---|
| POST | `/v1/agents` | none | Register an agent, get back a plaintext API key (shown once) |
| POST | `/v1/tasks` | agent | Create a task |
| GET | `/v1/tasks` | agent | List your own tasks |
| GET | `/v1/tasks/{id}` | agent, owner-only | Read one task (403 if it's not yours) |
| POST | `/v1/claims` | agent | Submit a completion claim against your own task |
| GET | `/v1/claims` | agent | List your own claims, optional `?status=` filter |
| GET | `/v1/claims/{id}` | agent, owner-only | Read one claim |
| GET | `/v1/claims/{id}/attempts` | agent, owner-only | Full verification history for one claim |
| POST | `/v1/verification-runs` | any agent | Run the deterministic (file/url) worker over due claims |
| POST | `/v1/llm-review-runs` | any agent | Run the LLM-review worker over claims routed to it |
| POST | `/v1/sweep-runs` | any agent | Run the SLA sweep (meant to be hit by a scheduler, not a user) |
| POST | `/v1/reports` | admin only | Generate a PDF audit report for a period |
| GET | `/v1/reports/{id}` | admin only | Read a report's JSON summary |
| GET | `/v1/reports/{id}/pdf` | admin only | Download the PDF |
| GET | `/health` | none | Liveness check |

The `*-runs` endpoints are worker endpoints, not per-agent data endpoints — any authenticated
agent can trigger a sweep of all due claims system-wide (ownership is still enforced on every
route that reads or writes actual claim/task *data*). In production these would be called by a
scheduler (cron / Windows Task Scheduler), not a user — see `app/lib/sweep.py`'s docstring for
why the sweep is deliberately kept separate from the two queue-draining workers.

## The 6 concepts, and where they live

| Concept | Where |
|---|---|
| API endpoints | `app/routes/*.py` |
| Database | `app/db.py` — plain `sqlite3`, 7 tables |
| Authentication | `app/lib/auth.py` — per-agent API keys + a separate admin key, both actually enforced (403 on cross-agent access, not just "logged in or not") |
| Background jobs | `app/lib/verification.py`, `app/lib/llm_review.py` — durable, crash-safe workers (claim/commit/resolve pattern) |
| Reporting (PDF) | `app/lib/reports.py` |
| LLM integration | `app/judges/*.py` — real Claude API judge + mock judge, swappable by one config line |

Plus a second, distinct background mechanism: `app/lib/sweep.py`, a cron-style scheduled
sweep (see `DESIGN.md` for why it's deliberately not the same thing as the two workers above).

## Running the tests

```bash
source .venv/bin/activate
pytest
```

101 tests, fully offline (no real network, no real API key, no `sleep()` — the durable-worker
staleness tests pass an explicit `now` instead of waiting for real time to pass), in under 2
seconds. Covers: auth enforcement (both the lib-level ownership checks and real 401/403s over
HTTP), evidence-type routing for all four evidence types, the claim/reclaim durable-worker
pattern (including simulating a crashed worker), the LLM judge adapter swap (mock + a real
Claude call proven against a fake transport, no network needed), the SLA sweep, PDF report
generation (including the Latin-1 safety net for arbitrary claim text), and cost math.

## Project structure

```
app/
  config.py        All tunable constants (reads .env)
  db.py             SQLite schema + connection helper
  deps.py           FastAPI auth dependencies (turns lib exceptions into 401/403)
  main.py           FastAPI app + router wiring + startup
  schemas.py        Pydantic request bodies
  lib/              All real logic — auth, claims, evidence checks, the two durable
                    workers, the sweep, reports, cost math. Pure functions against a
                    plain sqlite3.Connection; tests/ calls these directly.
  judges/           The LLMJudge interface + MockJudge + AnthropicJudge + registry
  routes/           HTTP-only route handlers
scripts/
  seed.py           Demo data + a full run of both workers + the sweep
tests/              101 tests, see above
DESIGN.md           Problem statement, data model, evidence-routing design
BUILDLOG.md         What broke during the build and how it got fixed
EVIDENCE.md         Real end-to-end proof, including the real Claude API run
```
