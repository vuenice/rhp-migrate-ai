"""Unit and integration tests for version detection, critic evaluation, and feedback dispatch."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from rhp_migrate.ai.client import MockLLMClient, LLMRunner
from rhp_migrate.ai.critic import MigrationCritic
from rhp_migrate.ai.evaluation_models import (
    CodeSample,
    ContextMdProposal,
    LintMistake,
    LintOpportunity,
    LLMDifficulty,
    MigrateMistake,
    MigrateOpportunity,
    MigrationCriticReport,
    RHPLanguageGap,
)
from rhp_migrate.core.feedback import FeedbackDispatcher
from rhp_migrate.core.version_detector import (
    PHPVersionInfo,
    detect_php_version,
    parse_version_numbers,
)
from rhp_migrate.engine.rhp_cli import (
    CheckReport,
    CompilerDiagnostic,
    LintDiagnostic,
    LintReport,
    RhpCli,
    run_lint,
)


# ---------------------------------------------------------------------------
# Version Detector Tests
# ---------------------------------------------------------------------------

def test_parse_version_numbers():
    assert parse_version_numbers("^8.2") == (8, 2)
    assert parse_version_numbers(">=7.4.33") == (7, 4)
    assert parse_version_numbers("8.1.*") == (8, 1)
    assert parse_version_numbers("8") == (8, 0)
    assert parse_version_numbers("invalid") is None


def test_detect_php_version_from_composer(tmp_path: Path):
    composer = tmp_path / "composer.json"
    composer.write_text(json.dumps({"require": {"php": "^8.3"}}), encoding="utf-8")

    info = detect_php_version(tmp_path)
    assert info.version == "8.3"
    assert info.major == 8
    assert info.minor == 3
    assert info.source == "composer.json"
    assert info.is_php8_plus is True


def test_detect_php_version_from_dockerfile(tmp_path: Path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM php:8.1-fpm-alpine\nRUN docker-php-ext-install pdo", encoding="utf-8")

    info = detect_php_version(tmp_path)
    assert info.version == "8.1"
    assert info.major == 8
    assert info.minor == 1
    assert "Dockerfile" in info.source


def test_detect_php_version_fallback(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with patch("shutil.which", return_value=None):
        info = detect_php_version(empty_dir)
        assert info.version == "8.2"
        assert info.source == "fallback"


# ---------------------------------------------------------------------------
# Lint CLI Tests
# ---------------------------------------------------------------------------

@patch("subprocess.run")
def test_run_lint_success(mock_subproc, tmp_path: Path):
    sample_file = tmp_path / "sample.php"
    sample_file.write_text("<?php $x = 10;", encoding="utf-8")

    mock_json = [
        {
            "file": str(sample_file),
            "line": 1,
            "column": 7,
            "rule_id": "RHP-LINT-001",
            "message": "Unused variable $x",
            "severity": "warning",
            "auto_fixable": True,
        }
    ]
    mock_subproc.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps(mock_json),
        stderr="",
    )

    with patch("rhp_migrate.engine.rhp_cli.get_rhp_binary", return_value=Path("/usr/bin/rhp")):
        report = run_lint(sample_file)
        assert isinstance(report, LintReport)
        assert report.warnings == 1
        assert report.errors == 0
        assert report.has_warnings is True
        assert report.has_errors is False
        assert len(report.diagnostics) == 1
        assert report.diagnostics[0].rule_id == "RHP-LINT-001"


# ---------------------------------------------------------------------------
# Critic & Evaluation Models Tests
# ---------------------------------------------------------------------------

def test_critic_analyze_flow():
    mock_client = MockLLMClient()
    mock_runner = LLMRunner(provider=mock_client)

    critic = MigrationCritic(runner=mock_runner)

    custom_report = MigrationCriticReport(
        file_path="tests/fixture.php",
        php_version="8.2",
        migrate_mistakes=[
            MigrateMistake(
                title="Incorrect cookie cast",
                sample=CodeSample(
                    title="setcookie conversion",
                    before="setcookie('a', 'b');",
                    after="Cookie::raw_set('a', 'b');",
                    expected="Cookie::queue('a', 'b');",
                    explanation="Cookie facade uses queue() in RHP rather than raw_set",
                ),
            )
        ],
        migrate_missed_opportunities=[
            MigrateOpportunity(
                pattern_name="array_key_exists",
                suggested_rule_id="RHP-DET-099",
                sample=CodeSample(
                    title="array_key_exists to isset",
                    before="if (array_key_exists('k', $arr)) {}",
                    after="if (isset($arr['k'])) {}",
                    expected="if (isset($arr['k'])) {}",
                    explanation="Can be converted deterministically without LLM",
                ),
                reasoning="Deterministic AST transform saves LLM calls",
            )
        ],
        context_proposal=ContextMdProposal(
            title="Converting array_key_exists to isset",
            category="syntax",
            markdown_content="Always prefer `isset($dict['key'])` for associative lookups.",
        ),
    )
    mock_client.queue_response(custom_report)

    result = critic.analyze(
        file_path="tests/fixture.php",
        php_version="8.2",
        original_code="setcookie('a', 'b');",
        migrated_code="Cookie::raw_set('a', 'b');",
        final_code="Cookie::queue('a', 'b');",
    )

    assert result.file_path == "tests/fixture.php"
    assert len(result.migrate_mistakes) == 1
    assert result.migrate_mistakes[0].title == "Incorrect cookie cast"
    assert len(result.migrate_missed_opportunities) == 1
    assert result.context_proposal is not None
    assert result.context_proposal.title == "Converting array_key_exists to isset"


# ---------------------------------------------------------------------------
# Feedback Dispatcher Tests
# ---------------------------------------------------------------------------

def test_feedback_local_dispatch_and_approval(tmp_path: Path):
    dispatcher = FeedbackDispatcher(base_dir=tmp_path)

    report = MigrationCriticReport(
        file_path="src/LegacyService.php",
        php_version="8.1",
        rhp_language_gaps=[
            RHPLanguageGap(
                feature_name="array_filter callback arrow functions",
                php_equivalent="array_filter($arr, fn($x) => $x > 0)",
                suggested_rhp_syntax="$arr->filter(fn(x) => x > 0)",
                impact="high",
                example=CodeSample(
                    title="Array filter",
                    before="array_filter($x, fn($v) => $v > 0)",
                    after="/* manual foreach */",
                    expected="$x->filter(fn($v) => $v > 0)",
                    explanation="RHP lacks array_filter stdlib",
                ),
            )
        ],
        context_proposal=ContextMdProposal(
            title="Refactoring array_filter to foreach accumulator",
            category="stdlib",
            markdown_content="Until RHP adds Collections API, refactor array_filter into a typed for-in loop.",
        ),
    )

    # 1. Dispatch locally
    res = dispatcher.dispatch(report)
    assert Path(res["local_path"]).exists()
    assert dispatcher.jsonl_path.exists()
    assert dispatcher.pending_context_path.exists()
    assert res["has_proposal"] is True

    # 2. Check pending contexts
    pending = dispatcher.load_pending_contexts()
    assert len(pending) == 1
    assert pending[0]["file"] == "src/LegacyService.php"
    assert "Refactoring array_filter" in pending[0]["content"]

    # 3. Approve and merge into target context.md
    target_md = tmp_path / "rules" / "context.md"
    target_md.parent.mkdir(parents=True, exist_ok=True)
    target_md.write_text("# Knowledge Base\n", encoding="utf-8")

    approved_count = dispatcher.approve_all_pending(target_md)
    assert approved_count == 1

    merged_content = target_md.read_text(encoding="utf-8")
    assert "Refactoring array_filter to foreach accumulator" in merged_content
    # Pending file should now be emptied
    assert len(dispatcher.load_pending_contexts()) == 0


@patch("urllib.request.urlopen")
def test_feedback_remote_dispatch(mock_urlopen, tmp_path: Path):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "recorded", "id": 123}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    dispatcher = FeedbackDispatcher(
        base_dir=tmp_path,
        central_url="https://api.rhp.dev/v1/telemetry",
        api_key="secret-token",
    )

    report = MigrationCriticReport(
        file_path="src/App.php",
        php_version="8.2",
    )

    sent, msg = dispatcher.send_remote(report)
    assert sent is True
    assert "recorded" in msg
    assert mock_urlopen.call_count == 1
