"""BYOK (Bring-Your-Own-Key) integration with LiteLLM for dynamic multi-provider inference."""

from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncGenerator, Optional, Type, TypeVar
from fastapi import Header, Request

from rhp_migrate.ai.client import (
    BaseLLMClient,
    LLMRunner,
    MockLLMClient,
    calculate_cost,
    extract_json_payload,
    parse_structured_output,
)
from rhp_migrate.ai.models import TokenUsage
from rhp_migrate.server.models import BYOKCredentials

T = TypeVar("T", bound=Any)


# Default models per provider when none specified
DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "gemini": "gemini-2.5-pro",
    "anthropic": "claude-3-7-sonnet",
    "openai": "gpt-4o",
    "deepseek": "deepseek-chat",
    "ollama": "llama3",
    "mock": "mock-model",
}


def resolve_litellm_model(provider: Optional[str], model_name: Optional[str]) -> str:
    """Resolve a unified model identifier for LiteLLM based on provider and model name.

    Examples:
        - provider='anthropic', model='claude-3-7-sonnet' -> 'anthropic/claude-3-7-sonnet'
        - provider='gemini', model='gemini-2.5-pro' -> 'gemini/gemini-2.5-pro'
        - provider='ollama', model='llama3' -> 'ollama/llama3'
        - provider='deepseek', model='deepseek-chat' -> 'deepseek/deepseek-chat'
        - provider='openai', model='gpt-4o' -> 'gpt-4o'
        - model='custom/model-v1' -> 'custom/model-v1'
    """
    prov = (provider or "").strip().lower()
    model = (model_name or "").strip()

    if not model:
        model = DEFAULT_PROVIDER_MODELS.get(prov, "gpt-4o")

    # If the user already supplied a prefixed model (e.g. 'gemini/gemini-2.5-flash'), honor it
    if "/" in model:
        return model

    if prov == "gemini":
        return f"gemini/{model}"
    elif prov == "anthropic":
        return f"anthropic/{model}"
    elif prov == "deepseek":
        return f"deepseek/{model}"
    elif prov == "ollama":
        return f"ollama/{model}"
    elif prov and prov not in ("openai", "mock"):
        return f"{prov}/{model}"

    return model


class LiteLLMClient(BaseLLMClient):
    """Dynamic BYOK LLM client powered by LiteLLM.

    Executes inference using user-provided credentials without storing keys
    or incurring centralized infrastructure inference costs.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        temperature: float = 0.0,
        mock_responses: Optional[list[Any]] = None,
    ) -> None:
        self._raw_provider = (provider or "").strip().lower()
        self.api_base = api_base
        self._mock_responses = mock_responses or []

        resolved_model = resolve_litellm_model(self._raw_provider, model_name)
        # Fall back to env var if api_key not provided in request
        effective_key = api_key or self._fallback_env_key(self._raw_provider)
        super().__init__(model_name=resolved_model, api_key=effective_key, temperature=temperature)

    @classmethod
    def _fallback_env_key(cls, provider: str) -> Optional[str]:
        """Check environment variables if no user key was passed."""
        if provider == "gemini":
            return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        elif provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY")
        elif provider == "openai":
            return os.environ.get("OPENAI_API_KEY")
        elif provider == "deepseek":
            return os.environ.get("DEEPSEEK_API_KEY")
        return None

    @property
    def provider_name(self) -> str:
        if self._raw_provider:
            return self._raw_provider
        if "/" in self.model_name:
            return self.model_name.split("/")[0]
        return "litellm"

    def _build_kwargs(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Construct LiteLLM completion keyword arguments."""
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        return kwargs

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        """Synchronously execute structured generation using litellm.completion."""
        if self._raw_provider == "mock" or self._mock_responses:
            mock = MockLLMClient(model_name=self.model_name, responses=self._mock_responses)
            return mock.generate_structured(system_prompt, user_prompt, response_model)

        import litellm
        litellm.drop_params = True

        schema_json = json.dumps(response_model.model_json_schema())
        schema_instruction = (
            f"\n\nCRITICAL INSTRUCTION: You MUST return a single valid JSON object strictly matching this schema:\n"
            f"{schema_json}\n"
            f"Do NOT include markdown formatting or commentary outside the JSON object."
        )

        messages = [
            {"role": "system", "content": system_prompt + schema_instruction},
            {"role": "user", "content": user_prompt},
        ]
        kwargs = self._build_kwargs(messages)

        # Attempt structured output response format; fallback if unsupported by provider
        try:
            resp = litellm.completion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = litellm.completion(**kwargs)

        raw_text = resp.choices[0].message.content or ""
        parsed = parse_structured_output(raw_text, response_model)

        usage = self._extract_usage(resp)
        return parsed, usage, raw_text

    async def agenerate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        """Asynchronously execute structured generation using litellm.acompletion."""
        if self._raw_provider == "mock" or self._mock_responses:
            mock = MockLLMClient(model_name=self.model_name, responses=self._mock_responses)
            return mock.generate_structured(system_prompt, user_prompt, response_model)

        import litellm
        litellm.drop_params = True

        schema_json = json.dumps(response_model.model_json_schema())
        schema_instruction = (
            f"\n\nCRITICAL INSTRUCTION: You MUST return a single valid JSON object strictly matching this schema:\n"
            f"{schema_json}\n"
            f"Do NOT include markdown formatting or commentary outside the JSON object."
        )

        messages = [
            {"role": "system", "content": system_prompt + schema_instruction},
            {"role": "user", "content": user_prompt},
        ]
        kwargs = self._build_kwargs(messages)

        try:
            resp = await litellm.acompletion(**kwargs, response_format={"type": "json_object"})
        except Exception:
            resp = await litellm.acompletion(**kwargs)

        raw_text = resp.choices[0].message.content or ""
        parsed = parse_structured_output(raw_text, response_model)

        usage = self._extract_usage(resp)
        return parsed, usage, raw_text

    async def astream_completion(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncGenerator[str, None]:
        """Stream raw token completion from LiteLLM."""
        if self._raw_provider == "mock":
            yield "// Migrated RHP code (mock stream)\n"
            return

        import litellm
        litellm.drop_params = True

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs = self._build_kwargs(messages)
        kwargs["stream"] = True

        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            content = chunk.choices[0].delta.content or ""
            if content:
                yield content

    @staticmethod
    def _extract_usage(resp: Any) -> TokenUsage:
        """Extract prompt and completion token counts from LiteLLM response."""
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        usage = getattr(resp, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(usage, "completion_tokens", 0) or 0
            total_tokens = getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
        )


def extract_byok_from_headers(
    x_provider: Optional[str] = Header(None, alias="x-provider"),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    x_model_name: Optional[str] = Header(None, alias="x-model-name"),
    x_api_base: Optional[str] = Header(None, alias="x-api-base"),
) -> BYOKCredentials:
    """FastAPI dependency to extract BYOK credentials from incoming HTTP headers."""
    return BYOKCredentials(
        provider=x_provider,
        api_key=x_api_key,
        model_name=x_model_name,
        api_base=x_api_base,
    )


def create_runner_from_byok(
    credentials: BYOKCredentials,
    max_retries: int = 2,
    mock_responses: Optional[list[Any]] = None,
) -> LLMRunner:
    """Build an LLMRunner configured with dynamic BYOK LiteLLM credentials."""
    client = LiteLLMClient(
        model_name=credentials.model_name,
        provider=credentials.provider,
        api_key=credentials.api_key,
        api_base=credentials.api_base,
        temperature=credentials.temperature,
        mock_responses=mock_responses,
    )
    return LLMRunner(
        provider=client,
        model=client.model_name,
        max_retries=max_retries,
    )
