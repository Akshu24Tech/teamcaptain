"""One client for every free provider: Groq, Ollama and Gemini all speak the OpenAI API.

`complete_json` never trusts the model: output is parsed, validated against a pydantic
schema, and on failure the validation error is fed back for a bounded number of repairs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from tc.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class LLMUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    repairs: int = 0


class ChatModel(Protocol):
    """Anything that turns (system, user) into text. Lets tests use a fake model."""

    name: str

    async def chat(self, system: str, user: str) -> tuple[str, int, int]: ...


class OpenAICompatibleModel:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.name = model or settings.llm_model
        self._client = AsyncOpenAI(
            base_url=base_url or settings.llm_base_url,
            # Ollama ignores the key, but the SDK requires a non-empty string.
            api_key=api_key or settings.llm_api_key or "not-needed",
            timeout=settings.llm_timeout_s,
        )

    async def chat(self, system: str, user: str) -> tuple[str, int, int]:
        response = await self._client.chat.completions.create(
            model=self.name,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        return (
            text,
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text.strip()


async def complete_json(
    model: ChatModel, system: str, user: str, schema: type[T]
) -> tuple[T, LLMUsage]:
    usage = LLMUsage(model=model.name)
    started = time.perf_counter()
    prompt = user
    last_error = ""
    for attempt in range(settings.llm_repair_attempts + 1):
        text, tokens_in, tokens_out = await model.chat(system, prompt)
        usage.input_tokens += tokens_in
        usage.output_tokens += tokens_out
        try:
            parsed = schema.model_validate(json.loads(_strip_fences(text)))
            usage.repairs = attempt
            usage.latency_ms = int((time.perf_counter() - started) * 1000)
            return parsed, usage
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:800]
            prompt = (
                f"{user}\n\nYour previous reply was invalid:\n{last_error}\n"
                "Reply again with ONLY a JSON object matching the required shape."
            )
    raise LLMError(f"{model.name} returned invalid JSON after repairs: {last_error}")
