"""Deterministic checks for the two evidence types that CAN be checked without
an LLM. Both return (outcome, detail) where outcome is 'pass' or 'fail' —
never 'inconclusive'; an inconclusive result belongs to the LLM path only."""
import hashlib
import os

import httpx

from app.config import WORKSPACE_ROOT


def _safe_path(relative_path: str) -> str | None:
    """Resolves relative_path under WORKSPACE_ROOT and refuses anything that
    would escape it (../../etc/passwd and friends). Returns None if unsafe."""
    candidate = os.path.normpath(os.path.join(WORKSPACE_ROOT, relative_path.lstrip("/\\")))
    root_with_sep = WORKSPACE_ROOT.rstrip(os.sep) + os.sep
    if candidate != WORKSPACE_ROOT and not candidate.startswith(root_with_sep):
        return None
    return candidate


def check_file_evidence(payload: dict) -> tuple[str, str]:
    path = payload.get("path")
    if not path:
        return "fail", "no 'path' given in evidence_payload"

    full_path = _safe_path(path)
    if full_path is None:
        return "fail", f"path '{path}' escapes the workspace root - refused"

    if not os.path.isfile(full_path):
        return "fail", f"no file found at '{path}'"

    expected_sha256 = payload.get("expected_sha256")
    if expected_sha256:
        with open(full_path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        if actual.lower() != expected_sha256.lower():
            return "fail", f"sha256 mismatch: expected {expected_sha256}, got {actual}"
        return "pass", f"file exists at '{path}' and sha256 matches"

    return "pass", f"file exists at '{path}' (no hash was given to check)"


def check_url_evidence(payload: dict, *, client: httpx.Client | None = None) -> tuple[str, str]:
    url = payload.get("url")
    if not url:
        return "fail", "no 'url' given in evidence_payload"

    expect_status = payload.get("expect_status", 200)
    expect_contains = payload.get("expect_contains")

    owns_client = client is None
    client = client or httpx.Client(timeout=10.0, follow_redirects=True)
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        return "fail", f"request to '{url}' failed: {exc}"
    finally:
        if owns_client:
            client.close()

    if resp.status_code != expect_status:
        return "fail", f"'{url}' returned {resp.status_code}, expected {expect_status}"

    if expect_contains and expect_contains not in resp.text:
        return "fail", f"'{url}' response did not contain expected text '{expect_contains}'"

    return "pass", f"'{url}' returned {resp.status_code}" + (
        " and contained the expected text" if expect_contains else ""
    )
