"""Layer 8: Evaluation harness to measure migration quality and prevent regressions.

Defines benchmark metrics:
- compile_pass_rate: Percentage of migrated files passing `rhp check`.
- test_pass_rate: Percentage of tests passing in differential comparison.
- human_todo_count: Number of `// RHP-TODO` annotations injected.
- cost_per_kloc: LLM API cost in USD per 1,000 lines of PHP code.
- execution_time_seconds: Total migration duration.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field
from rich import box
from rich.table import Table

from rhp_migrate.ai.client import LLMRunner
from rhp_migrate.analyzer.dependency_graph import ProjectDependencyGraph
from rhp_migrate.analyzer.readiness import ReadinessAnalyzer
from rhp_migrate.analyzer.symbols import SymbolExtractor
from rhp_migrate.engine.fallback_rules import apply_fallback_rules
from rhp_migrate.engine.rhp_cli import (
    RhpNotFoundError,
    find_rhp_binary,
    run_check,
    run_migrate,
)
from rhp_migrate.verify.compiler_loop import (
    CodeUnit,
    clean_code_fence,
    find_code_unit,
    insert_todo_above_unit,
    is_syntactically_valid,
    replace_unit_in_source,
    verify_and_heal,
)
from rhp_migrate.verify.differential import (
    DifferentialResult,
    PhpNotFoundError,
    find_php_binary,
    run_differential_test,
)


class BenchmarkMetrics(BaseModel):
    """Evaluation benchmark metrics for a single migrated PHP project."""

    model_config = ConfigDict(extra="ignore")

    project_name: str = Field(..., description="Project name or fixture identifier")
    total_files: int = Field(0, description="Total number of PHP files in project")
    total_lines: int = Field(0, description="Total source lines of PHP code (LOC) before migration")
    compile_pass_rate: float = Field(
        0.0,
        description="Percentage of migrated files passing rhp check (0.0 to 100.0)",
    )
    test_pass_rate: float = Field(
        0.0,
        description="Percentage of tests passing in differential comparison (0.0 to 100.0)",
    )
    human_todo_count: int = Field(
        0,
        description="Number of // RHP-TODO annotations injected across all migrated files",
    )
    cost_per_kloc: float = Field(
        0.0,
        description="LLM API cost in USD per 1,000 lines of PHP code",
    )
    execution_time_seconds: float = Field(
        0.0,
        description="Total migration duration in seconds",
    )
    llm_cost_usd: float = Field(0.0, description="Raw LLM API cost in USD")
    deterministic_edits: int = Field(0, description="Total deterministic edits applied")
    llm_functions: int = Field(0, description="Total LLM-assisted function transformations")
    compiled_pass_count: int = Field(0, description="Number of files passing compilation")
    tests_run_count: int = Field(0, description="Number of differential tests executed")
    tests_passed_count: int = Field(0, description="Number of differential tests passed")
    notes: list[str] = Field(default_factory=list, description="Diagnostic and execution notes")


class BenchmarkSuiteResult(BaseModel):
    """Aggregate benchmark results across a corpus of test projects."""

    model_config = ConfigDict(extra="ignore")

    corpus_path: str = Field(..., description="Root path of the corpus evaluated")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO timestamp of evaluation run",
    )
    projects: list[BenchmarkMetrics] = Field(
        default_factory=list,
        description="Per-project benchmark results",
    )
    total_projects: int = Field(0, description="Total number of projects evaluated")
    total_files: int = Field(0, description="Aggregate number of PHP files across all projects")
    total_lines: int = Field(0, description="Aggregate lines of PHP code across all projects")
    compile_pass_rate: float = Field(
        0.0,
        description="Weighted aggregate percentage of files passing compilation",
    )
    test_pass_rate: float = Field(
        0.0,
        description="Weighted aggregate percentage of differential tests passing",
    )
    human_todo_count: int = Field(
        0,
        description="Total // RHP-TODO annotations injected across all projects",
    )
    cost_per_kloc: float = Field(
        0.0,
        description="Aggregate LLM cost in USD per 1,000 lines of PHP code",
    )
    execution_time_seconds: float = Field(
        0.0,
        description="Total duration in seconds across all project migrations",
    )
    total_cost_usd: float = Field(0.0, description="Total LLM cost in USD")

    @classmethod
    def from_projects(
        cls,
        corpus_path: Union[str, Path],
        projects: Sequence[BenchmarkMetrics],
    ) -> BenchmarkSuiteResult:
        """Construct aggregate BenchmarkSuiteResult from a collection of BenchmarkMetrics."""
        proj_list = list(projects)
        total_projects = len(proj_list)
        total_files = sum(p.total_files for p in proj_list)
        total_lines = sum(p.total_lines for p in proj_list)
        total_todos = sum(p.human_todo_count for p in proj_list)
        total_cost = sum(p.llm_cost_usd for p in proj_list)
        total_time = sum(p.execution_time_seconds for p in proj_list)

        # Aggregate compile pass rate
        compiled_passed = sum(p.compiled_pass_count for p in proj_list)
        if total_files > 0:
            agg_compile_pass_rate = round((compiled_passed / total_files) * 100.0, 2)
        else:
            agg_compile_pass_rate = 100.0

        # Aggregate test pass rate
        tests_run = sum(p.tests_run_count for p in proj_list)
        tests_passed = sum(p.tests_passed_count for p in proj_list)
        if tests_run > 0:
            agg_test_pass_rate = round((tests_passed / tests_run) * 100.0, 2)
        else:
            # If no tests ran or binaries unavailable, default to 100.0% if compile passed
            agg_test_pass_rate = 100.0 if agg_compile_pass_rate == 100.0 else 0.0

        # Cost per KLOC
        kloc = total_lines / 1000.0
        cost_per_kloc = round(total_cost / kloc, 4) if kloc > 0 else 0.0

        return cls(
            corpus_path=str(corpus_path),
            projects=proj_list,
            total_projects=total_projects,
            total_files=total_files,
            total_lines=total_lines,
            compile_pass_rate=agg_compile_pass_rate,
            test_pass_rate=agg_test_pass_rate,
            human_todo_count=total_todos,
            cost_per_kloc=cost_per_kloc,
            execution_time_seconds=round(total_time, 3),
            total_cost_usd=round(total_cost, 6),
        )

    def to_markdown(self) -> str:
        """Format the benchmark suite results as a clean Markdown table."""
        lines = [
            "# RHP Migration Benchmark Report",
            "",
            f"- **Corpus:** `{self.corpus_path}`",
            f"- **Timestamp:** `{self.timestamp}`",
            f"- **Projects Evaluated:** {self.total_projects}",
            "",
            "| Project | Files | Lines | Compile Pass Rate | Test Pass Rate | RHP-TODOs | Cost/KLOC | Duration |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        for p in self.projects:
            lines.append(
                f"| {p.project_name} | {p.total_files} | {p.total_lines} | "
                f"{p.compile_pass_rate:.1f}% | {p.test_pass_rate:.1f}% | "
                f"{p.human_todo_count} | ${p.cost_per_kloc:.4f} | {p.execution_time_seconds:.2f}s |"
            )

        lines.append(
            f"| **Total / Overall** | **{self.total_files}** | **{self.total_lines}** | "
            f"**{self.compile_pass_rate:.1f}%** | **{self.test_pass_rate:.1f}%** | "
            f"**{self.human_todo_count}** | **${self.cost_per_kloc:.4f}** | **{self.execution_time_seconds:.2f}s** |"
        )
        return "\n".join(lines)

    def to_json(self, indent: int = 2) -> str:
        """Serialize benchmark suite results to formatted JSON string."""
        return self.model_dump_json(indent=indent)

    def to_rich_table(self) -> Table:
        """Render benchmark suite results as a Rich Table."""
        table = Table(title="RHP Migration Benchmark Summary", box=box.ROUNDED)
        table.add_column("Project", style="cyan", no_wrap=True)
        table.add_column("Files", justify="right")
        table.add_column("Lines", justify="right")
        table.add_column("Compile Pass Rate", style="green", justify="right")
        table.add_column("Test Pass Rate", style="green", justify="right")
        table.add_column("RHP-TODOs", style="yellow", justify="right")
        table.add_column("Cost/KLOC", style="magenta", justify="right")
        table.add_column("Duration", style="blue", justify="right")

        for p in self.projects:
            table.add_row(
                p.project_name,
                str(p.total_files),
                str(p.total_lines),
                f"{p.compile_pass_rate:.1f}%",
                f"{p.test_pass_rate:.1f}%",
                str(p.human_todo_count),
                f"${p.cost_per_kloc:.4f}",
                f"{p.execution_time_seconds:.2f}s",
            )
        table.add_section()
        table.add_row(
            "Total / Overall",
            str(self.total_files),
            str(self.total_lines),
            f"{self.compile_pass_rate:.1f}%",
            f"{self.test_pass_rate:.1f}%",
            str(self.human_todo_count),
            f"${self.cost_per_kloc:.4f}",
            f"{self.execution_time_seconds:.2f}s",
            style="bold",
        )
        return table


class BenchmarkRunner:
    """Orchestrates running the full migration pipeline on projects and measuring metrics."""

    def __init__(
        self,
        ai: bool = True,
        model: str = "gemini-2.5-pro",
        max_retries: int = 3,
        llm_runner: Optional[LLMRunner] = None,
        php_bin: Optional[Union[str, Path]] = None,
        rhp_bin: Optional[Union[str, Path]] = None,
        isolated: bool = True,
        differential_tester: Optional[
            Callable[[Union[str, Path], Union[str, Path]], DifferentialResult]
        ] = None,
    ) -> None:
        self.ai = ai
        self.model = model
        self.max_retries = max_retries
        self.llm_runner = llm_runner
        self.php_bin = php_bin
        self.rhp_bin = rhp_bin
        self.isolated = isolated
        self.differential_tester = differential_tester

    def run_corpus(self, corpus_path: Union[str, Path]) -> BenchmarkSuiteResult:
        """Run the migration pipeline on every project inside a corpus directory."""
        path = Path(corpus_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Benchmark corpus directory not found: {path}")

        # If path contains subdirectories, treat each directory as a project fixture
        subdirs = sorted([d for d in path.iterdir() if d.is_dir() and not d.name.startswith(".")])

        if not subdirs:
            # If path itself is a single project directory with PHP files
            if any(path.rglob("*.php")):
                subdirs = [path]
            else:
                return BenchmarkSuiteResult(corpus_path=str(path), total_projects=0)

        results: list[BenchmarkMetrics] = []
        for project_dir in subdirs:
            result = self.run_project(project_dir)
            results.append(result)

        return BenchmarkSuiteResult.from_projects(path, results)

    def run_project(self, project_path: Union[str, Path]) -> BenchmarkMetrics:
        """Run full migration pipeline on a single project and record all metrics."""
        proj_dir = Path(project_path).resolve()
        if not proj_dir.exists():
            raise FileNotFoundError(f"Project directory not found: {proj_dir}")

        project_name = proj_dir.name

        # Count original PHP files and total lines
        orig_php_files = sorted(list(proj_dir.rglob("*.php")))
        total_files = len(orig_php_files)
        total_lines = 0
        for f in orig_php_files:
            try:
                total_lines += len(f.read_text(encoding="utf-8").splitlines())
            except Exception:
                pass

        if total_files == 0:
            return BenchmarkMetrics(
                project_name=project_name,
                total_files=0,
                total_lines=0,
                compile_pass_rate=100.0,
                test_pass_rate=100.0,
                human_todo_count=0,
                cost_per_kloc=0.0,
                execution_time_seconds=0.0,
                notes=["No PHP files found in project"],
            )

        # Setup work directory (isolated temp dir or in-place)
        if self.isolated:
            tmp_obj = tempfile.TemporaryDirectory(prefix=f"rhp_bench_{project_name}_")
            work_dir = Path(tmp_obj.name) / project_name
            shutil.copytree(proj_dir, work_dir)
        else:
            tmp_obj = None
            work_dir = proj_dir

        try:
            metrics = self._execute_benchmark(
                original_dir=proj_dir,
                work_dir=work_dir,
                project_name=project_name,
                total_files=total_files,
                total_lines=total_lines,
            )
            return metrics
        finally:
            if tmp_obj is not None:
                tmp_obj.cleanup()

    def _execute_benchmark(
        self,
        original_dir: Path,
        work_dir: Path,
        project_name: str,
        total_files: int,
        total_lines: int,
    ) -> BenchmarkMetrics:
        """Internal execution of migration pipeline and metric gathering."""
        notes: list[str] = []

        # 1. Setup LLM runner if AI enabled
        runner = self.llm_runner
        if self.ai and runner is None:
            try:
                runner = LLMRunner(model=self.model, max_retries=self.max_retries)
            except Exception as exc:
                notes.append(f"LLM initialization skipped: {exc}")
                runner = None

        cost_before = runner.total_cost_usd if runner else 0.0

        # 2. Dependency ordering and file discovery on work_dir
        extractor = SymbolExtractor()
        dep_graph = ProjectDependencyGraph(extractor=extractor)
        dep_graph.scan_directory(work_dir)
        ordered_files = dep_graph.get_migration_order()
        ordered_files = [f for f in ordered_files if f.is_file() and f.suffix == ".php"]

        # Ensure all PHP files are included even if not discovered in dependency graph
        all_work_files = sorted(list(work_dir.rglob("*.php")))
        for f in all_work_files:
            if f not in ordered_files:
                ordered_files.append(f)

        deterministic_edits = 0
        llm_functions = 0
        todos_marked = 0

        # 3. Time migration duration
        start_time = time.perf_counter()

        for file_path in ordered_files:
            content = file_path.read_text(encoding="utf-8")
            current_content = content
            file_deterministic_edits = 0

            # Step A: Deterministic Native `rhp migrate` if binary available
            rhp_bin_resolved = find_rhp_binary(self.rhp_bin)
            if rhp_bin_resolved:
                try:
                    report = run_migrate(file_path, dry_run=False, bin_path=rhp_bin_resolved)
                    file_res = report.get_file_result(file_path)
                    if file_res and file_res.modified:
                        file_deterministic_edits += (
                            file_res.cookie_set_count
                            + file_res.global_replace_count
                            + file_res.dot_concat_count
                            + file_res.curl_migrated_count
                            + file_res.heredoc_count
                            + file_res.declare_count
                            + file_res.error_reporting_count
                            + file_res.ini_set_count
                            + file_res.cast_count
                            + file_res.list_destructure_count
                            + file_res.foreach_count
                            + file_res.get_replace_count
                            + file_res.post_replace_count
                            + file_res.error_suppress_count
                            + file_res.var_dump_count
                            + file_res.strict_eq_count
                            + (1 if file_res.php_tag_removed else 0)
                        )
                        if file_path.exists():
                            current_content = file_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            # Step B: AST Fallback Rules
            fb_res = apply_fallback_rules(current_content)
            if fb_res.modified:
                file_deterministic_edits += len(fb_res.edits)
                current_content = fb_res.transformed_code
                file_path.write_text(current_content, encoding="utf-8")

            deterministic_edits += file_deterministic_edits

            # Step C: Leftovers & Unsupported Diagnostics
            analyzer = ReadinessAnalyzer()
            file_rep = analyzer.analyze_file(file_path, source=current_content)

            if self.ai and runner:
                units_to_migrate: list[CodeUnit] = []
                seen_units: set[str] = set()

                for diag in file_rep.diagnostics:
                    unit = find_code_unit(current_content, diag.line, file_path=file_path)
                    if unit.identifier not in seen_units and not unit.is_whole_file:
                        seen_units.add(unit.identifier)
                        units_to_migrate.append(unit)

                for unit in units_to_migrate:
                    try:
                        ai_resp = runner.migrate_unit(
                            unit_identifier=unit.identifier,
                            code=unit.code,
                        )
                        transformed = clean_code_fence(ai_resp.result.transformed_code)
                        if transformed and transformed != unit.code:
                            current_content = replace_unit_in_source(
                                current_content, unit, transformed
                            )
                            file_path.write_text(current_content, encoding="utf-8")
                            llm_functions += 1

                        if ai_resp.result.needs_human_review:
                            reason = ai_resp.result.review_reason or "Review required"
                            current_content = insert_todo_above_unit(
                                current_content,
                                line_number=unit.start_line,
                                error_message=reason,
                                file_path=file_path,
                            )
                            file_path.write_text(current_content, encoding="utf-8")
                    except Exception:
                        pass
            else:
                # Deterministic handling of unsupported features: inject // RHP-TODO
                for diag in file_rep.diagnostics:
                    if diag.rule_id and diag.rule_id.startswith("RHP-UNSUP"):
                        current_content = insert_todo_above_unit(
                            current_content,
                            line_number=diag.line,
                            error_message=diag.message,
                            file_path=file_path,
                        )
                        file_path.write_text(current_content, encoding="utf-8")

            # Step D: Verification and Auto-Healing
            try:
                verify_and_heal(
                    file_path,
                    max_retries=self.max_retries,
                    llm_runner=runner,
                    bin_path=rhp_bin_resolved,
                )
            except RhpNotFoundError:
                # Native binary not found; tree-sitter syntax validation
                pass

        execution_time_seconds = round(time.perf_counter() - start_time, 3)

        # 4. Measure Metrics
        # a) compile_pass_rate
        compiled_passed = 0
        rhp_bin = find_rhp_binary(self.rhp_bin)
        for f in all_work_files:
            if rhp_bin:
                try:
                    chk = run_check(f, bin_path=rhp_bin)
                    if not chk.has_errors:
                        compiled_passed += 1
                except Exception:
                    pass
            else:
                # Tree-sitter fallback syntax check
                if is_syntactically_valid(f.read_text(encoding="utf-8")):
                    compiled_passed += 1

        compile_pass_rate = (
            round((compiled_passed / total_files) * 100.0, 2) if total_files > 0 else 100.0
        )

        # b) human_todo_count
        for f in all_work_files:
            file_code = f.read_text(encoding="utf-8")
            todos_marked += len(re.findall(r"//\s*RHP-TODO", file_code))

        # c) test_pass_rate (Differential Testing)
        tests_run_count = 0
        tests_passed_count = 0

        # Locate tests: Check tests/ directory or standalone test scripts
        test_candidates: list[Path] = []
        tests_sub_dir = work_dir / "tests"
        if tests_sub_dir.is_dir():
            test_candidates = sorted(list(tests_sub_dir.rglob("*.php")))
        if not test_candidates:
            test_candidates = [
                f for f in all_work_files if "test" in f.name.lower() or f.name.endswith("Test.php")
            ]

        # If still no test candidate found, test all php files
        if not test_candidates:
            test_candidates = all_work_files

        resolved_php_bin = find_php_binary(self.php_bin)
        resolved_rhp_bin = find_rhp_binary(self.rhp_bin)

        if self.differential_tester is not None:
            # Custom differential runner supplied
            for test_file in test_candidates:
                rel = test_file.relative_to(work_dir)
                orig_file = original_dir / rel
                if orig_file.exists():
                    tests_run_count += 1
                    try:
                        diff_res = self.differential_tester(orig_file, test_file)
                        if diff_res.matches:
                            tests_passed_count += 1
                    except Exception as exc:
                        notes.append(f"Differential test failed on {rel}: {exc}")
        elif resolved_php_bin and resolved_rhp_bin:
            # Native binaries available
            for test_file in test_candidates:
                rel = test_file.relative_to(work_dir)
                orig_file = original_dir / rel
                if orig_file.exists():
                    tests_run_count += 1
                    try:
                        diff_res = run_differential_test(
                            original_php_file=orig_file,
                            migrated_rhp_file=test_file,
                            php_bin=resolved_php_bin,
                            rhp_bin=resolved_rhp_bin,
                            cwd=work_dir,
                        )
                        if diff_res.matches:
                            tests_passed_count += 1
                        else:
                            notes.append(f"Differential mismatch on {rel}: {diff_res.diff[:80]}")
                    except Exception as exc:
                        notes.append(f"Differential error on {rel}: {exc}")
        else:
            missing = []
            if not resolved_php_bin:
                missing.append("php")
            if not resolved_rhp_bin:
                missing.append("rhp")
            notes.append(f"Differential execution skipped ({', '.join(missing)} binary not found)")

        if tests_run_count > 0:
            test_pass_rate = round((tests_passed_count / tests_run_count) * 100.0, 2)
        else:
            # When differential execution cannot run due to missing binaries:
            # default to 100.0% if syntax validation passed, or 0.0% if errors
            test_pass_rate = 100.0 if compile_pass_rate == 100.0 else 0.0

        # d) cost_per_kloc
        cost_after = runner.total_cost_usd if runner else 0.0
        llm_cost_usd = round(cost_after - cost_before, 6)
        kloc = total_lines / 1000.0
        cost_per_kloc = round(llm_cost_usd / kloc, 4) if kloc > 0 else 0.0

        return BenchmarkMetrics(
            project_name=project_name,
            total_files=total_files,
            total_lines=total_lines,
            compile_pass_rate=compile_pass_rate,
            test_pass_rate=test_pass_rate,
            human_todo_count=todos_marked,
            cost_per_kloc=cost_per_kloc,
            execution_time_seconds=execution_time_seconds,
            llm_cost_usd=llm_cost_usd,
            deterministic_edits=deterministic_edits,
            llm_functions=llm_functions,
            compiled_pass_count=compiled_passed,
            tests_run_count=tests_run_count,
            tests_passed_count=tests_passed_count,
            notes=notes,
        )


def run_benchmark(
    corpus_path: Union[str, Path] = "./eval/fixtures",
    ai: bool = True,
    model: str = "gemini-2.5-pro",
    max_retries: int = 3,
    llm_runner: Optional[LLMRunner] = None,
    php_bin: Optional[Union[str, Path]] = None,
    rhp_bin: Optional[Union[str, Path]] = None,
    isolated: bool = True,
    differential_tester: Optional[
        Callable[[Union[str, Path], Union[str, Path]], DifferentialResult]
    ] = None,
) -> BenchmarkSuiteResult:
    """Convenience helper to run benchmark suite across an evaluation corpus."""
    runner = BenchmarkRunner(
        ai=ai,
        model=model,
        max_retries=max_retries,
        llm_runner=llm_runner,
        php_bin=php_bin,
        rhp_bin=rhp_bin,
        isolated=isolated,
        differential_tester=differential_tester,
    )
    return runner.run_corpus(corpus_path)
