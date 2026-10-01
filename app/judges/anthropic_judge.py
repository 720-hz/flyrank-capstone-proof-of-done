"""The one REAL LLM judge. A real Claude API call, forced into a structured
tool-call response so parsing doesn't depend on the model choosing to format
its prose a certain way. httpx.Client is accepted as a constructor arg (not
imported-and-called inline) specifically so tests can pass a fake transport
and prove this adapter's request-building/response-parsing without any
network access — see tests/test_anthropic_judge.py. The genuinely-real call
(a live API key) runs on a machine with normal internet access; this
sandbox's egress policy allows api.anthropic.com through but no key is
available here to spend — see DESIGN.md and EVIDENCE.md."""
import httpx

from app.config import ANTHROPIC_API_BASE, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from app.judges.base import JudgeResult
from app.lib.money import compute_cost_micro_cents

_TOOL = {
    "name": "submit_verdict",
    "description": "Submit your verdict on whether a task-completion claim is plausible given its evidence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["plausible", "suspicious", "insufficient_evidence"],
            },
            "confidence": {
                "type": "number",
                "description": "0.0 to 1.0",
            },
            "reasoning": {
                "type": "string",
                "description": "One or two sentences explaining the verdict.",
            },
        },
        "required": ["verdict", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = (
    "You are a skeptical auditor reviewing whether an AI agent's claim that it "
    "completed a task is credible, given only the task description, the agent's "
    "claim text, and whatever evidence it supplied (which may be empty or vague). "
    "You cannot independently check files or URLs here — only the claim's internal "
    "plausibility, specificity, and consistency. Vague, hedged, or suspiciously "
    "generic claims should be marked 'suspicious'. Claims with no real substance "
    "to judge should be marked 'insufficient_evidence'. Only mark 'plausible' when "
    "the claim is specific, internally consistent, and not obviously evasive."
)


class AnthropicJudge:
    provider = "anthropic"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key if api_key is not None else ANTHROPIC_API_KEY
        self.model = model if model is not None else ANTHROPIC_MODEL
        self._client = client

    def judge(
        self, *, task_description: str, claim_text: str,
        evidence_type: str, evidence_payload: dict,
    ) -> JudgeResult:
        user_content = (
            f"Task description:\n{task_description}\n\n"
            f"Agent's completion claim:\n{claim_text}\n\n"
            f"Evidence type: {evidence_type}\n"
            f"Evidence payload: {evidence_payload}\n\n"
            "Call submit_verdict with your assessment."
        )

        body = {
            "model": self.model,
            "max_tokens": 300,
            "system": _SYSTEM_PROMPT,
            "tools": [_TOOL],
            "tool_choice": {"type": "tool", "name": "submit_verdict"},
            "messages": [{"role": "user", "content": user_content}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        client = self._client or httpx.Client(timeout=30.0)
        owns_client = self._client is None
        try:
            resp = client.post(f"{ANTHROPIC_API_BASE}/v1/messages", headers=headers, json=body)
            data = {}
            try:
                data = resp.json()
            except ValueError:
                pass

            if resp.status_code != 200:
                error_msg = data.get("error", {}).get("message", resp.text)
                return JudgeResult(
                    verdict="insufficient_evidence",
                    confidence=0.0,
                    reasoning=f"LLM call failed (HTTP {resp.status_code}): {error_msg}",
                    input_tokens=0,
                    output_tokens=0,
                    cost_micro_cents=0,
                    raw_response=data,
                )

            tool_input = _extract_tool_input(data)
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)

            if tool_input is None:
                return JudgeResult(
                    verdict="insufficient_evidence",
                    confidence=0.0,
                    reasoning="model did not return a submit_verdict tool call",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_micro_cents=compute_cost_micro_cents(input_tokens, output_tokens),
                    raw_response=data,
                )

            return JudgeResult(
                verdict=tool_input.get("verdict", "insufficient_evidence"),
                confidence=float(tool_input.get("confidence", 0.0)),
                reasoning=tool_input.get("reasoning", ""),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_micro_cents=compute_cost_micro_cents(input_tokens, output_tokens),
                raw_response=data,
            )
        except httpx.HTTPError as exc:
            return JudgeResult(
                verdict="insufficient_evidence",
                confidence=0.0,
                reasoning=f"LLM call raised {type(exc).__name__}: {exc}",
                input_tokens=0,
                output_tokens=0,
                cost_micro_cents=0,
                raw_response={},
            )
        finally:
            if owns_client:
                client.close()


def _extract_tool_input(data: dict) -> dict | None:
    for block in data.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "submit_verdict":
            return block.get("input")
    return None
