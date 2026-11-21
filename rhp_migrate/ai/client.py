"""Extensible LLM client runner with token tracking and cost calculation (Layer 5)."""

from abc import ABC, abstractmethod
import json
import os
import re
import time
from typing import Any, Callable, Optional, Sequence, Type, TypeVar, Union
from pydantic import BaseModel, ValidationError

from rhp_migrate.ai.models import (
    AIExecutionStats,
    AIMigrationResponse,
    CostEstimate,
    LLMMigrationResult,
    TokenUsage,
)
from rhp_migrate.ai.prompts import (
    MigrationPromptContext,
    build_system_prompt,
    build_user_prompt,
)

T = TypeVar("T", bound=BaseModel)

# Model pricing in USD per 1,000,000 tokens (input_cost_per_m, output_cost_per_m)
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Google Gemini
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    # Anthropic Claude
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
}


def get_model_pricing(model: str) -> dict[str, float]:
    """Find pricing rates for a model by exact name or prefix match."""
    normalized = model.lower().strip()
    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]

    for key, rates in MODEL_PRICING.items():
        if normalized.startswith(key):
            return rates

    # Default fallback pricing (conservative estimate equivalent to flash/mini)
    return {"input": 0.15, "output": 0.60}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> CostEstimate:
    """Calculate USD cost for token consumption given model and token counts.

    Args:
        model: Model identifier string.
        prompt_tokens: Number of prompt/input tokens.
        completion_tokens: Number of completion/output tokens.

    Returns:
        CostEstimate with prompt, completion, and total cost in USD.
    """
    rates = get_model_pricing(model)
    prompt_cost = (prompt_tokens / 1_000_000.0) * rates["input"]
    completion_cost = (completion_tokens / 1_000_000.0) * rates["output"]
    total_cost = prompt_cost + completion_cost
    return CostEstimate(
        prompt_cost_usd=round(prompt_cost, 6),
        completion_cost_usd=round(completion_cost, 6),
        total_cost_usd=round(total_cost, 6),
    )


def extract_json_payload(text: str) -> str:
    """Extract valid JSON from raw text, removing markdown code fences or conversational text."""
    stripped = text.strip()

    # Match ```json ... ``` code fences
    fence_pattern = r"```(?:json)?\s*(\{[\s\S]*?\})\s*```"
    match = re.search(fence_pattern, stripped)
    if match:
        return match.group(1).strip()

    # Match the outermost {...} JSON object
    first_brace = stripped.find("{")
    last_brace = stripped.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return stripped[first_brace : last_brace + 1].strip()

    return stripped


def parse_structured_output(raw_text: str, model_cls: Type[T]) -> T:
    """Parse raw text into a validated Pydantic model instance.

    Args:
        raw_text: String returned by the LLM.
        model_cls: Target Pydantic model class.

    Returns:
        Validated model instance.

    Raises:
        ValueError: If JSON is invalid or fails Pydantic schema validation.
    """
    payload = extract_json_payload(raw_text)
    try:
        data = json.loads(payload)
        return model_cls.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as err:
        raise ValueError(f"Failed to parse structured output into {model_cls.__name__}: {err}\nPayload: {payload}") from err


class BaseLLMClient(ABC):
    """Abstract interface for LLM provider runners."""

    def __init__(self, model_name: str, api_key: Optional[str] = None, temperature: float = 0.0) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.temperature = temperature

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'gemini', 'anthropic', 'openai', 'mock')."""
        ...

    @abstractmethod
    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        """Execute structured generation.

        Returns:
            Tuple of (parsed_model, token_usage, raw_response_text).
        """
        ...


class GeminiClient(BaseLLMClient):
    """LLM client for Google Gemini models (e.g. gemini-2.5-pro, gemini-2.5-flash)."""

    def __init__(
        self,
        model_name: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key or os.environ.get("GEMINI_API_KEY"), temperature=temperature)
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "gemini"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except ImportError as err:
                raise ImportError(
                    "Google Gemini SDK is not installed. Install it via 'pip install google-genai'."
                ) from err
        return self._client

    def _get_types(self) -> Any:
        try:
            from google.genai import types
            return types
        except ImportError as err:
            raise ImportError(
                "Google Gemini SDK is not installed. Install it via 'pip install google-genai'."
            ) from err

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        client = self._get_client()
        types = self._get_types()

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=response_model,
            temperature=self.temperature,
        )

        response = client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=config,
        )

        raw_text = response.text or ""
        parsed = response_model.model_validate_json(raw_text)

        # Extract token usage metadata if available
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata is not None:
            meta = response.usage_metadata
            prompt_tokens = getattr(meta, "prompt_token_count", 0) or 0
            completion_tokens = getattr(meta, "candidates_token_count", 0) or 0
            total_tokens = getattr(meta, "total_token_count", prompt_tokens + completion_tokens) or 0

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
        )

        return parsed, usage, raw_text


class AnthropicClient(BaseLLMClient):
    """LLM client for Anthropic Claude models (e.g. claude-3-5-sonnet, claude-3-7-sonnet)."""

    def __init__(
        self,
        model_name: str = "claude-3-5-sonnet",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"), temperature=temperature)
        self.max_tokens = max_tokens
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError as err:
                raise ImportError(
                    "Anthropic SDK is not installed. Install it via 'pip install anthropic'."
                ) from err
        return self._client

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        client = self._get_client()

        # Enforce structured output via tool calling
        tool_name = "record_migration_result"
        tools = [
            {
                "name": tool_name,
                "description": "Record the structured migration result for the target code unit.",
                "input_schema": response_model.model_json_schema(),
            }
        ]

        response = client.messages.create(
            model=self.model_name,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=tools,
            tool_choice={"type": "tool", "name": tool_name},
        )

        # Extract tool call input
        raw_text = ""
        parsed: Optional[T] = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                raw_text = json.dumps(block.input)
                parsed = response_model.model_validate(block.input)
                break

        if parsed is None:
            # Fallback to text parsing if no tool_use block
            text_blocks = [getattr(b, "text", "") for b in response.content if hasattr(b, "text")]
            raw_text = "\n".join(text_blocks)
            parsed = parse_structured_output(raw_text, response_model)

        prompt_tokens = getattr(response.usage, "input_tokens", 0)
        completion_tokens = getattr(response.usage, "output_tokens", 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return parsed, usage, raw_text


class OpenAIClient(BaseLLMClient):
    """LLM client for OpenAI models (e.g. gpt-4o, gpt-4o-mini)."""

    def __init__(
        self,
        model_name: str = "gpt-4o",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key or os.environ.get("OPENAI_API_KEY"), temperature=temperature)
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "openai"

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(api_key=self.api_key)
            except ImportError as err:
                raise ImportError(
                    "OpenAI SDK is not installed. Install it via 'pip install openai'."
                ) from err
        return self._client

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        client = self._get_client()

        # Use OpenAI structured outputs via parse
        response = client.beta.chat.completions.parse(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_model,
            temperature=self.temperature,
        )

        parsed = response.choices[0].message.parsed
        raw_text = response.choices[0].message.content or ""

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens or 0
            completion_tokens = response.usage.completion_tokens or 0

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return parsed, usage, raw_text


class MockLLMClient(BaseLLMClient):
    """Mock client for offline testing, dry-runs, and deterministic verification."""

    def __init__(
        self,
        model_name: str = "mock-model",
        responses: Optional[Union[list[Any], Any]] = None,
        simulated_prompt_tokens: int = 150,
        simulated_completion_tokens: int = 80,
    ) -> None:
        super().__init__(model_name=model_name)
        self.simulated_prompt_tokens = simulated_prompt_tokens
        self.simulated_completion_tokens = simulated_completion_tokens
        self._responses: list[Any] = []
        if responses is not None:
            if isinstance(responses, list):
                self._responses = list(responses)
            else:
                self._responses = [responses]
        self.call_history: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    def queue_response(self, response: Any) -> None:
        """Enqueue a mock response."""
        self._responses.append(response)

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
    ) -> tuple[T, TokenUsage, str]:
        self.call_history.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_model": response_model,
        })

        usage = TokenUsage(
            prompt_tokens=self.simulated_prompt_tokens,
            completion_tokens=self.simulated_completion_tokens,
            total_tokens=self.simulated_prompt_tokens + self.simulated_completion_tokens,
        )

        if self._responses:
            item = self._responses.pop(0)
            if callable(item):
                item = item(system_prompt, user_prompt, response_model)

            if isinstance(item, response_model):
                raw_text = item.model_dump_json()
                return item, usage, raw_text
            elif isinstance(item, dict):
                parsed = response_model.model_validate(item)
                return parsed, usage, json.dumps(item)
            elif isinstance(item, str):
                parsed = parse_structured_output(item, response_model)
                return parsed, usage, item
            elif isinstance(item, Exception):
                raise item

        # Default fallback valid response for LLMMigrationResult if no item queued
        if issubclass(response_model, LLMMigrationResult):
            default_res = LLMMigrationResult(
                unit_identifier="MockUnit",
                transformed_code="// Mock transformed code",
                resolved_types=[],
                applied_rules=[],
                needs_human_review=False,
            )
            return default_res, usage, default_res.model_dump_json()  # type: ignore[return-value]

        from rhp_migrate.ai.evaluation_models import MigrationCriticReport
        if issubclass(response_model, MigrationCriticReport):
            default_critic = MigrationCriticReport(
                file_path="mock.php",
                php_version="8.2",
            )
            return default_critic, usage, default_critic.model_dump_json()  # type: ignore[return-value]

        raise ValueError(f"No mock response available for {response_model.__name__}")


class LLMRunner:
    """High-level extensible LLM migration runner with token and cost tracking."""

    def __init__(
        self,
        provider: Optional[Union[str, BaseLLMClient]] = None,
        model: str = "gemini-2.5-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 2,
    ) -> None:
        self.model = model
        self.max_retries = max_retries

        if isinstance(provider, BaseLLMClient):
            self.client = provider
            self.model = provider.model_name
        else:
            self.client = self._create_provider(
                provider_name=provider,
                model_name=model,
                api_key=api_key,
                temperature=temperature,
            )

        # Cumulative telemetry tracking
        self.total_usage = TokenUsage()
        self.total_cost_usd = 0.0
        self.call_count = 0

    @classmethod
    def _create_provider(
        cls,
        provider_name: Optional[str],
        model_name: str,
        api_key: Optional[str],
        temperature: float,
    ) -> BaseLLMClient:
        resolved = provider_name.lower().strip() if provider_name else None

        if not resolved:
            # Auto-detect provider from model name
            m_lower = model_name.lower()
            if "gemini" in m_lower:
                resolved = "gemini"
            elif "claude" in m_lower:
                resolved = "anthropic"
            elif "gpt" in m_lower or "o3" in m_lower:
                resolved = "openai"
            elif "mock" in m_lower:
                resolved = "mock"
            else:
                resolved = "gemini"

        if resolved == "gemini":
            return GeminiClient(model_name=model_name, api_key=api_key, temperature=temperature)
        elif resolved == "anthropic":
            return AnthropicClient(model_name=model_name, api_key=api_key, temperature=temperature)
        elif resolved == "openai":
            return OpenAIClient(model_name=model_name, api_key=api_key, temperature=temperature)
        elif resolved == "mock":
            return MockLLMClient(model_name=model_name)
        elif resolved in ("litellm", "ollama", "deepseek"):
            from rhp_migrate.server.byok import LiteLLMClient
            return LiteLLMClient(
                model_name=model_name,
                provider=resolved,
                api_key=api_key,
                temperature=temperature,
            )
        else:
            raise ValueError(f"Unknown LLM provider: '{provider_name}'. Supported: gemini, anthropic, openai, litellm, ollama, deepseek, mock.")

    def reset_stats(self) -> None:
        """Reset cumulative token and cost counters."""
        self.total_usage = TokenUsage()
        self.total_cost_usd = 0.0
        self.call_count = 0

    def get_usage_summary(self) -> dict[str, Any]:
        """Return a dictionary summarizing cumulative token usage and costs."""
        return {
            "model": self.model,
            "provider": self.client.provider_name,
            "call_count": self.call_count,
            "prompt_tokens": self.total_usage.prompt_tokens,
            "completion_tokens": self.total_usage.completion_tokens,
            "total_tokens": self.total_usage.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
        }

    def migrate_context(self, context: MigrationPromptContext) -> AIMigrationResponse:
        """Migrate a target unit encapsulated in a MigrationPromptContext."""
        system_prompt = context.render_system_prompt()
        user_prompt = context.render_user_prompt()
        return self._execute_migration(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            unit_identifier=context.unit_identifier,
        )

    def migrate_unit(
        self,
        unit_identifier: str,
        code: str,
        *,
        ast_context: Optional[Any] = None,
        caller_signatures: Optional[Union[Sequence[Union[str, dict[str, Any]]], str]] = None,
        class_properties: Optional[Union[Sequence[Any], str]] = None,
        rules: Optional[Any] = None,
        compiler_errors: Optional[Union[Sequence[str], str]] = None,
        extra_instructions: Optional[str] = None,
    ) -> AIMigrationResponse:
        """Migrate a single PHP function, method, or class into strictly-typed RHP code.

        Args:
            unit_identifier: Fully-qualified name (e.g. 'App\\Services\\PaymentService::charge').
            code: PHP source code of the single target unit.
            ast_context: AST symbol context.
            caller_signatures: External call sites and argument types.
            class_properties: Enclosing class properties and dependencies.
            rules: Migration rules from rules.yaml or Rule objects.
            compiler_errors: Prior compilation errors for retry turns.
            extra_instructions: Optional custom instructions.

        Returns:
            AIMigrationResponse containing result, usage, and cost estimates.
        """
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(
            unit_identifier=unit_identifier,
            code=code,
            ast_context=ast_context,
            caller_signatures=caller_signatures,
            class_properties=class_properties,
            rules=rules,
            compiler_errors=compiler_errors,
            extra_instructions=extra_instructions,
        )
        return self._execute_migration(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            unit_identifier=unit_identifier,
        )

    def _execute_migration(
        self,
        system_prompt: str,
        user_prompt: str,
        unit_identifier: str,
    ) -> AIMigrationResponse:
        start_time = time.perf_counter()
        last_error: Optional[Exception] = None

        current_user_prompt = user_prompt
        for attempt in range(self.max_retries + 1):
            try:
                result, usage, raw_text = self.client.generate_structured(
                    system_prompt=system_prompt,
                    user_prompt=current_user_prompt,
                    response_model=LLMMigrationResult,
                )
                duration = time.perf_counter() - start_time

                # Track cumulative tokens & calculate costs
                cost = calculate_cost(
                    model=self.model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                )
                self.total_usage = self.total_usage.add(usage)
                self.total_cost_usd += cost.total_cost_usd
                self.call_count += 1

                stats = AIExecutionStats(
                    model=self.model,
                    token_usage=usage,
                    cost=cost,
                    duration_seconds=round(duration, 4),
                )

                return AIMigrationResponse(
                    result=result,
                    stats=stats,
                    raw_response=raw_text,
                )
            except Exception as err:
                last_error = err
                if attempt < self.max_retries:
                    # Append error feedback for retry turn
                    current_user_prompt = (
                        f"{user_prompt}\n\n"
                        f"## PREVIOUS ATTEMPT FAILED SCHEMA VALIDATION\n"
                        f"Error: {str(err)}\n"
                        f"Ensure you return valid JSON conforming to LLMMigrationResult."
                    )

        duration = time.perf_counter() - start_time
        raise RuntimeError(
            f"Failed to migrate unit '{unit_identifier}' after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error
