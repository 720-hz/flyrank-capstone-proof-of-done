"""Deterministic file/url evidence checks, including the path-traversal guard
documented as a specific, deliberately-tested security property in DESIGN.md."""
import hashlib
import os

import httpx
import pytest

import app.lib.evidence_checks as evidence_checks
from app.lib.evidence_checks import check_file_evidence, check_url_evidence


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(evidence_checks, "WORKSPACE_ROOT", str(root))
    return root


def test_check_file_evidence_passes_when_file_exists_no_hash_requested(workspace):
    (workspace / "out.txt").write_text("hello")
    outcome, detail = check_file_evidence({"path": "out.txt"})
    assert outcome == "pass"
    assert "out.txt" in detail


def test_check_file_evidence_fails_when_file_missing(workspace):
    outcome, detail = check_file_evidence({"path": "never-written.txt"})
    assert outcome == "fail"
    assert "no file found" in detail


def test_check_file_evidence_fails_when_path_is_missing(workspace):
    outcome, detail = check_file_evidence({})
    assert outcome == "fail"
    assert "no 'path'" in detail


def test_check_file_evidence_verifies_matching_sha256(workspace):
    content = b"exact bytes matter"
    (workspace / "out.bin").write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    outcome, detail = check_file_evidence({"path": "out.bin", "expected_sha256": expected})
    assert outcome == "pass"
    assert "sha256 matches" in detail


def test_check_file_evidence_catches_sha256_mismatch(workspace):
    (workspace / "out.bin").write_bytes(b"actual content")
    outcome, detail = check_file_evidence({"path": "out.bin", "expected_sha256": "0" * 64})
    assert outcome == "fail"
    assert "mismatch" in detail


@pytest.mark.parametrize("traversal", [
    "../outside.txt",
    "../../etc/passwd",
    "subdir/../../escape.txt",
])
def test_check_file_evidence_refuses_path_traversal(workspace, traversal):
    outcome, detail = check_file_evidence({"path": traversal})
    assert outcome == "fail"
    assert "escapes the workspace root" in detail


def test_check_file_evidence_neutralizes_a_leading_slash_instead_of_treating_it_as_absolute(workspace):
    # an absolute-looking path is stripped to workspace-relative rather than
    # being resolved as a real filesystem absolute path — it can't escape,
    # it just (safely) resolves to nothing inside the workspace.
    outcome, detail = check_file_evidence({"path": "/etc/passwd"})
    assert outcome == "fail"
    assert "no file found" in detail
    assert "escapes" not in detail


def test_check_file_evidence_allows_legitimate_subdirectories(workspace):
    (workspace / "sub").mkdir()
    (workspace / "sub" / "nested.txt").write_text("ok")
    outcome, _ = check_file_evidence({"path": "sub/nested.txt"})
    assert outcome == "pass"


def _client_for(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_check_url_evidence_passes_on_matching_status_and_content():
    def handler(request):
        return httpx.Response(200, text="deployment successful")

    outcome, detail = check_url_evidence(
        {"url": "https://example.com/health", "expect_contains": "successful"},
        client=_client_for(handler),
    )
    assert outcome == "pass"
    assert "200" in detail


def test_check_url_evidence_fails_on_unexpected_status():
    def handler(request):
        return httpx.Response(404, text="not found")

    outcome, detail = check_url_evidence({"url": "https://example.com/x"}, client=_client_for(handler))
    assert outcome == "fail"
    assert "404" in detail


def test_check_url_evidence_fails_when_expected_text_missing():
    def handler(request):
        return httpx.Response(200, text="something else entirely")

    outcome, detail = check_url_evidence(
        {"url": "https://example.com/x", "expect_contains": "deployment successful"},
        client=_client_for(handler),
    )
    assert outcome == "fail"
    assert "did not contain" in detail


def test_check_url_evidence_fails_when_url_missing():
    outcome, detail = check_url_evidence({})
    assert outcome == "fail"
    assert "no 'url'" in detail


def test_check_url_evidence_handles_connection_errors_without_raising():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    outcome, detail = check_url_evidence({"url": "https://example.com/x"}, client=_client_for(handler))
    assert outcome == "fail"
    assert "failed" in detail
