"""HTTP-level tests: routing + the two-tier auth actually enforced over real
requests (401/403), plus one full flow proving every piece wired together
end to end through the live FastAPI app rather than lib functions directly."""


def test_health_check_needs_no_auth(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_register_agent_returns_a_plaintext_key_shown_once(api_client):
    resp = api_client.post("/v1/agents", json={"name": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "alice"
    assert body["api_key"].startswith("pod_")


def test_protected_routes_401_without_a_key(api_client):
    cases = [
        ("GET", "/v1/tasks", None),
        ("POST", "/v1/tasks", {"title": "t", "description": "d"}),  # valid body, so auth is the only failure
        ("GET", "/v1/claims", None),
    ]
    for method, path, body in cases:
        resp = api_client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} should 401 without auth"


def test_protected_routes_401_with_a_garbage_key(api_client):
    headers = {"Authorization": "Bearer pod_totally-made-up"}
    resp = api_client.get("/v1/tasks", headers=headers)
    assert resp.status_code == 401


def test_agent_cannot_read_another_agents_task(api_client, register_via_api):
    _, _, owner_headers = register_via_api("owner")
    _, _, stranger_headers = register_via_api("stranger")

    task_resp = api_client.post("/v1/tasks", json={"title": "secret plan", "description": "shh"},
                                 headers=owner_headers)
    task_id = task_resp.json()["id"]

    resp = api_client.get(f"/v1/tasks/{task_id}", headers=stranger_headers)
    assert resp.status_code == 403


def test_agent_cannot_create_a_claim_against_another_agents_task(api_client, register_via_api):
    _, _, owner_headers = register_via_api("owner")
    _, _, stranger_headers = register_via_api("stranger")

    task_resp = api_client.post("/v1/tasks", json={"title": "t", "description": "d"}, headers=owner_headers)
    task_id = task_resp.json()["id"]

    resp = api_client.post(
        "/v1/claims",
        json={"task_id": task_id, "claim_text": "I did your task", "evidence_type": "text", "evidence_payload": {}},
        headers=stranger_headers,
    )
    assert resp.status_code == 403


def test_agent_cannot_read_another_agents_claim(api_client, register_via_api):
    _, _, owner_headers = register_via_api("owner")
    _, _, stranger_headers = register_via_api("stranger")

    task_id = api_client.post("/v1/tasks", json={"title": "t", "description": "d"},
                               headers=owner_headers).json()["id"]
    claim_id = api_client.post(
        "/v1/claims",
        json={"task_id": task_id, "claim_text": "x", "evidence_type": "text", "evidence_payload": {}},
        headers=owner_headers,
    ).json()["id"]

    resp = api_client.get(f"/v1/claims/{claim_id}", headers=stranger_headers)
    assert resp.status_code == 403


def test_claim_rejects_unknown_evidence_type(api_client, register_via_api):
    _, _, headers = register_via_api()
    task_id = api_client.post("/v1/tasks", json={"title": "t", "description": "d"}, headers=headers).json()["id"]

    resp = api_client.post(
        "/v1/claims",
        json={"task_id": task_id, "claim_text": "x", "evidence_type": "clairvoyance", "evidence_payload": {}},
        headers=headers,
    )
    assert resp.status_code == 422


def test_reports_require_admin_key_not_an_agent_key(api_client, register_via_api):
    _, _, agent_headers = register_via_api()
    resp = api_client.post(
        "/v1/reports",
        json={"period_start": "2000-01-01T00:00:00+00:00", "period_end": "2100-01-01T00:00:00+00:00"},
        headers=agent_headers,
    )
    assert resp.status_code == 401


def test_reports_401_without_any_key(api_client):
    resp = api_client.post(
        "/v1/reports",
        json={"period_start": "2000-01-01T00:00:00+00:00", "period_end": "2100-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 401


def test_reports_succeed_with_the_real_admin_key(api_client, admin_key):
    resp = api_client.post(
        "/v1/reports",
        json={"period_start": "2000-01-01T00:00:00+00:00", "period_end": "2100-01-01T00:00:00+00:00"},
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert resp.status_code == 200
    assert resp.json()["summary"]["total_claims"] == 0


def test_full_flow_false_file_claim_gets_marked_failed(api_client, register_via_api, tmp_path, monkeypatch):
    import app.lib.evidence_checks as evidence_checks

    workspace = tmp_path / "flow-workspace"
    workspace.mkdir()
    monkeypatch.setattr(evidence_checks, "WORKSPACE_ROOT", str(workspace))

    _, _, headers = register_via_api("agent-under-test")
    task_id = api_client.post(
        "/v1/tasks", json={"title": "write hello.txt", "description": "write hello.txt to the workspace root"},
        headers=headers,
    ).json()["id"]

    claim_resp = api_client.post(
        "/v1/claims",
        json={"task_id": task_id, "claim_text": "I wrote hello.txt", "evidence_type": "file",
              "evidence_payload": {"path": "hello.txt"}},
        headers=headers,
    )
    claim_id = claim_resp.json()["id"]
    assert claim_resp.json()["status"] == "pending"

    run_resp = api_client.post("/v1/verification-runs", headers=headers)
    assert run_resp.json()["processed"] == 1

    final = api_client.get(f"/v1/claims/{claim_id}", headers=headers).json()
    assert final["status"] == "failed"

    attempts = api_client.get(f"/v1/claims/{claim_id}/attempts", headers=headers).json()["attempts"]
    assert len(attempts) == 1
    assert "no file found" in attempts[0]["detail"]


def test_full_flow_text_claim_routes_through_llm_review_to_resolution(api_client, register_via_api):
    _, _, headers = register_via_api("agent-under-test")
    task_id = api_client.post(
        "/v1/tasks", json={"title": "summarize the doc", "description": "summarize the shared doc"},
        headers=headers,
    ).json()["id"]

    claim_id = api_client.post(
        "/v1/claims",
        json={"task_id": task_id, "claim_text": "I summarized doc.md into a 3-point overview and sent it to #team",
              "evidence_type": "text", "evidence_payload": {}},
        headers=headers,
    ).json()["id"]

    assert api_client.post("/v1/verification-runs", headers=headers).json()["processed"] == 1
    mid = api_client.get(f"/v1/claims/{claim_id}", headers=headers).json()
    assert mid["status"] == "needs_llm_review"

    assert api_client.post("/v1/llm-review-runs", headers=headers).json()["processed"] == 1
    final = api_client.get(f"/v1/claims/{claim_id}", headers=headers).json()
    assert final["status"] in ("verified", "suspicious")  # resolved one way or the other, not stuck
    assert final["resolved_at"] is not None
