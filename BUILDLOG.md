# Build log

What actually broke while building this, and how it got fixed. Kept honest on purpose —
same convention as the previous capstones' BUILDLOG.md files.

## Missing `'reviewing'` claim status in the schema's CHECK constraint

While writing the second durable worker (`app/lib/llm_review.py`), I introduced a
`'reviewing'` claim status for its claim step — mirroring `'verifying'` in the deterministic
worker (`app/lib/verification.py`). But the original `app/db.py` schema's `CHECK` constraint
on `claims.status` only listed `('pending','verifying','verified','failed','suspicious',
'needs_llm_review')` — no `'reviewing'`. Caught before running anything, by re-reading the
schema against the new worker code, not by hitting the constraint violation at runtime. Fixed
by adding `'reviewing'` to both the SQL `CHECK` in `app/db.py` and `CLAIM_STATUSES` in
`app/config.py`.

## `FPDFUnicodeEncodingException` on an em-dash

First `POST /v1/reports` smoke test failed immediately: `Character "—" at index 12 ... is
outside the range of characters supported by the font used: "helveticaB"`. fpdf2's core
Helvetica font only supports Latin-1, and the PDF title/headers in `app/lib/reports.py` used
em-dashes. Fixed the literal strings, but that's only half the problem — `claim_text` and
LLM `reasoning` are arbitrary strings nothing in this codebase controls, so *any* non-Latin-1
character (an emoji, a curly quote, non-English text) would crash report generation the same
way. Added `_safe()` — `text.encode("latin-1", errors="replace").decode("latin-1")` — and
wrapped every dynamic string that reaches the PDF in it. `tests/test_reports.py` has a
dedicated test that generates a report from claim text containing an emoji and an em-dash to
prove this doesn't regress.

## `fpdf2`'s `multi_cell` cursor positioning

After the Latin-1 fix, the same endpoint failed differently: `fpdf.errors.FPDFException: Not
enough horizontal space to render a single character`, raised on the *second* `multi_cell()`
call inside the per-claim loop (the first one, printing the claim header, worked fine).
Introspected the signature (`inspect.signature(FPDF.multi_cell)`) and found the default is
`new_x=XPos.RIGHT, new_y=YPos.NEXT` — unlike every `cell()` call elsewhere in the same file,
which explicitly passes `new_x="LMARGIN", new_y="NEXT"`. Left at its default, the first
`multi_cell()` call leaves the X cursor near the right margin; the next call's `w=0` (meaning
"use all remaining width to the right margin from the current X") then has almost no width to
work with. Fixed by adding the same explicit `new_x="LMARGIN", new_y="NEXT"` to all three
`multi_cell()` calls in the loop. Verified by rendering the resulting PDF to an image
(`pdftoppm`) and actually looking at it, not just checking the HTTP call stopped 500ing — the
exception goes away either way if you only fix the symptom you were shown, so the visual check
mattered here.

## Egress testing before committing to an LLM provider

Before writing `AnthropicJudge`, tested direct HTTP reachability from this sandbox against 12
candidate providers: `generativelanguage.googleapis.com`, `api.openai.com`,
`api.anthropic.com`, `api.groq.com`, `openrouter.ai`, `api.cohere.com`,
`api-inference.huggingface.co`, `huggingface.co`, `api.mistral.ai`, `api.together.xyz`,
`api.cerebras.ai`, `api.deepseek.com`. Only `api.anthropic.com` came back with a real HTTP
status (`405` on a bare `GET /v1/messages`, meaning the request actually reached the API and
got a real "wrong method" response) — everything else returned a proxy `403`, meaning the
egress policy blocked the connection before it ever reached the provider. This determined the
real judge targets Claude specifically, not "whichever provider" — documented in `DESIGN.md`
rather than discovered as a surprise mid-build.

## `_safe_path`'s handling of an absolute-looking input path

Writing `tests/test_evidence_checks.py`'s path-traversal tests, I initially expected
`{"path": "/etc/passwd"}` to be refused the same way as `"../../etc/passwd"` (an "escapes the
workspace root" failure). It isn't — `_safe_path()` does
`relative_path.lstrip("/\\")` before joining it under `WORKSPACE_ROOT`, so a leading slash is
stripped rather than treated as a real filesystem absolute path. `/etc/passwd` becomes
`etc/passwd` under the workspace root, which doesn't exist there, so it resolves to an
ordinary "no file found" — not a security hole, just not the same failure mode my first draft
of the test assumed. Fixed the test to assert what the code actually (correctly) does, with a
comment explaining why, rather than changing the code to match a wrong assumption.

## Sandbox process lifecycle, while manually smoke-testing

Not a bug in the app — a lesson about this build sandbox. Background `uvicorn` processes
started with `&` inside one shell tool call don't reliably survive into the next call the way
a normal persistent terminal would, and separately, `pkill -f uvicorn` matches against the
*invoking shell's own command line* (which contains the literal string "uvicorn" because
that's the command being run) and can kill its own parent process before anything after it
executes. Worked around by always starting the server and running whatever curl/test sequence
needed it inside one shell invocation, and by killing by exact PID (from `ss -ltnp` / `pgrep`)
instead of `pkill -f` when a server needed to be stopped and restarted across calls.
