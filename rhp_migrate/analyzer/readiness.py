"""Readiness scoring and migration analysis reporting for PHP files and projects."""

from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from rhp_migrate.analyzer.dependency_graph import ProjectDependencyGraph
from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.analyzer.symbols import SymbolExtractor, SymbolTable
from rhp_migrate.analyzer.unsupported_scanner import (
    UnsupportedFeature,
    UnsupportedFinding,
    UnsupportedRuleMeta,
    UnsupportedScanner,
)


def calculate_readiness_score(unsupported_count: int, untyped_param_count: int) -> float:
    """Calculate the migration readiness percentage for a file or project.

    Formula:
        readiness_percentage = 1.0 - (unsupported_count * 0.15 + untyped_param_count * 0.05)
    Clamped to range [0.0, 1.0] and rounded to 4 decimal places.

    Args:
        unsupported_count: Number of forbidden RHP features found.
        untyped_param_count: Number of untyped function/method parameters found.

    Returns:
        Readiness score as a float between 0.0 and 1.0.
    """
    raw_score = 1.0 - (unsupported_count * 0.15 + untyped_param_count * 0.05)
    return round(max(0.0, min(1.0, raw_score)), 4)


class DiagnosticSeverity(str, Enum):
    """Severity levels for analyzer diagnostics."""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class Diagnostic(BaseModel):
    """Per-file diagnostic entry indicating an issue, unsupported feature, or migration tip."""
    model_config = ConfigDict(extra="forbid")

    file_path: Path = Field(..., description="Path to file where diagnostic was identified")
    line: int = Field(..., description="1-indexed line number")
    column: int = Field(..., description="1-indexed column number")
    severity: DiagnosticSeverity = Field(DiagnosticSeverity.ERROR, description="Diagnostic severity")
    rule_id: Optional[str] = Field(None, description="Migration rule ID if applicable (e.g. RHP-UNSUP-001)")
    message: str = Field(..., description="Human-readable diagnostic description")
    code_snippet: Optional[str] = Field(None, description="Relevant source code line or snippet")


class FileReport(BaseModel):
    """Migration readiness report for a single PHP file."""
    model_config = ConfigDict(extra="forbid")

    file_path: Path = Field(..., description="Path to the analyzed file")
    readiness_percentage: float = Field(..., description="Readiness score from 0.0 to 1.0")
    unsupported_count: int = Field(..., description="Count of unsupported features in this file")
    untyped_param_count: int = Field(..., description="Count of untyped parameters in this file")
    unsupported_findings: list[UnsupportedFinding] = Field(
        default_factory=list, description="Detailed list of unsupported feature findings"
    )
    classes: list[str] = Field(default_factory=list, description="Class FQCNs declared in this file")
    functions: list[str] = Field(default_factory=list, description="Function names declared in this file")
    diagnostics: list[Diagnostic] = Field(default_factory=list, description="Combined diagnostics for this file")

    @property
    def is_migration_ready(self) -> bool:
        """True if file has no unsupported features and high readiness score."""
        return self.unsupported_count == 0 and self.readiness_percentage >= 0.8


class AnalysisReport(BaseModel):
    """Complete migration readiness report across project files."""
    model_config = ConfigDict(extra="forbid")

    project_path: Optional[Path] = Field(None, description="Root directory of the analyzed project")
    file_reports: dict[str, FileReport] = Field(
        default_factory=dict, description="File reports keyed by file path string"
    )
    migration_order: list[Path] = Field(
        default_factory=list, description="Topological file migration order (leaves first)"
    )
    total_files: int = Field(0, description="Total number of analyzed PHP files")
    overall_readiness: float = Field(1.0, description="Average readiness score across all analyzed files")
    total_unsupported_count: int = Field(0, description="Aggregate unsupported features count")
    total_untyped_param_count: int = Field(0, description="Aggregate untyped parameters count")
    unsupported_rule_counts: dict[str, int] = Field(
        default_factory=dict, description="Occurrences grouped by rule ID"
    )
    unsupported_rule_references: dict[str, str] = Field(
        default_factory=dict, description="Rule references mapping rule_id -> title and description"
    )

    def get_file_report(self, file_path: Union[str, Path]) -> Optional[FileReport]:
        """Retrieve FileReport by Path or path string."""
        path_str = str(Path(file_path).resolve())
        return self.file_reports.get(path_str) or self.file_reports.get(str(file_path))

    def format_text(self) -> str:
        """Format analysis report as a clean human-readable text summary."""
        lines = [
            "============================================================",
            "                RHP MIGRATION READINESS REPORT               ",
            "============================================================",
            f"Total Files Analyzed : {self.total_files}",
            f"Overall Readiness    : {self.overall_readiness * 100:.1f}%",
            f"Unsupported Features : {self.total_unsupported_count}",
            f"Untyped Parameters   : {self.total_untyped_param_count}",
            "------------------------------------------------------------",
        ]

        if self.unsupported_rule_counts:
            lines.append("Unsupported Rules Detected:")
            for rule_id, count in sorted(self.unsupported_rule_counts.items()):
                ref_desc = self.unsupported_rule_references.get(rule_id, "")
                lines.append(f"  - [{rule_id}] {ref_desc}: {count} occurrence(s)")
            lines.append("------------------------------------------------------------")

        if self.migration_order:
            lines.append("Recommended Migration Order (Leaves / Foundations First):")
            for idx, p in enumerate(self.migration_order, 1):
                fr = self.get_file_report(p)
                score_str = f"({fr.readiness_percentage * 100:.0f}% ready)" if fr else ""
                lines.append(f"  {idx}. {p.name} {score_str}")
            lines.append("------------------------------------------------------------")

        lines.append("Per-File Diagnostics Summary:")
        for file_str, fr in sorted(self.file_reports.items()):
            lines.append(
                f"  * {fr.file_path.name}: {fr.readiness_percentage * 100:.1f}% ready "
                f"({fr.unsupported_count} unsupported, {fr.untyped_param_count} untyped params)"
            )
            for diag in fr.diagnostics:
                rule_tag = f"[{diag.rule_id}] " if diag.rule_id else ""
                lines.append(f"      L{diag.line}:{diag.column} [{diag.severity.value}] {rule_tag}{diag.message}")

        lines.append("============================================================")
        return "\n".join(lines)


class ReadinessAnalyzer:
    """Performs read-only AST parsing, symbol extraction, and readiness analysis."""

    def __init__(
        self,
        parser: Optional[PHPParser] = None,
        scanner: Optional[UnsupportedScanner] = None,
        extractor: Optional[SymbolExtractor] = None,
    ) -> None:
        self.parser = parser or PHPParser()
        self.scanner = scanner or UnsupportedScanner(parser=self.parser)
        self.extractor = extractor or SymbolExtractor(parser=self.parser)

    def analyze_file(
        self,
        file_path: Union[str, Path],
        source: Optional[Union[str, bytes]] = None,
    ) -> FileReport:
        """Analyze a single PHP file without modifying it.

        Args:
            file_path: Path to the PHP file.
            source: Optional source content. If omitted, reads from file_path.

        Returns:
            FileReport containing readiness score, diagnostics, and unsupported findings.
        """
        path = Path(file_path).resolve()

        if source is not None:
            symbols = self.extractor.extract_source(source, file_path=path)
            findings = self.scanner.scan_source(source, file_path=path)
        else:
            symbols = self.extractor.extract_file(path)
            findings = self.scanner.scan_file(path)

        unsupported_count = len(findings)
        untyped_param_count = symbols.total_untyped_params
        readiness_percentage = calculate_readiness_score(unsupported_count, untyped_param_count)

        diagnostics: list[Diagnostic] = []

        # 1. Diagnostics for unsupported features (ERROR)
        for finding in findings:
            diagnostics.append(
                Diagnostic(
                    file_path=path,
                    line=finding.line,
                    column=finding.column,
                    severity=DiagnosticSeverity.ERROR,
                    rule_id=finding.rule_id,
                    message=finding.message,
                    code_snippet=finding.node_text,
                )
            )

        # 2. Diagnostics for untyped parameters (WARNING)
        # Unique classes
        unique_classes = {c.fqcn: c for c in symbols.classes.values()}.values()
        for c in unique_classes:
            for m in c.methods:
                for p in m.parameters:
                    if p.is_untyped:
                        diagnostics.append(
                            Diagnostic(
                                file_path=path,
                                line=m.line,
                                column=1,
                                severity=DiagnosticSeverity.WARNING,
                                rule_id="RHP-PARAM-UNTYPED",
                                message=f"Parameter '{p.name}' in method '{c.name}::{m.name}' lacks a type hint.",
                                code_snippet=f"{m.visibility} function {m.name}(..., {p.name}, ...)",
                            )
                        )

        for f in symbols.functions.values():
            for p in f.parameters:
                if p.is_untyped:
                    diagnostics.append(
                        Diagnostic(
                            file_path=path,
                            line=f.line,
                            column=1,
                            severity=DiagnosticSeverity.WARNING,
                            rule_id="RHP-PARAM-UNTYPED",
                            message=f"Parameter '{p.name}' in function '{f.name}' lacks a type hint.",
                            code_snippet=f"function {f.name}(..., {p.name}, ...)",
                        )
                    )

        # Sort diagnostics by line and column
        diagnostics.sort(key=lambda d: (d.line, d.column))

        class_fqcns = sorted({c.fqcn for c in symbols.classes.values()})
        function_names = sorted({f.name for f in symbols.functions.values()})

        return FileReport(
            file_path=path,
            readiness_percentage=readiness_percentage,
            unsupported_count=unsupported_count,
            untyped_param_count=untyped_param_count,
            unsupported_findings=findings,
            classes=class_fqcns,
            functions=function_names,
            diagnostics=diagnostics,
        )

    def analyze_project(
        self,
        files_or_directory: Union[str, Path, Iterable[Union[str, Path]]],
        pattern: str = "**/*.php",
    ) -> AnalysisReport:
        """Analyze a collection of PHP files or a directory.

        Computes topological dependency migration ordering and per-file readiness scores.

        Args:
            files_or_directory: Path to a directory, or iterable of file paths.
            pattern: Glob pattern when files_or_directory is a directory.

        Returns:
            AnalysisReport containing aggregate statistics, ordering, and diagnostics.
        """
        dep_graph = ProjectDependencyGraph(extractor=self.extractor)
        file_reports: dict[str, FileReport] = {}
        paths_to_analyze: list[Path] = []

        if isinstance(files_or_directory, (str, Path)):
            root = Path(files_or_directory).resolve()
            if root.is_dir():
                for f in sorted(root.glob(pattern)):
                    if f.is_file():
                        paths_to_analyze.append(f)
            elif root.is_file():
                paths_to_analyze.append(root)
            project_path = root if root.is_dir() else root.parent
        else:
            paths_to_analyze = [Path(p).resolve() for p in files_or_directory]
            project_path = paths_to_analyze[0].parent if paths_to_analyze else None

        for path in paths_to_analyze:
            dep_graph.add_file(path)
            report = self.analyze_file(path)
            file_reports[str(path)] = report

        migration_order = dep_graph.get_migration_order()

        total_files = len(file_reports)
        total_unsupported = sum(r.unsupported_count for r in file_reports.values())
        total_untyped = sum(r.untyped_param_count for r in file_reports.values())
        overall_readiness = (
            round(sum(r.readiness_percentage for r in file_reports.values()) / total_files, 4)
            if total_files > 0
            else 1.0
        )

        unsupported_rule_counts: dict[str, int] = {}
        unsupported_rule_references: dict[str, str] = {}
        for r in file_reports.values():
            for finding in r.unsupported_findings:
                unsupported_rule_counts[finding.rule_id] = (
                    unsupported_rule_counts.get(finding.rule_id, 0) + 1
                )
                if finding.rule_id not in unsupported_rule_references:
                    unsupported_rule_references[finding.rule_id] = finding.title

        return AnalysisReport(
            project_path=project_path,
            file_reports=file_reports,
            migration_order=migration_order,
            total_files=total_files,
            overall_readiness=overall_readiness,
            total_unsupported_count=total_unsupported,
            total_untyped_param_count=total_untyped,
            unsupported_rule_counts=unsupported_rule_counts,
            unsupported_rule_references=unsupported_rule_references,
        )
