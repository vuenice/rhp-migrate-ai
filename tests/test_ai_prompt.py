"""Tests for Layer 5 AI migration models, prompt construction, and extensible LLM runner."""

import json
from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from rhp_migrate.ai.models import (
    AIExecutionStats,
    AIMigrationResponse,
    CostEstimate,
    LLMMigrationResult,
    PatchType,
    TokenUsage,
    TypeResolution,
)
from rhp_migrate.ai.prompts import (
    SYSTEM_PROMPT,
    MigrationPromptContext,
    build_system_prompt,
    build_user_prompt,
    format_ast_context,
    format_caller_signatures,
    format_class_properties,
    format_compiler_errors,
    format_rules_for_prompt,
    validate_single_unit,
)
from rhp_migrate.ai.client import (
    AnthropicClient,
    GeminiClient,
    LLMRunner,
    MockLLMClient,
    OpenAIClient,
    calculate_cost,
    extract_json_payload,
    get_model_pricing,
    parse_structured_output,
)
from rhp_migrate.rules.registry import RuleRegistry
from rhp_migrate.rules.schema import MigrationStrategy, Rule, RuleCategory, RuleExample


# ---------------------------------------------------------------------------
# Task 1: Model Tests
# ---------------------------------------------------------------------------


class TestAIModels:
    """Tests for Pydantic models in rhp_migrate.ai.models."""

    def test_patch_type_enum(self) -> None:
        """Verify PatchType enum members."""
        assert PatchType.FULL_REPLACEMENT.value == "FULL_REPLACEMENT"
        assert PatchType.UNIFIED_DIFF.value == "UNIFIED_DIFF"
        assert PatchType.UNRESOLVED.value == "UNRESOLVED"

    def test_type_resolution_valid(self) -> None:
        """Verify TypeResolution valid instantiation and fields."""
        resolution = TypeResolution(
            param_name="$amount",
            inferred_type="float",
            confidence=0.95,
            rationale="Calculated from numeric arithmetic and currency float operations.",
        )
        assert resolution.param_name == "$amount"
        assert resolution.inferred_type == "float"
        assert resolution.confidence == 0.95
        assert "arithmetic" in resolution.rationale

    def test_type_resolution_confidence_bounds(self) -> None:
        """Verify confidence bounds (0.0 to 1.0)."""
        with pytest.raises(ValidationError):
            TypeResolution(
                param_name="$x",
                inferred_type="int",
                confidence=1.5,  # > 1.0
                rationale="Invalid confidence",
            )
        with pytest.raises(ValidationError):
            TypeResolution(
                param_name="$x",
                inferred_type="int",
                confidence=-0.1,  # < 0.0
                rationale="Invalid confidence",
            )

    def test_llm_migration_result_defaults(self) -> None:
        """Verify LLMMigrationResult with defaults."""
        result = LLMMigrationResult(
            unit_identifier="App\\Services\\PaymentService::charge",
            transformed_code="public function charge(float $amount): bool { return true; }",
        )
        assert result.unit_identifier == "App\\Services\\PaymentService::charge"
        assert result.patch_type == PatchType.FULL_REPLACEMENT
        assert result.resolved_types == []
        assert result.applied_rules == []
        assert result.needs_human_review is False
        assert result.review_reason is None

    def test_llm_migration_result_full(self) -> None:
        """Verify LLMMigrationResult with all fields populated."""
        res = TypeResolution(
            param_name="$amount",
            inferred_type="float",
            confidence=0.9,
            rationale="Passed as float",
        )
        result = LLMMigrationResult(
            unit_identifier="App\\Services\\PaymentService::charge",
            patch_type=PatchType.FULL_REPLACEMENT,
            transformed_code="public function charge(float $amount, string $currency): bool",
            resolved_types=[res],
            applied_rules=["RHP-UNSUP-001"],
            needs_human_review=True,
            review_reason="Dynamic payment gateway resolution requires review.",
        )
        assert result.needs_human_review is True
        assert result.review_reason == "Dynamic payment gateway resolution requires review."
        assert len(result.resolved_types) == 1
        assert result.applied_rules == ["RHP-UNSUP-001"]

        # Serialization round-trip
        json_data = result.model_dump_json()
        restored = LLMMigrationResult.model_validate_json(json_data)
        assert restored == result

    def test_llm_migration_result_extra_fields_forbidden(self) -> None:
        """Ensure extra unknown fields are rejected."""
        with pytest.raises(ValidationError):
            LLMMigrationResult(
                unit_identifier="test",
                transformed_code="code",
                hallucinated_field="forbidden",  # type: ignore[call-arg]
            )

    def test_token_usage_and_addition(self) -> None:
        """Verify TokenUsage addition and aggregation."""
        usage1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        usage2 = TokenUsage(prompt_tokens=50, completion_tokens=25, total_tokens=75)
        combined = usage1.add(usage2)
        assert combined.prompt_tokens == 150
        assert combined.completion_tokens == 75
        assert combined.total_tokens == 225


# ---------------------------------------------------------------------------
# Task 2: Prompt Tests
# ---------------------------------------------------------------------------


class TestAIPrompts:
    """Tests for prompt generation in rhp_migrate.ai.prompts."""

    def test_system_prompt_guidelines(self) -> None:
        """Verify system prompt includes all mandatory RHP guidelines."""
        sys_prompt = build_system_prompt()
        assert "Strict Static Types Mandatory" in sys_prompt
        assert "No Reflection" in sys_prompt
        assert "Array Disambiguation" in sys_prompt
        assert "Never Invent Non-Existent APIs" in sys_prompt
        assert "Single-Unit Processing Scope" in sys_prompt
        assert "Strict Structured JSON Output" in sys_prompt
        assert "LLMMigrationResult" in sys_prompt

    def test_validate_single_unit_accepts_single_function(self) -> None:
        """Validate single function code is accepted."""
        code = """
        function calculateTotal(float $subtotal, float $tax): float {
            return $subtotal + $tax;
        }
        """
        validate_single_unit(code, "calculateTotal")

    def test_validate_single_unit_accepts_single_class(self) -> None:
        """Validate single class code is accepted."""
        code = """
        class PaymentService {
            public function charge(float $amount): bool {
                return true;
            }
        }
        """
        validate_single_unit(code, "App\\Services\\PaymentService")

    def test_validate_single_unit_rejects_empty(self) -> None:
        """Validate empty code raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_single_unit("", "test")

    def test_validate_single_unit_rejects_whole_file_with_multiple_classes(self) -> None:
        """Enforce constraint: NEVER send whole files with multiple classes."""
        multi_class_file = """
        class UserService {
            public function getUser(int $id) {}
        }
        class OrderService {
            public function getOrder(int $id) {}
        }
        """
        with pytest.raises(ValueError, match="Whole files with multiple classes cannot be sent"):
            validate_single_unit(multi_class_file, "App\\Services")

    def test_format_rules_from_yaml_ids(self) -> None:
        """Verify format_rules_for_prompt loads rules and examples from rules.yaml."""
        output = format_rules_for_prompt(["RHP-UNSUP-001"])
        assert "RHP-UNSUP-001" in output
        assert "Reflection API unsupported" in output
        assert "BEFORE:" in output
        assert "AFTER:" in output

    def test_format_rules_from_rule_objects(self) -> None:
        """Verify format_rules_for_prompt handles explicit Rule instances."""
        custom_rule = Rule(
            id="RHP-CUST-001",
            title="Custom Migration Rule",
            category=RuleCategory.SYNTAX,
            strategy=MigrationStrategy.LLM_ASSIST,
            php_pattern="old_api()",
            rhp_replacement="new_api()",
            examples=[
                RuleExample(
                    before="old_api();",
                    after="new_api();",
                    explanation="Use modern replacement.",
                )
            ],
        )
        output = format_rules_for_prompt([custom_rule])
        assert "RHP-CUST-001" in output
        assert "Custom Migration Rule" in output
        assert "old_api()" in output
        assert "new_api()" in output

    def test_format_caller_signatures(self) -> None:
        """Verify caller signatures and call-site formatting."""
        signatures = [
            {
                "caller": "App\\Http\\Controllers\\CheckoutController::submit",
                "args": "100.0, 'USD'",
                "line": 42,
            },
            "App\\Jobs\\ProcessSubscription::handle calls charge($sub->price, 'EUR')",
        ]
        formatted = format_caller_signatures(signatures)
        assert "CheckoutController::submit" in formatted
        assert "100.0, 'USD'" in formatted
        assert "ProcessSubscription::handle" in formatted

    def test_format_class_properties(self) -> None:
        """Verify class property context formatting."""
        props = [
            {"name": "$logger", "type": "LoggerInterface", "visibility": "private"},
            "protected float $taxRate",
        ]
        formatted = format_class_properties(props)
        assert "private LoggerInterface $logger" in formatted
        assert "protected float $taxRate" in formatted

    def test_format_compiler_errors(self) -> None:
        """Verify compiler errors formatting for retry turns."""
        errors = [
            "line 14: TypeError: Parameter 1 must be float, mixed given",
            "line 25: UnsupportedFeature: ReflectionMethod is not allowed in RHP",
        ]
        formatted = format_compiler_errors(errors)
        assert "Parameter 1 must be float" in formatted
        assert "ReflectionMethod is not allowed" in formatted

    def test_build_user_prompt_complete(self) -> None:
        """Verify user prompt properly injects all required context."""
        code = "public function charge($amount) { return $this->gateway->charge($amount); }"
        user_prompt = build_user_prompt(
            unit_identifier="App\\Services\\PaymentService::charge",
            code=code,
            ast_context={"parameters": [{"name": "$amount", "type_hint": None}]},
            caller_signatures=["CheckoutController line 42: charge(50.0)"],
            class_properties=["private PaymentGateway $gateway"],
            rules=["RHP-UNSUP-001"],
            compiler_errors=["Parameter $amount is untyped."],
            patch_type=PatchType.FULL_REPLACEMENT,
            extra_instructions="Preserve gateway timeout semantics.",
        )

        assert "App\\Services\\PaymentService::charge" in user_prompt
        assert "FULL_REPLACEMENT" in user_prompt
        assert code in user_prompt
        assert "CheckoutController line 42" in user_prompt
        assert "private PaymentGateway $gateway" in user_prompt
        assert "RHP-UNSUP-001" in user_prompt
        assert "Parameter $amount is untyped." in user_prompt
        assert "Preserve gateway timeout semantics." in user_prompt
        assert "LLMMigrationResult" in user_prompt

    def test_migration_prompt_context_model(self) -> None:
        """Verify MigrationPromptContext encapsulation and rendering."""
        ctx = MigrationPromptContext(
            unit_identifier="App\\Utils\\Format::money",
            code="function money($val) { return '$' . $val; }",
            caller_signatures=["Format::money(12.5)"],
            compiler_errors=["Missing parameter type"],
        )
        sys = ctx.render_system_prompt()
        usr = ctx.render_user_prompt()

        assert "Strict Static Types Mandatory" in sys
        assert "App\\Utils\\Format::money" in usr
        assert "Format::money(12.5)" in usr
        assert "Missing parameter type" in usr


# ---------------------------------------------------------------------------
# Task 3: Client, Runner, and Cost Calculation Tests
# ---------------------------------------------------------------------------


class TestCostCalculationAndPricing:
    """Tests for token tracking and cost calculation in client.py."""

    def test_gemini_pricing_calculation(self) -> None:
        """Verify pricing for gemini-2.5-pro and gemini-2.5-flash."""
        # gemini-2.5-pro: $1.25 / 1M input, $5.00 / 1M output
        cost_pro = calculate_cost("gemini-2.5-pro", prompt_tokens=1_000_000, completion_tokens=1_000_000)
        assert cost_pro.prompt_cost_usd == 1.25
        assert cost_pro.completion_cost_usd == 5.00
        assert cost_pro.total_cost_usd == 6.25

        # gemini-2.5-flash: $0.15 / 1M input, $0.60 / 1M output
        cost_flash = calculate_cost("gemini-2.5-flash", prompt_tokens=100_000, completion_tokens=50_000)
        assert cost_flash.prompt_cost_usd == pytest.approx(0.015, abs=1e-5)
        assert cost_flash.completion_cost_usd == pytest.approx(0.030, abs=1e-5)
        assert cost_flash.total_cost_usd == pytest.approx(0.045, abs=1e-5)

    def test_claude_and_openai_pricing(self) -> None:
        """Verify pricing for Claude 3.5 Sonnet and GPT-4o."""
        # claude-3-5-sonnet: $3.00 / 1M input, $15.00 / 1M output
        cost_claude = calculate_cost("claude-3-5-sonnet", prompt_tokens=500_000, completion_tokens=100_000)
        assert cost_claude.prompt_cost_usd == pytest.approx(1.50, abs=1e-4)
        assert cost_claude.completion_cost_usd == pytest.approx(1.50, abs=1e-4)
        assert cost_claude.total_cost_usd == pytest.approx(3.00, abs=1e-4)

        # gpt-4o: $2.50 / 1M input, $10.00 / 1M output
        cost_gpt = calculate_cost("gpt-4o", prompt_tokens=200_000, completion_tokens=100_000)
        assert cost_gpt.prompt_cost_usd == pytest.approx(0.50, abs=1e-4)
        assert cost_gpt.completion_cost_usd == pytest.approx(1.00, abs=1e-4)
        assert cost_gpt.total_cost_usd == pytest.approx(1.50, abs=1e-4)

    def test_prefix_model_pricing_match(self) -> None:
        """Verify model pricing matches versioned/dated model tags via prefix."""
        rates = get_model_pricing("gemini-2.5-flash-preview-0514")
        assert rates["input"] == 0.15
        assert rates["output"] == 0.60


class TestJsonParsingAndExtraction:
    """Tests for structured output JSON parsing and extraction."""

    def test_extract_clean_json(self) -> None:
        raw = '{"unit_identifier": "test", "transformed_code": "code"}'
        assert extract_json_payload(raw) == raw

    def test_extract_markdown_fenced_json(self) -> None:
        raw = """Here is the result:
```json
{
  "unit_identifier": "test",
  "transformed_code": "code"
}
```
Done."""
        payload = extract_json_payload(raw)
        data = json.loads(payload)
        assert data["unit_identifier"] == "test"

    def test_extract_json_with_surrounding_text(self) -> None:
        raw = 'Sure thing! {"unit_identifier": "test", "transformed_code": "code"} Hope this helps!'
        payload = extract_json_payload(raw)
        data = json.loads(payload)
        assert data["unit_identifier"] == "test"

    def test_parse_structured_output_success(self) -> None:
        raw = json.dumps({
            "unit_identifier": "App\\Test::run",
            "transformed_code": "function run(): void {}",
            "resolved_types": [],
            "applied_rules": [],
            "needs_human_review": False,
        })
        parsed = parse_structured_output(raw, LLMMigrationResult)
        assert parsed.unit_identifier == "App\\Test::run"

    def test_parse_structured_output_invalid(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse structured output"):
            parse_structured_output("Not valid JSON at all", LLMMigrationResult)


class TestLLMRunnerAndMockClient:
    """Tests for LLMRunner using MockLLMClient and mocked provider responses."""

    def test_runner_with_mock_client_success(self) -> None:
        """Test LLMRunner executing with MockLLMClient returning structured output."""
        mock_client = MockLLMClient(
            model_name="gemini-2.5-flash",
            simulated_prompt_tokens=400,
            simulated_completion_tokens=200,
        )
        expected_result = LLMMigrationResult(
            unit_identifier="App\\Services\\PaymentService::charge",
            patch_type=PatchType.FULL_REPLACEMENT,
            transformed_code="public function charge(float $amount): bool { return true; }",
            resolved_types=[
                TypeResolution(
                    param_name="$amount",
                    inferred_type="float",
                    confidence=1.0,
                    rationale="Inferred from caller checkout invocation.",
                )
            ],
            applied_rules=["RHP-TYP-001"],
            needs_human_review=False,
        )
        mock_client.queue_response(expected_result)

        runner = LLMRunner(provider=mock_client)
        response = runner.migrate_unit(
            unit_identifier="App\\Services\\PaymentService::charge",
            code="public function charge($amount) { return true; }",
            caller_signatures=["checkout calls charge(19.99)"],
        )

        assert response.result == expected_result
        assert response.stats.token_usage.prompt_tokens == 400
        assert response.stats.token_usage.completion_tokens == 200
        assert response.stats.token_usage.total_tokens == 600
        assert response.stats.cost.total_cost_usd > 0.0

        # Cumulative tracking
        assert runner.call_count == 1
        assert runner.total_usage.total_tokens == 600
        assert runner.total_cost_usd == response.stats.cost.total_cost_usd

        summary = runner.get_usage_summary()
        assert summary["call_count"] == 1
        assert summary["total_tokens"] == 600

        runner.reset_stats()
        assert runner.call_count == 0
        assert runner.total_usage.total_tokens == 0
        assert runner.total_cost_usd == 0.0

    def test_runner_auto_detects_provider(self) -> None:
        """Verify LLMRunner auto-detects provider based on model name."""
        runner_gemini = LLMRunner(model="gemini-2.5-pro", api_key="dummy")
        assert isinstance(runner_gemini.client, GeminiClient)

        runner_claude = LLMRunner(model="claude-3-5-sonnet", api_key="dummy")
        assert isinstance(runner_claude.client, AnthropicClient)

        runner_openai = LLMRunner(model="gpt-4o", api_key="dummy")
        assert isinstance(runner_openai.client, OpenAIClient)

    def test_runner_retry_on_initial_failure(self) -> None:
        """Verify runner retries when first attempt returns invalid JSON and succeeds on second attempt."""
        mock_client = MockLLMClient(model_name="gemini-2.5-flash")
        # First attempt: invalid text
        mock_client.queue_response("Invalid text that is not JSON")
        # Second attempt: valid result
        valid_result = LLMMigrationResult(
            unit_identifier="App\\Test::method",
            transformed_code="function method(): void {}",
        )
        mock_client.queue_response(valid_result)

        runner = LLMRunner(provider=mock_client, max_retries=2)
        response = runner.migrate_unit(
            unit_identifier="App\\Test::method",
            code="function method() {}",
        )
        assert response.result.unit_identifier == "App\\Test::method"
        assert len(mock_client.call_history) == 2
        # Check that retry turn included previous error feedback
        retry_prompt = mock_client.call_history[1]["user_prompt"]
        assert "PREVIOUS ATTEMPT FAILED SCHEMA VALIDATION" in retry_prompt

    def test_runner_raises_when_max_retries_exceeded(self) -> None:
        """Verify runner raises RuntimeError when all retries fail."""
        mock_client = MockLLMClient(model_name="gemini-2.5-flash")
        mock_client.queue_response("Broken 1")
        mock_client.queue_response("Broken 2")

        runner = LLMRunner(provider=mock_client, max_retries=1)
        with pytest.raises(RuntimeError, match="Failed to migrate unit .* after 2 attempts"):
            runner.migrate_unit(
                unit_identifier="App\\Broken::run",
                code="function run() {}",
            )


# ---------------------------------------------------------------------------
# Task 4: Mocked Provider Integration Tests
# ---------------------------------------------------------------------------


class TestMockedProviders:
    """Mock test cases verifying Gemini, Anthropic, and OpenAI client behaviors."""

    def test_gemini_client_structured_generation_mocked(self) -> None:
        """Mock google-genai Client and test structured response parsing and token usage."""
        client = GeminiClient(model_name="gemini-2.5-pro", api_key="test-key")

        mock_genai_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "unit_identifier": "App\\Services\\Billing::invoice",
            "patch_type": "FULL_REPLACEMENT",
            "transformed_code": "public function invoice(int $id): bool { return true; }",
            "resolved_types": [
                {
                    "param_name": "$id",
                    "inferred_type": "int",
                    "confidence": 1.0,
                    "rationale": "Database ID integer",
                }
            ],
            "applied_rules": ["RHP-TYP-001"],
            "needs_human_review": False,
            "review_reason": None,
        })
        mock_response.usage_metadata.prompt_token_count = 350
        mock_response.usage_metadata.candidates_token_count = 120
        mock_response.usage_metadata.total_token_count = 470
        mock_genai_client.models.generate_content.return_value = mock_response

        mock_types = MagicMock()
        with patch.object(client, "_get_client", return_value=mock_genai_client), patch.object(
            client, "_get_types", return_value=mock_types
        ):
            res, usage, raw = client.generate_structured(
                system_prompt="sys",
                user_prompt="user",
                response_model=LLMMigrationResult,
            )

        assert res.unit_identifier == "App\\Services\\Billing::invoice"
        assert res.transformed_code == "public function invoice(int $id): bool { return true; }"
        assert usage.prompt_tokens == 350
        assert usage.completion_tokens == 120
        assert usage.total_tokens == 470

    def test_anthropic_client_tool_use_mocked(self) -> None:
        """Mock Anthropic client and test tool_use structured extraction."""
        client = AnthropicClient(model_name="claude-3-5-sonnet", api_key="test-key")

        mock_anthropic = MagicMock()
        mock_response = MagicMock()

        # Mock tool_use block
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.name = "record_migration_result"
        tool_block.input = {
            "unit_identifier": "App\\Models\\User::isAdmin",
            "patch_type": "FULL_REPLACEMENT",
            "transformed_code": "public function isAdmin(): bool { return $this->role == 'admin'; }",
            "resolved_types": [],
            "applied_rules": ["RHP-SYN-003"],
            "needs_human_review": False,
            "review_reason": None,
        }

        mock_response.content = [tool_block]
        mock_response.usage.input_tokens = 280
        mock_response.usage.output_tokens = 95
        mock_anthropic.messages.create.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_anthropic):
            res, usage, raw = client.generate_structured(
                system_prompt="sys",
                user_prompt="user",
                response_model=LLMMigrationResult,
            )

        assert res.unit_identifier == "App\\Models\\User::isAdmin"
        assert res.applied_rules == ["RHP-SYN-003"]
        assert usage.prompt_tokens == 280
        assert usage.completion_tokens == 95
        assert usage.total_tokens == 375

    def test_openai_client_beta_parse_mocked(self) -> None:
        """Mock OpenAI client beta parse structured response."""
        client = OpenAIClient(model_name="gpt-4o", api_key="test-key")

        mock_openai = MagicMock()
        mock_response = MagicMock()

        expected_model = LLMMigrationResult(
            unit_identifier="App\\Repositories\\OrderRepo::find",
            patch_type=PatchType.FULL_REPLACEMENT,
            transformed_code="public function find(string $uuid): ?Order",
            resolved_types=[],
            applied_rules=[],
            needs_human_review=False,
        )

        mock_choice = MagicMock()
        mock_choice.message.parsed = expected_model
        mock_choice.message.content = expected_model.model_dump_json()
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 310
        mock_response.usage.completion_tokens = 85
        mock_openai.beta.chat.completions.parse.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_openai):
            res, usage, raw = client.generate_structured(
                system_prompt="sys",
                user_prompt="user",
                response_model=LLMMigrationResult,
            )

        assert res.unit_identifier == "App\\Repositories\\OrderRepo::find"
        assert usage.prompt_tokens == 310
        assert usage.completion_tokens == 85
        assert usage.total_tokens == 395
