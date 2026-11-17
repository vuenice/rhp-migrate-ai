"""Unit and integration tests for Layer 8: Evaluation Harness and Benchmark Runner."""

import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from rhp_migrate.ai.client import LLMRunner, MockLLMClient
from rhp_migrate.ai.models import (
    AIExecutionStats,
    AIMigrationResponse,
    CostEstimate,
    LLMMigrationResult,
    PatchType,
    TokenUsage,
)
from rhp_migrate.cli import app
from rhp_migrate.eval.benchmark import (
    BenchmarkMetrics,
    BenchmarkRunner,
    BenchmarkSuiteResult,
    run_benchmark,
)
from rhp_migrate.verify.differential import DifferentialResult

runner = CliRunner()


# ===========================================================================
# Part 1: Benchmark Model Tests
# ===========================================================================

def test_benchmark_metrics_initialization():
    """BenchmarkMetrics initializes correctly with default and custom values."""
    metrics = BenchmarkMetrics(
        project_name="sample-project",
        total_files=5,
        total_lines=250,
        compile_pass_rate=98.5,
        test_pass_rate=95.0,
        human_todo_count=2,
        cost_per_kloc=0.0125,
        execution_time_seconds=1.234,
        llm_cost_usd=0.003125,
    )
    assert metrics.project_name == "sample-project"
    assert metrics.total_files == 5
    assert metrics.total_lines == 250
    assert metrics.compile_pass_rate == 98.5
    assert metrics.test_pass_rate == 95.0
    assert metrics.human_todo_count == 2
    assert metrics.cost_per_kloc == 0.0125
    assert metrics.execution_time_seconds == 1.234
    assert metrics.llm_cost_usd == 0.003125


def test_benchmark_suite_result_aggregation():
    """BenchmarkSuiteResult aggregates metrics accurately across multiple projects."""
    p1 = BenchmarkMetrics(
        project_name="proj-1",
        total_files=10,
        total_lines=500,
        compile_pass_rate=100.0,
        test_pass_rate=100.0,
        human_todo_count=1,
        cost_per_kloc=0.02,
        execution_time_seconds=2.0,
        llm_cost_usd=0.01,
        compiled_pass_count=10,
        tests_run_count=4,
        tests_passed_count=4,
    )
    p2 = BenchmarkMetrics(
        project_name="proj-2",
        total_files=10,
        total_lines=500,
        compile_pass_rate=80.0,
        test_pass_rate=50.0,
        human_todo_count=3,
        cost_per_kloc=0.04,
        execution_time_seconds=3.0,
        llm_cost_usd=0.02,
        compiled_pass_count=8,
        tests_run_count=4,
        tests_passed_count=2,
    )

    suite = BenchmarkSuiteResult.from_projects("/path/to/corpus", [p1, p2])
    assert suite.total_projects == 2
    assert suite.total_files == 20
    assert suite.total_lines == 1000
    assert suite.human_todo_count == 4
    assert suite.compile_pass_rate == 90.0  # 18/20 = 90%
    assert suite.test_pass_rate == 75.0     # 6/8 = 75%
    assert suite.execution_time_seconds == 5.0
    assert suite.total_cost_usd == 0.03
    assert suite.cost_per_kloc == 0.03      # 0.03 USD / 1.0 KLOC = 0.03


def test_benchmark_suite_serialization():
    """to_markdown, to_json, and to_rich_table produce expected formats."""
    p = BenchmarkMetrics(
        project_name="mini-blog",
        total_files=4,
        total_lines=150,
        compile_pass_rate=100.0,
        test_pass_rate=100.0,
        human_todo_count=0,
        cost_per_kloc=0.0,
        execution_time_seconds=0.45,
        compiled_pass_count=4,
    )
    suite = BenchmarkSuiteResult.from_projects("./eval/fixtures", [p])

    # Markdown check
    md = suite.to_markdown()
    assert "# RHP Migration Benchmark Report" in md
    assert "mini-blog" in md
    assert "100.0%" in md
    assert "| **Total / Overall** |" in md

    # JSON check
    json_str = suite.to_json()
    data = json.loads(json_str)
    assert data["total_projects"] == 1
    assert data["projects"][0]["project_name"] == "mini-blog"
    assert data["compile_pass_rate"] == 100.0

    # Rich table check
    table = suite.to_rich_table()
    assert table.title == "RHP Migration Benchmark Summary"
    assert len(table.columns) == 8


# ===========================================================================
# Part 2: Benchmark Runner Integration Tests
# ===========================================================================

def test_runner_on_mini_blog_fixture():
    """BenchmarkRunner processes mini-blog fixture and produces valid metrics."""
    corpus = Path("./eval/fixtures/mini-blog").resolve()
    assert corpus.exists(), "mini-blog fixture must exist"

    bench_runner = BenchmarkRunner(ai=False, isolated=True)
    res = bench_runner.run_project(corpus)

    assert res.project_name == "mini-blog"
    assert res.total_files == 5
    assert res.total_lines > 50
    assert res.compile_pass_rate == 100.0
    assert res.test_pass_rate == 100.0
    assert res.execution_time_seconds >= 0.0


def test_runner_on_curl_consumer_fixture():
    """BenchmarkRunner processes curl-consumer fixture."""
    corpus = Path("./eval/fixtures/curl-consumer").resolve()
    assert corpus.exists(), "curl-consumer fixture must exist"

    bench_runner = BenchmarkRunner(ai=False, isolated=True)
    res = bench_runner.run_project(corpus)

    assert res.project_name == "curl-consumer"
    assert res.total_files == 3
    assert res.compile_pass_rate == 100.0


def test_runner_on_unsupported_patterns_fixture():
    """BenchmarkRunner flags unsupported patterns and records human_todo_count."""
    corpus = Path("./eval/fixtures/unsupported-patterns").resolve()
    assert corpus.exists(), "unsupported-patterns fixture must exist"

    bench_runner = BenchmarkRunner(ai=False, isolated=True)
    res = bench_runner.run_project(corpus)

    assert res.project_name == "unsupported-patterns"
    assert res.total_files == 4
    # Unsupported patterns must inject // RHP-TODO annotations
    assert res.human_todo_count > 0


def test_runner_preserves_fixtures_in_isolated_mode():
    """Isolated mode ensures original fixture files are untouched."""
    fixture_dir = Path("./eval/fixtures/mini-blog").resolve()
    file_to_check = fixture_dir / "app" / "Models" / "Post.php"
    original_mtime = file_to_check.stat().st_mtime
    original_content = file_to_check.read_text(encoding="utf-8")

    bench_runner = BenchmarkRunner(ai=False, isolated=True)
    bench_runner.run_project(fixture_dir)

    assert file_to_check.read_text(encoding="utf-8") == original_content
    assert file_to_check.stat().st_mtime == original_mtime


def test_runner_differential_tester_callback():
    """Custom differential tester callback calculates test_pass_rate accurately."""
    fixture_dir = Path("./eval/fixtures/mini-blog").resolve()

    # Callback that matches all tests
    perfect_diff = lambda orig, mig: DifferentialResult(
        matches=True,
        php_output="OK",
        rhp_output="OK",
        diff="",
    )
    runner_perfect = BenchmarkRunner(
        ai=False,
        isolated=True,
        differential_tester=perfect_diff,
    )
    res_perfect = runner_perfect.run_project(fixture_dir)
    assert res_perfect.tests_run_count > 0
    assert res_perfect.test_pass_rate == 100.0

    # Callback that fails all tests
    failing_diff = lambda orig, mig: DifferentialResult(
        matches=False,
        php_output="A",
        rhp_output="B",
        diff="Mismatch",
        stdout_matches=False,
    )
    runner_failing = BenchmarkRunner(
        ai=False,
        isolated=True,
        differential_tester=failing_diff,
    )
    res_failing = runner_failing.run_project(fixture_dir)
    assert res_failing.tests_run_count > 0
    assert res_failing.test_pass_rate == 0.0


def test_runner_with_mock_llm():
    """BenchmarkRunner records LLM cost and computes cost_per_kloc."""
    mock_client = MockLLMClient(
        responses=[
            LLMMigrationResult(
                unit_identifier="Unit1",
                transformed_code="function test(): void {}",
                needs_human_review=True,
                review_reason="Manual check",
            )
        ]
    )
    llm_runner = LLMRunner(provider=mock_client, model="gemini-2.5-pro")

    fixture_dir = Path("./eval/fixtures/mini-blog").resolve()
    bench_runner = BenchmarkRunner(
        ai=True,
        llm_runner=llm_runner,
        isolated=True,
    )
    res = bench_runner.run_project(fixture_dir)

    assert res.total_files > 0
    assert res.cost_per_kloc >= 0.0


def test_run_corpus_benchmark():
    """run_benchmark convenience function evaluates entire fixtures corpus."""
    suite = run_benchmark(corpus_path="./eval/fixtures", ai=False, isolated=True)
    assert suite.total_projects == 3
    assert suite.total_files == 12
    assert suite.compile_pass_rate == 100.0
    assert suite.test_pass_rate == 100.0
    assert suite.human_todo_count > 0
    assert suite.execution_time_seconds >= 0.0


# ===========================================================================
# Part 3: CLI Command Tests
# ===========================================================================

def test_cli_benchmark_default():
    """rhp-migrate-ai benchmark runs and outputs markdown summary."""
    res = runner.invoke(app, ["benchmark", "--corpus=./eval/fixtures/", "--no-ai"])
    assert res.exit_code == 0
    assert "# RHP Migration Benchmark Report" in res.stdout
    assert "mini-blog" in res.stdout
    assert "curl-consumer" in res.stdout
    assert "unsupported-patterns" in res.stdout
    assert "Total / Overall" in res.stdout


def test_cli_benchmark_json_format():
    """rhp-migrate-ai benchmark --format=json outputs valid JSON string."""
    res = runner.invoke(app, ["benchmark", "--corpus=./eval/fixtures/", "--format=json", "--no-ai"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["total_projects"] == 3
    assert "compile_pass_rate" in data
    assert "test_pass_rate" in data
    assert "human_todo_count" in data
    assert "cost_per_kloc" in data
    assert "execution_time_seconds" in data


def test_cli_benchmark_table_format():
    """rhp-migrate-ai benchmark --format=table outputs Rich summary table."""
    res = runner.invoke(app, ["benchmark", "--corpus=./eval/fixtures/", "--format=table", "--no-ai"])
    assert res.exit_code == 0
    assert "RHP Migration Benchmark Summary" in res.stdout
    assert "mini-blog" in res.stdout


def test_cli_benchmark_output_file(tmp_path: Path):
    """rhp-migrate-ai benchmark saves report to file when -o option provided."""
    out_file = tmp_path / "benchmark_report.md"
    res = runner.invoke(
        app,
        ["benchmark", "--corpus=./eval/fixtures/", "--no-ai", "-o", str(out_file)],
    )
    assert res.exit_code == 0
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "# RHP Migration Benchmark Report" in content
    assert "mini-blog" in content


def test_cli_benchmark_invalid_corpus():
    """rhp-migrate-ai benchmark errors with non-zero exit code if corpus path missing."""
    res = runner.invoke(app, ["benchmark", "--corpus=/invalid/corpus/path/12345"])
    assert res.exit_code == 1
    assert "Corpus directory does not exist" in res.stdout
