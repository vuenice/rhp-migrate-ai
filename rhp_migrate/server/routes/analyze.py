"""AST readiness and unsupported feature analysis routes."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from typing import Any, Optional
import zipfile

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import ValidationError

from rhp_migrate.analyzer.readiness import AnalysisReport, FileReport, ReadinessAnalyzer
from rhp_migrate.server.models import (
    AnalyzeJsonRequest,
    AnalyzeResponse,
    FileDiagnosticItem,
    FileReportSummary,
)

router = APIRouter()


def _convert_file_report(fr: FileReport, base_dir: Optional[Path] = None) -> FileReportSummary:
    """Convert an internal FileReport to an API FileReportSummary."""
    if base_dir:
        try:
            file_path_str = str(Path(fr.file_path).resolve().relative_to(base_dir.resolve()))
        except ValueError:
            file_path_str = Path(fr.file_path).name
    else:
        file_path_str = Path(fr.file_path).name

    diagnostics = [
        FileDiagnosticItem(
            line=d.line,
            column=d.column,
            severity=d.severity.value if hasattr(d.severity, "value") else str(d.severity),
            rule_id=d.rule_id,
            message=d.message,
            code_snippet=d.code_snippet,
        )
        for d in fr.diagnostics
    ]

    return FileReportSummary(
        file_path=file_path_str,
        readiness_percentage=fr.readiness_percentage,
        unsupported_count=fr.unsupported_count,
        untyped_param_count=fr.untyped_param_count,
        is_migration_ready=fr.is_migration_ready,
        classes=fr.classes,
        functions=fr.functions,
        diagnostics=diagnostics,
    )


def _convert_analysis_report(report: AnalysisReport, base_dir: Optional[Path] = None) -> AnalyzeResponse:
    """Convert an internal AnalysisReport to an API AnalyzeResponse."""
    summaries: dict[str, FileReportSummary] = {}
    for path_str, fr in report.file_reports.items():
        summary = _convert_file_report(fr, base_dir=base_dir)
        summaries[summary.file_path] = summary

    migration_order_rel: list[str] = []
    for p in report.migration_order:
        if base_dir:
            try:
                migration_order_rel.append(str(p.relative_to(base_dir)))
            except ValueError:
                migration_order_rel.append(p.name)
        else:
            migration_order_rel.append(str(p))

    return AnalyzeResponse(
        total_files=report.total_files,
        overall_readiness=report.overall_readiness,
        total_unsupported_count=report.total_unsupported_count,
        total_untyped_param_count=report.total_untyped_param_count,
        unsupported_rule_counts=report.unsupported_rule_counts,
        unsupported_rule_references=report.unsupported_rule_references,
        migration_order=migration_order_rel,
        file_reports=summaries,
        summary_text=report.format_text(),
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_project(request: Request) -> AnalyzeResponse:
    """Analyze a PHP codebase or code snippet for RHP readiness and unsupported features.

    Supports:
    1. application/json:
       - Single code snippet: {"code": "<?php ...", "file_name": "app/Foo.php"}
       - In-memory files: {"files": {"app/A.php": "...", "app/B.php": "..."}}
       - Local path on server: {"project_path": "/path/to/project"}
    2. multipart/form-data:
       - Zip archive upload containing project directory (file=@project.zip)
       - Multiple uploaded PHP files (files=@foo.php, files=@bar.php)
    """
    content_type = request.headers.get("content-type", "").lower()
    analyzer = ReadinessAnalyzer()

    # -----------------------------------------------------------------------
    # Case 1: multipart/form-data (ZIP or multiple uploaded files)
    # -----------------------------------------------------------------------
    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_items = form.getlist("file") + form.getlist("files")

        if not uploaded_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No file or files provided in multipart form-data.",
            )

        with tempfile.TemporaryDirectory(prefix="rhp_analyze_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)

            for item in uploaded_items:
                if not hasattr(item, "read"):
                    continue

                filename = getattr(item, "filename", None) or "upload.php"
                content_bytes = await item.read()

                # If uploaded file is a ZIP archive, unpack it
                if filename.lower().endswith(".zip") or zipfile.is_zipfile(io.BytesIO(content_bytes)):
                    with zipfile.ZipFile(io.BytesIO(content_bytes)) as z:
                        z.extractall(temp_dir)
                else:
                    # Single file or part of multiple files
                    dest = temp_dir / filename
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(content_bytes)

            report = analyzer.analyze_project(temp_dir)
            return _convert_analysis_report(report, base_dir=temp_dir)

    # -----------------------------------------------------------------------
    # Case 2: application/json
    # -----------------------------------------------------------------------
    try:
        body = await request.json()
        req_data = AnalyzeJsonRequest.model_validate(body)
    except (ValidationError, Exception) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid JSON request body: {exc}",
        )

    # 2a. Direct PHP code snippet
    if req_data.code is not None:
        file_name = req_data.file_name or "snippet.php"
        fake_path = Path(file_name)
        fr = analyzer.analyze_file(fake_path, source=req_data.code)

        # Wrap in AnalysisReport
        single_report = AnalysisReport(
            project_path=fake_path.parent,
            file_reports={str(fake_path): fr},
            migration_order=[fake_path],
            total_files=1,
            overall_readiness=fr.readiness_percentage,
            total_unsupported_count=fr.unsupported_count,
            total_untyped_param_count=fr.untyped_param_count,
            unsupported_rule_counts={},
            unsupported_rule_references={},
        )
        return _convert_analysis_report(single_report, base_dir=fake_path.resolve().parent)

    # 2b. In-memory dictionary of files: {"path/file.php": "<?php ..."}
    if req_data.files:
        with tempfile.TemporaryDirectory(prefix="rhp_analyze_mem_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for rel_path, code_str in req_data.files.items():
                dest = temp_dir / rel_path.lstrip("/\\")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(code_str, encoding="utf-8")

            report = analyzer.analyze_project(temp_dir)
            return _convert_analysis_report(report, base_dir=temp_dir)

    # 2c. Filesystem project directory path
    if req_data.project_path:
        target = Path(req_data.project_path).resolve()
        if not target.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project path does not exist: {req_data.project_path}",
            )
        if target.is_file():
            fr = analyzer.analyze_file(target)
            single_report = AnalysisReport(
                project_path=target.parent,
                file_reports={str(target): fr},
                migration_order=[target],
                total_files=1,
                overall_readiness=fr.readiness_percentage,
                total_unsupported_count=fr.unsupported_count,
                total_untyped_param_count=fr.untyped_param_count,
            )
            return _convert_analysis_report(single_report, base_dir=target.parent)

        report = analyzer.analyze_project(target)
        return _convert_analysis_report(report, base_dir=target)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Must provide either 'code', 'files', 'project_path', or upload a file.",
    )
