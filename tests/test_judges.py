"""AnthropicJudge (the real LLM integration) and the registry that swaps
judges by config, not by code. AnthropicJudge accepts an httpx.Client
constructor arg specifically so request-building and response-parsing can be
proven here without any real network access or API key — see its docstring
and DESIGN.md / EVIDENCE.md for why api.anthropic.com is the only reachable
provider from this sandbox."""
import json

import httpx
import pytest

from app.judges.anthropic_judge import AnthropicJudge
from app.judges.base import JudgeResult
from app.judges.mock_judge import MockJudge
from app.judges.registry import get_judge


def _client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _tool_use_response(verdict="plausible", confidence=0.85, reasoning="specific and consistent"):
    return httpx.Response(
        200,
        json={
            "content": [
                {
                    "type": "tool_use",
                    "name": "submit_verdict",
                    "input": {"verdict": verdict, "confidence": confidence, "reasoning": reasoning},
                }
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40},
        },
    )


def test_anthropic_judge_builds_a_forced_tool_use_request():
    captured = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return _tool_use_response()

    judge = AnthropicJudge(api_key="test-key", model="claude-haiku-4-5", client=_client_for(handler))
    judge.judge(task_description="build a website", claim_text="I built it and deployed to prod",
                evidence_type="text", evidence_payload={})

    assert captured["url"].endswith("/v1/messages")
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["body"]["model"] == "claude-haiku-4-5"
    assert captured["body"]["tool_choice"] == {"type": "tool", "name": "submit_verdict"}
    assert captured["body"]["tools"][0]["name"] == "submit_verdict"
    assert "build a website" in captured["body"]["messages"][0]["content"]


def test_anthropic_judge_parses_tool_use_response_into_judge_result():
    judge = AnthropicJudge(
        api_key="test-key",
        client=_client_for(lambda r: _tool_use_response(verdict="suspicious", confidence=0.2, reasoning="vague")),
    )
    result = judge.judge(task_description="x", claim_text="y", evidence_type="text", evidence_payload={})

    assert result["verdict"] == "suspicious"
    assert result["confidence"] == 0.2
    assert result["reasoning"] == "vague"
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 40
    assert result["cost_micro_cents"] > 0  # real token usage must cost something, never silently free


def test_anthropic_judge_handles_non_200_without_raising():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    judge = AnthropicJudge(api_key="bad-key", client=_client_for(handler))
    result = judge.judge(task_description="x", claim_text="y", evidence_type="text", evidence_payload={})

    assert result["verdict"] == "insufficient_evidence"
    assert result["confidence"] == 0.0
    assert "invalid x-api-key" in result["reasoning"]
    assert result["cost_micro_cents"] == 0


def test_anthropic_judge_handles_missing_tool_call_gracefully():
    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "I refuse to use tools"}],
                                          "usage": {"input_tokens": 5, "output_tokens": 5}})

    judge = AnthropicJudge(api_key="test-key", client=_client_for(handler))
    result = judge.judge(task_description="x", claim_text="y", evidence_type="text", evidence_payload={})

    assert result["verdict"] == "insufficient_evidence"
    assert "did not return a submit_verdict" in result["reasoning"]


def test_anthropic_judge_handles_connection_errors_without_raising():
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)

    judge = AnthropicJudge(api_key="test-key", client=_client_for(handler))
    result = judge.judge(task_description="x", claim_text="y", evidence_type="text", evidence_payload={})

    assert result["verdict"] == "insufficient_evidence"
    assert "ConnectError" in result["reasoning"]


def test_registry_defaults_to_mock_judge(monkeypatch):
    monkeypatch.setattr("app.judges.registry.LLM_JUDGE", "mock")
    judge = get_judge()
    assert isinstance(judge, MockJudge)


def test_registry_switches_to_anthropic_by_config_alone(monkeypatch):
    monkeypatch.setattr("app.judges.registry.LLM_JUDGE", "anthropic")
    judge = get_judge()
    assert isinstance(judge, AnthropicJudge)


def test_registry_override_always_wins_regardless_of_config():
    # proves app/lib/llm_review.py's injectable `judge=` param (used by every
    # test above) and a live LLM_JUDGE=anthropic config can never collide —
    # the override takes priority no matter what.
    class ThrowawayJudge:
        provider = "throwaway"
        model = "v0"

        def judge(self, **kwargs) -> JudgeResult:
            return JudgeResult(verdict="plausible", confidence=1.0, reasoning="", input_tokens=0,
                                output_tokens=0, cost_micro_cents=0, raw_response={})

    override = ThrowawayJudge()
    assert get_judge(override) is override
