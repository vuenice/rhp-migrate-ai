"""Layer 7: User-facing CLI with incremental dependency execution, atomic git commits, and hash caching."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Optional
import typer
from typer.core import TyperGroup
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from rhp_migrate.ai.client import LLMRunner
from rhp_migrate.ai.critic import MigrationCritic
from rhp_migrate.analyzer.dependency_graph import ProjectDependencyGraph
from rhp_migrate.analyzer.readiness import ReadinessAnalyzer
from rhp_migrate.analyzer.symbols import SymbolExtractor
from rhp_migrate.core.cache import MigrationCache
from rhp_migrate.core.feedback import FeedbackDispatcher
from rhp_migrate.core.git_manager import GitManager
from rhp_migrate.core.version_detector import detect_php_version
from rhp_migrate.engine.fallback_rules import apply_fallback_rules
from rhp_migrate.engine.rhp_cli import (
    BuildReport,
    LintReport,
    MigrateReport,
    RhpNotFoundError,
    find_rhp_binary,
    run_build,
    run_check,
    run_lint,
    run_migrate,
)
from rhp_migrate.eval.benchmark import BenchmarkRunner
from rhp_migrate.verify.compiler_loop import (
    CodeUnit,
    clean_code_fence,
    find_code_unit,
    insert_todo_above_unit,
    is_syntactically_valid,
    replace_unit_in_source,
    verify_and_heal,
)
from rhp_migrate.verify.deterministic_audit import (
    DeterministicMistakeRecord,
    DeterministicValidator,
)



class DefaultCommandGroup(TyperGroup):
    """Custom TyperGroup that defaults to the 'migrate' command when no subcommand is specified."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        if not args:
            args = ["migrate"]
        elif args[0] not in self.commands and args[0] not in ("--help", "-h"):
            args = ["migrate"] + args
        return super().parse_args(ctx, args)

    def format_help(self, ctx: Any, formatter: Any) -> None:
        super().format_help(ctx, formatter)
        cmd = self.get_command(ctx, "migrate")
        if cmd:
            formatter.write("\n")
            with formatter.section("Default Command (migrate) Options"):
                cmd.format_options(ctx, formatter)



app = typer.Typer(
    name="rhp-migrate-ai",
    help="AI-powered migration tool from PHP/Laravel to strictly-typed RHP.",
    add_completion=False,
    cls=DefaultCommandGroup,
)
console = Console()


def render_analysis_table(target_path: Path, analyzer: ReadinessAnalyzer) -> None:
    """Render a rich table summarizing the pre-analysis report."""
    if target_path.is_file():
        file_report = analyzer.analyze_file(target_path)
        title = f"RHP Pre-Analysis Report: {target_path.name}"
        table = Table(title=title, box=box.ROUNDED)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="magenta")

        table.add_row("File", str(target_path))
        table.add_row("Readiness", f"{file_report.readiness_percentage * 100:.1f}%")
        table.add_row("Unsupported Features", str(file_report.unsupported_count))
        table.add_row("Untyped Parameters", str(file_report.untyped_param_count))
        table.add_row("Total Diagnostics", str(len(file_report.diagnostics)))
        console.print(table)

        if file_report.diagnostics:
            diag_table = Table(title="Diagnostics", box=box.SIMPLE)
            diag_table.add_column("Line:Col", style="yellow")
            diag_table.add_column("Severity", style="red")
            diag_table.add_column("Rule", style="cyan")
            diag_table.add_column("Message")
            for d in file_report.diagnostics:
                diag_table.add_row(
                    f"{d.line}:{d.column}",
                    d.severity.value,
                    d.rule_id or "-",
                    d.message,
                )
            console.print(diag_table)
    else:
        report = analyzer.analyze_project(target_path)
        table = Table(title="RHP Pre-Analysis Report", box=box.ROUNDED)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value", style="magenta")

        table.add_row("Total Files Analyzed", str(report.total_files))
        table.add_row("Overall Readiness", f"{report.overall_readiness * 100:.1f}%")
        table.add_row("Unsupported Features", str(report.total_unsupported_count))
        table.add_row("Untyped Parameters", str(report.total_untyped_param_count))
        console.print(table)

        if report.migration_order:
            order_table = Table(title="Recommended Dependency Migration Order", box=box.SIMPLE)
            order_table.add_column("#", style="dim", width=4)
            order_table.add_column("File", style="cyan")
            order_table.add_column("Readiness", style="green")
            for idx, p in enumerate(report.migration_order, 1):
                fr = report.get_file_report(p)
                score_str = f"{fr.readiness_percentage * 100:.0f}%" if fr else "-"
                order_table.add_row(str(idx), p.name, score_str)
            console.print(order_table)


def _handle_companion_files(
    companion_files: list[Any],
    base_dir: Path,
    git_mgr: Optional[GitManager],
    dry_run: bool,
) -> None:
    """Write companion files (e.g. .rade.rhp templates) to disk and stage in git."""
    for comp in companion_files:
        comp_path = (base_dir / comp.file_path).resolve()
        if not dry_run:
            comp_path.parent.mkdir(parents=True, exist_ok=True)
            comp_path.write_text(comp.content, encoding="utf-8")
        console.print(
            f"[bold green]Created companion template:[/bold green] [cyan]{comp.file_path}[/cyan]"
        )
        if git_mgr is not None and not dry_run and git_mgr.is_git_repo():
            try:
                git_mgr._run_git(["add", str(comp_path)])
            except Exception:
                pass


def _extract_units_for_errors(
    source: str,
    file_path: Path,
    errors: list[str],
    warnings: list[str],
) -> list[CodeUnit]:
    """Identify targeted code units for errors/warnings."""
    units: list[CodeUnit] = []
    seen: set[str] = set()

    for msg in errors + warnings:
        m = re.search(r"line\s+(\d+)", msg, re.IGNORECASE)
        if m:
            line_num = int(m.group(1))
            unit = find_code_unit(source, line_num, file_path=file_path)
            if unit.identifier not in seen and not unit.is_whole_file:
                seen.add(unit.identifier)
                units.append(unit)

    if not units:
        analyzer = ReadinessAnalyzer()
        rep = analyzer.analyze_file(file_path, source=source)
        for diag in rep.diagnostics:
            unit = find_code_unit(source, diag.line, file_path=file_path)
            if unit.identifier not in seen and not unit.is_whole_file:
                seen.add(unit.identifier)
                units.append(unit)

    if not units:
        unit = find_code_unit(source, 1, file_path=file_path)
        units.append(unit)

    return units


def _extract_units_for_diagnostics(
    source: str,
    file_path: Path,
    diagnostics: list[Any],
) -> list[CodeUnit]:
    """Identify targeted code units for lint/build diagnostics."""
    units: list[CodeUnit] = []
    seen: set[str] = set()

    for d in diagnostics:
        line_num = getattr(d, "line", 1)
        unit = find_code_unit(source, line_num, file_path=file_path)
        if unit.identifier not in seen and not unit.is_whole_file:
            seen.add(unit.identifier)
            units.append(unit)

    if not units:
        analyzer = ReadinessAnalyzer()
        rep = analyzer.analyze_file(file_path, source=source)
        for diag in rep.diagnostics:
            unit = find_code_unit(source, diag.line, file_path=file_path)
            if unit.identifier not in seen and not unit.is_whole_file:
                seen.add(unit.identifier)
                units.append(unit)

    if not units:
        unit = find_code_unit(source, 1, file_path=file_path)
        units.append(unit)

    return units


@app.command(name="migrate")
def main(
    path: Path = typer.Argument(
        Path("."),
        help="Path to PHP file or project directory to migrate.",
        exists=False,
    ),
    analyze_only: bool = typer.Option(
        False,
        "--analyze-only",
        help="Run pre-analysis report only (0 edits).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview changes without writing to disk.",
    ),
    ai: bool = typer.Option(
        True,
        "--ai/--no-ai",
        help="Enable or disable LLM stage for leftovers (default: True).",
    ),
    model: str = typer.Option(
        "gemini-2.5-pro",
        "--model",
        help="LLM model name (default: gemini-2.5-pro).",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        help="Maximum compiler error retry attempts (default: 3).",
    ),
    cache: bool = typer.Option(
        True,
        "--cache/--no-cache",
        help="Enable or disable hash cache.",
    ),
    git: bool = typer.Option(
        True,
        "--git/--no-git",
        help="Create branch and auto-commit each unit.",
    ),
    critic: bool = typer.Option(
        True,
        "--critic/--no-critic",
        help="Run AI continuous improvement critic to analyze tool mistakes & opportunities.",
    ),
    central_url: Optional[str] = typer.Option(
        None,
        "--central-url",
        help="Centralized telemetry/reporting endpoint (overrides RHP_CENTRAL_URL).",
    ),
    php_version: Optional[str] = typer.Option(
        None,
        "--php-version",
        help="Override auto-detected PHP version (e.g. '8.2', '7.4').",
    ),
) -> None:
    """Migrate PHP/Laravel codebase to RHP with dependency ordering, atomic Git commits, and caching."""
    target_path = path.resolve()
    if not target_path.exists():
        console.print(f"[bold red]Error:[/bold red] Target path does not exist: {target_path}")
        raise typer.Exit(code=1)

    # 0. Detect PHP Version
    detected_php = detect_php_version(target_path)
    effective_php = php_version or detected_php.version
    console.print(
        f"[bold green]PHP Target Version:[/bold green] [cyan]{effective_php}[/cyan] "
        f"[dim](Source: {detected_php.source})[/dim]"
    )

    # 1. Analyze-only mode (0 edits)
    if analyze_only:
        analyzer = ReadinessAnalyzer()
        render_analysis_table(target_path, analyzer)
        return

    # 2. Dependency ordering and file discovery
    extractor = SymbolExtractor()
    if target_path.is_file():
        ordered_files = [target_path]
        st = extractor.extract_file(target_path)
        classes_by_file = {target_path: list(st.classes.values())}
        base_dir = target_path.parent
    else:
        dep_graph = ProjectDependencyGraph(extractor=extractor)
        dep_graph.scan_directory(target_path)
        ordered_files = dep_graph.get_migration_order()
        classes_by_file = {
            p: list(table.classes.values())
            for p, table in dep_graph.symbol_tables.items()
        }
        base_dir = target_path

    # Filter only existing regular files
    ordered_files = [f for f in ordered_files if f.is_file()]

    if not ordered_files:
        console.print(f"[yellow]No PHP files found to migrate in {target_path}.[/yellow]")
        _print_summary_table(
            files_scanned=0,
            deterministic_edits=0,
            llm_functions=0,
            todos_marked=0,
            token_cost=0.0,
        )
        return

    # 3. Setup Cache
    cache_mgr: Optional[MigrationCache] = None
    if cache:
        cache_mgr = MigrationCache(base_dir=base_dir)

    # 4. Setup Git Manager
    git_mgr: Optional[GitManager] = None
    if git:
        git_mgr = GitManager(repo_path=base_dir)
        if not git_mgr.is_git_repo():
            console.print(
                "[yellow]Notice: Target is not inside a git repository. Skipping git branch & commits.[/yellow]"
            )
            git_mgr = None
        elif not dry_run:
            try:
                branch = git_mgr.create_migration_branch()
                console.print(f"[bold green]Created migration branch:[/bold green] [cyan]{branch}[/cyan]")
            except Exception as exc:
                console.print(f"[yellow]Warning: Could not create git branch: {exc}[/yellow]")
                git_mgr = None

    # 5. Setup LLM Runner
    llm_runner: Optional[LLMRunner] = None
    if ai:
        try:
            llm_runner = LLMRunner(model=model, max_retries=max_retries)
        except Exception as exc:
            console.print(
                f"[yellow]Warning: Could not initialize LLM runner for model '{model}': {exc}. Proceeding with deterministic migration only.[/yellow]"
            )
            llm_runner = None

    # 6. Whole-Project 3-Stage Migration Pipeline
    # Snapshot original contents
    original_contents: dict[Path, str] = {f: f.read_text(encoding="utf-8") for f in ordered_files}
    post_migrate_contents: dict[Path, str] = {}
    healing_logs_by_file: dict[Path, list[str]] = {f: [] for f in ordered_files}

    settings_dict = {"ai": ai, "model": model, "max_retries": max_retries}
    files_to_process = [
        f for f in ordered_files
        if not (cache_mgr is not None and cache_mgr.should_skip(f, original_contents[f], settings_dict))
    ]

    files_scanned = len(ordered_files)
    deterministic_edits = 0
    llm_functions = 0
    todos_marked = 0

    if not files_to_process:
        console.print("[green]All files are up-to-date in cache. Skipping migration.[/green]")
        _print_summary_table(
            files_scanned=files_scanned,
            deterministic_edits=0,
            llm_functions=0,
            todos_marked=0,
            token_cost=0.0,
        )
        return

    rhp_bin = find_rhp_binary()

    # =========================================================================
    # STAGE 1: Deterministic `rhp migrate` on Whole Project
    # =========================================================================
    console.print("\n[bold cyan]─── Stage 1: Deterministic 'rhp migrate' (Whole Project) ───[/bold cyan]")
    migrate_rep: Optional[MigrateReport] = None
    if rhp_bin:
        try:
            if target_path.is_file():
                migrate_rep = run_migrate(
                    target_path.parent,
                    file_path=target_path,
                    dry_run=dry_run,
                    php_version=detected_php.major,
                    bin_path=rhp_bin,
                )
            else:
                migrate_rep = run_migrate(
                    target_path,
                    dry_run=dry_run,
                    php_version=detected_php.major,
                    bin_path=rhp_bin,
                )
        except Exception as exc:
            console.print(f"[dim]rhp migrate notice: {exc}[/dim]")

    # Accumulate metrics from migrate_rep
    if migrate_rep:
        for f in files_to_process:
            file_res = migrate_rep.get_file_result(f)
            if file_res and file_res.modified:
                deterministic_edits += (
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
                    + file_res.legacy_include_count
                    + file_res.ob_rewritten_count
                    + file_res.echo_response_count
                    + file_res.rade_directive_count
                    + (1 if file_res.php_tag_removed else 0)
                )

    # Apply AST fallback rules
    for f in files_to_process:
        curr_text = f.read_text(encoding="utf-8")
        fb_res = apply_fallback_rules(curr_text)
        if fb_res.modified:
            deterministic_edits += len(fb_res.edits)
            curr_text = fb_res.transformed_code
            if not dry_run:
                f.write_text(curr_text, encoding="utf-8")

    # Snapshot post-migrate contents
    for f in files_to_process:
        post_migrate_contents[f] = f.read_text(encoding="utf-8")

    # Deterministic syntax audit across files
    validator = DeterministicValidator()
    audit_records: dict[Path, Any] = {}
    stage1_errors_by_file: dict[Path, list[str]] = {}
    stage1_warnings_by_file: dict[Path, list[str]] = {}

    for f in files_to_process:
        curr_text = f.read_text(encoding="utf-8")
        rec = validator.validate(
            before_code=original_contents[f],
            after_code=curr_text,
            file_path=f,
            bin_path=rhp_bin,
        )
        audit_records[f] = rec
        errs: list[str] = []
        warns: list[str] = []
        if migrate_rep:
            f_res = migrate_rep.get_file_result(f)
            if f_res:
                errs.extend(f_res.errors)
                warns.extend(f_res.warnings)
        if not rec.is_valid:
            for d_err in rec.errors:
                loc = f"line {d_err.line}:{d_err.column}"
                code_prefix = f"[{d_err.error_code}] " if d_err.error_code else ""
                errs.append(f"{code_prefix}{loc}: {d_err.message}")
            validator.record_report(rec, base_dir=base_dir)

        if errs:
            stage1_errors_by_file[f] = errs
        if warns:
            stage1_warnings_by_file[f] = warns

    # If errors/warnings in Stage 1, invoke AI operation ONLY on those files
    stage1_troubled_files = set(stage1_errors_by_file.keys()) | set(stage1_warnings_by_file.keys())
    if stage1_troubled_files:
        console.print(
            f"[yellow]Deterministic 'rhp migrate' produced errors/warnings on {len(stage1_troubled_files)} file(s). Performing AI operations...[/yellow]"
        )
        if ai and llm_runner:
            for f in sorted(stage1_troubled_files):
                curr_text = f.read_text(encoding="utf-8")
                f_errs = stage1_errors_by_file.get(f, [])
                f_warns = stage1_warnings_by_file.get(f, [])
                units = _extract_units_for_errors(curr_text, f, f_errs, f_warns)
                for unit in units:
                    try:
                        ai_resp = llm_runner.migrate_unit(
                            unit_identifier=unit.identifier,
                            code=unit.code,
                            compiler_errors=f_errs if f_errs else None,
                            extra_instructions=(
                                f"Deterministic 'rhp migrate' produced errors/warnings on this use case: "
                                f"{'; '.join(f_errs + f_warns)}. "
                                f"Analyze why 'rhp migrate' was not successful doing that use case, "
                                f"resolve all errors and warnings, and return clean strictly-typed RHP code."
                            ),
                        )
                        transformed = clean_code_fence(ai_resp.result.transformed_code)
                        if transformed and transformed != unit.code:
                            curr_text = replace_unit_in_source(curr_text, unit, transformed)
                            if not dry_run:
                                f.write_text(curr_text, encoding="utf-8")
                            llm_functions += 1
                        _handle_companion_files(ai_resp.result.companion_files, base_dir, git_mgr, dry_run)
                        if ai_resp.result.needs_human_review:
                            reason = ai_resp.result.review_reason or "Review required"
                            curr_text = insert_todo_above_unit(curr_text, unit.start_line, reason, f)
                            if not dry_run:
                                f.write_text(curr_text, encoding="utf-8")
                            todos_marked += 1
                    except Exception:
                        pass
    else:
        console.print("[green]✔ Stage 1 completed with 0 deterministic errors/warnings.[/green]")

    # =========================================================================
    # STAGE 2: Whole-Project Linting (`rhp lint`)
    # =========================================================================
    console.print("\n[bold cyan]─── Stage 2: Whole-Project Linting ('rhp lint') ───[/bold cyan]")
    lint_rep: Optional[LintReport] = None
    if rhp_bin:
        try:
            lint_rep = run_lint(target_path, bin_path=rhp_bin, fix=True)
        except Exception as exc:
            console.print(f"[dim]rhp lint notice: {exc}[/dim]")

    lint_diags_by_file: dict[Path, list[Any]] = {}
    if lint_rep and lint_rep.diagnostics:
        for diag in lint_rep.diagnostics:
            p = Path(diag.file).resolve() if diag.file and Path(diag.file).exists() else target_path
            lint_diags_by_file.setdefault(p, []).append(diag)

    if lint_diags_by_file:
        console.print(
            f"[yellow]Stage 2 linting produced {len(lint_rep.diagnostics)} diagnostic(s) across {len(lint_diags_by_file)} file(s). Performing AI operations...[/yellow]"
        )
        if ai and llm_runner:
            for f, diags in sorted(lint_diags_by_file.items()):
                if not f.exists():
                    continue
                curr_text = f.read_text(encoding="utf-8")
                diag_msgs = [f"line {d.line}:{d.column}: [{d.rule_id}] {d.message}" for d in diags]
                units = _extract_units_for_diagnostics(curr_text, f, diags)
                for unit in units:
                    try:
                        ai_resp = llm_runner.migrate_unit(
                            unit_identifier=unit.identifier,
                            code=unit.code,
                            compiler_errors=diag_msgs,
                            extra_instructions=(
                                f"Linting flagged issues after migration: {'; '.join(diag_msgs)}. "
                                f"Analyze why migration command was not successful producing lint-clean code for this use case, "
                                f"resolve the lint issues, enforce strict typing and naming, and return strictly-typed RHP code."
                            ),
                        )
                        transformed = clean_code_fence(ai_resp.result.transformed_code)
                        if transformed and transformed != unit.code:
                            curr_text = replace_unit_in_source(curr_text, unit, transformed)
                            if not dry_run:
                                f.write_text(curr_text, encoding="utf-8")
                            llm_functions += 1
                        _handle_companion_files(ai_resp.result.companion_files, base_dir, git_mgr, dry_run)
                        if ai_resp.result.needs_human_review:
                            reason = ai_resp.result.review_reason or "Review required"
                            curr_text = insert_todo_above_unit(curr_text, unit.start_line, reason, f)
                            if not dry_run:
                                f.write_text(curr_text, encoding="utf-8")
                            todos_marked += 1
                    except Exception:
                        pass
    else:
        console.print("[green]✔ Stage 2 completed with 0 lint errors/warnings.[/green]")

    # =========================================================================
    # STAGE 3: Whole-Project Build (`rhp build`)
    # =========================================================================
    console.print("\n[bold cyan]─── Stage 3: Whole-Project Build ('rhp build') ───[/bold cyan]")
    build_rep: Optional[BuildReport] = None
    if rhp_bin:
        try:
            build_rep = run_build(target_path, bin_path=rhp_bin)
        except Exception as exc:
            console.print(f"[dim]rhp build notice: {exc}[/dim]")

    build_diags_by_file: dict[Path, list[Any]] = {}
    if build_rep and build_rep.diagnostics:
        for diag in build_rep.diagnostics:
            p = Path(diag.file).resolve() if diag.file and Path(diag.file).exists() else target_path
            build_diags_by_file.setdefault(p, []).append(diag)

    # Fallback to analyzer diagnostics if binary is unavailable or produced no diagnostics
    if not rhp_bin or not build_rep:
        analyzer = ReadinessAnalyzer()
        for f in files_to_process:
            c_text = f.read_text(encoding="utf-8")
            f_rep = analyzer.analyze_file(f, source=c_text)
            if f_rep.diagnostics:
                build_diags_by_file.setdefault(f, []).extend(f_rep.diagnostics)

    if build_diags_by_file:
        console.print(
            f"[yellow]Stage 3 build produced issues across {len(build_diags_by_file)} file(s). Performing AI healing & cause analysis...[/yellow]"
        )
        if ai and llm_runner:
            for f in sorted(build_diags_by_file.keys()):
                if not f.exists():
                    continue
                h_log = healing_logs_by_file[f]
                verified = False
                try:
                    if not dry_run:
                        verified = verify_and_heal(
                            f,
                            max_retries=max_retries,
                            llm_runner=llm_runner,
                            healing_log_out=h_log,
                            extra_instructions=(
                                "Compiler build failed on this use case. Analyze why the migration command was not successful "
                                "doing that use case, resolve all type errors and boundary contracts, and return clean RHP code."
                            ),
                        )
                        if h_log:
                            llm_functions += len(h_log)
                        else:
                            # If verify_and_heal completed with 0 retries or unit fix
                            llm_functions += 1
                    else:
                        verified = True
                        llm_functions += 1
                except RhpNotFoundError:
                    verified = is_syntactically_valid(f.read_text(encoding="utf-8"))
                    llm_functions += 1
    else:
        console.print("[green]✔ Stage 3 completed with 0 build errors/warnings.[/green]")

    # =========================================================================
    # STAGE 4: Continuous Improvement Critic, Commits, and Cache
    # =========================================================================
    for f in files_to_process:
        final_code = f.read_text(encoding="utf-8") if f.exists() else ""
        file_todos = len(re.findall(r"//\s*RHP-TODO", final_code))
        if file_todos > todos_marked:
            todos_marked = file_todos

        # AI Critic & Centralized Feedback Loop
        if ai and critic and llm_runner:
            try:
                critic_agent = MigrationCritic(runner=llm_runner)
                critic_report = critic_agent.analyze(
                    file_path=f,
                    php_version=effective_php,
                    original_code=original_contents.get(f, ""),
                    migrated_code=post_migrate_contents.get(f, ""),
                    final_code=final_code,
                    migrate_report=migrate_rep,
                    lint_report=lint_rep,
                    check_report=build_rep,
                    healing_log=healing_logs_by_file.get(f, []),
                )
                rec = audit_records.get(f)
                if rec and not rec.is_valid:
                    critic_report.migrate_mistakes.extend(rec.to_critic_mistakes())
                dispatcher = FeedbackDispatcher(base_dir=base_dir, central_url=central_url)
                dispatcher.dispatch(critic_report)
            except Exception:
                pass

        # Atomic Git Commit
        verified = is_syntactically_valid(final_code)
        if git_mgr is not None and not dry_run and verified:
            classes = classes_by_file.get(f, [])
            unit_name = classes[0].fqcn if classes else (str(f.relative_to(git_mgr.repo_path)) if f.is_relative_to(git_mgr.repo_path) else f.name)
            try:
                git_mgr.commit_file(f, unit_name=unit_name)
            except Exception as exc:
                console.print(f"[yellow]Warning: Git commit failed for {unit_name}: {exc}[/yellow]")

        # Update cache
        if cache_mgr is not None and not dry_run:
            cache_mgr.update(
                file_path=f,
                content=original_contents.get(f, ""),
                settings=settings_dict,
                verified=verified,
                stats={
                    "deterministic_edits": deterministic_edits,
                    "verified": verified,
                },
            )
            cache_mgr.save()

    # 7. Print Final Summary Table
    token_cost = llm_runner.total_cost_usd if llm_runner else 0.0
    _print_summary_table(
        files_scanned=files_scanned,
        deterministic_edits=deterministic_edits,
        llm_functions=llm_functions,
        todos_marked=todos_marked,
        token_cost=token_cost,
    )


def _print_summary_table(
    files_scanned: int,
    deterministic_edits: int,
    llm_functions: int,
    todos_marked: int,
    token_cost: float,
) -> None:
    """Print the final migration summary statistics table."""
    table = Table(title="RHP Migration Summary", box=box.ROUNDED)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold green")

    table.add_row("Files scanned", str(files_scanned))
    table.add_row("Deterministic edits applied", str(deterministic_edits))
    table.add_row("LLM-assisted functions", str(llm_functions))
    table.add_row("Units marked // RHP-TODO", str(todos_marked))
    table.add_row("Estimated token cost", f"${token_cost:.4f}")

    console.print(table)


@app.command(name="benchmark")
def benchmark(
    corpus: Path = typer.Option(
        Path("./eval/fixtures"),
        "--corpus",
        help="Path to evaluation corpus directory containing fixture projects.",
    ),
    format: str = typer.Option(
        "markdown",
        "--format",
        help="Output format: 'markdown', 'json', or 'table'.",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional file path to write benchmark results.",
    ),
    ai: bool = typer.Option(
        True,
        "--ai/--no-ai",
        help="Enable or disable LLM stage for benchmark.",
    ),
    model: str = typer.Option(
        "gemini-2.5-pro",
        "--model",
        help="LLM model name.",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        help="Maximum compiler error retry attempts.",
    ),
    in_place: bool = typer.Option(
        False,
        "--in-place",
        help="Run migration directly in-place on fixtures instead of isolated copy.",
    ),
) -> None:
    """Run Layer 8 evaluation harness across sample codebases to measure migration quality."""
    corpus_path = corpus.resolve()
    if not corpus_path.exists():
        console.print(f"[bold red]Error:[/bold red] Corpus directory does not exist: {corpus_path}")
        raise typer.Exit(code=1)

    runner = BenchmarkRunner(
        ai=ai,
        model=model,
        max_retries=max_retries,
        isolated=not in_place,
    )

    with console.status(f"[cyan]Running benchmark on corpus: {corpus_path}...[/cyan]"):
        suite_result = runner.run_corpus(corpus_path)

    # Format output
    norm_format = format.lower().strip()
    if norm_format == "json":
        output_text = suite_result.to_json(indent=2)
        console.print(output_text)
    elif norm_format == "table":
        console.print(suite_result.to_rich_table())
        output_text = suite_result.to_markdown()
    else:  # markdown default
        output_text = suite_result.to_markdown()
        console.print(output_text)

    # Write output to file if requested
    if output:
        out_path = output.resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        content_to_write = suite_result.to_json(indent=2) if norm_format == "json" else output_text
        out_path.write_text(content_to_write, encoding="utf-8")
        console.print(f"[green]Saved benchmark results to:[/green] [cyan]{out_path}[/cyan]")


@app.command(name="approve-context")
def approve_context(
    base_dir: Path = typer.Option(
        Path("."),
        "--dir",
        help="Base directory of project containing .rhp/feedback/",
    ),
    target_context: Path = typer.Option(
        Path("rhp_migrate/rules/context.md"),
        "--target",
        help="Target context markdown file to merge approved blocks into",
    ),
) -> None:
    """Review and merge proposed learning blocks from .rhp/feedback/pending_context.md into the repository context."""
    dispatcher = FeedbackDispatcher(base_dir=base_dir)
    pending = dispatcher.load_pending_contexts()

    if not pending:
        console.print("[yellow]No pending context proposals found to approve.[/yellow]")
        return

    console.print(f"[bold cyan]Found {len(pending)} pending context proposals:[/bold cyan]")
    for idx, p in enumerate(pending, 1):
        console.print(f"  {idx}. [green]{p['file']}[/green] [dim](ID: {p['id']}, PHP {p['php']})[/dim]")

    count = dispatcher.approve_all_pending(target_context)
    console.print(
        f"[bold green]Successfully approved and merged {count} proposals into:[/bold green] [cyan]{target_context}[/cyan]"
    )


@app.command(name="audit-deterministic")
def audit_deterministic(
    path: Path = typer.Argument(
        ...,
        help="Path to PHP file or directory to audit deterministic output syntax.",
    ),
    php_version: Optional[str] = typer.Option(
        None,
        "--php-version",
        help="Override source PHP version (e.g. '8' or '7').",
    ),
    record_report: bool = typer.Option(
        True,
        "--record/--no-record",
        help="Save audit report and context update suggestions into .rhp/reports/.",
    ),
) -> None:
    """Audit deterministic migration output on a file, verify syntax validity, and record mistakes."""
    target_path = path.resolve()
    if not target_path.exists():
        console.print(f"[bold red]Error:[/bold red] Target path does not exist: {target_path}")
        raise typer.Exit(code=1)

    targets = [target_path] if target_path.is_file() else [f for f in target_path.rglob("*.php") if f.is_file()]
    if not targets:
        console.print(f"[yellow]No PHP files found in {target_path}.[/yellow]")
        return

    rhp_bin = find_rhp_binary()
    detected_php = detect_php_version(target_path)
    eff_major = int(php_version[0]) if php_version and php_version[0].isdigit() else detected_php.major
    validator = DeterministicValidator()
    base_dir = target_path if target_path.is_dir() else target_path.parent

    total_files = len(targets)
    invalid_files = 0
    total_errors = 0

    for file_p in targets:
        original = file_p.read_text(encoding="utf-8")
        current = original

        # Apply deterministic passes
        if rhp_bin:
            try:
                rep = run_migrate(file_p.parent, file_path=file_p, dry_run=True, php_version=eff_major, bin_path=rhp_bin)
            except Exception:
                pass
        fb = apply_fallback_rules(current)
        if fb.modified:
            current = fb.transformed_code

        record = validator.validate(
            before_code=original,
            after_code=current,
            file_path=file_p,
            bin_path=rhp_bin,
        )

        if not record.is_valid:
            invalid_files += 1
            total_errors += len(record.errors)
            saved_json = validator.record_report(record, base_dir=base_dir) if record_report else None
            console.print(
                Panel.fit(
                    f"[bold red]Invalid Deterministic Syntax:[/bold red] {file_p.name}\n"
                    f"[yellow]What is invalid:[/yellow] {record.what_is_invalid}\n"
                    f"[dim]Errors: {len(record.errors)} | Report: {saved_json}[/dim]",
                    title="Deterministic Audit Failure",
                    border_style="red",
                )
            )
            # Print Markdown report summary
            console.print(record.format_markdown_report())
        else:
            console.print(f"[green]✔ Valid Syntax:[/green] {file_p.name}")

    status_style = "bold green" if invalid_files == 0 else "bold red"
    console.print(
        f"\n[bold cyan]Audit Complete:[/bold cyan] {total_files} files scanned, "
        f"[{status_style}]{invalid_files} files invalid[/{status_style}], "
        f"{total_errors} total syntax errors detected."
    )



@app.command(name="server")
def start_server_command(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Bind host address"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port number"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable server auto-reload"),
) -> None:
    """Launch the rhp-migrate-ai FastAPI backend server."""
    from rhp_migrate.server.main import run

    console.print(f"[bold green]Starting rhp-migrate-ai API server on[/bold green] [cyan]http://{host}:{port}[/cyan]...")
    run(host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()


