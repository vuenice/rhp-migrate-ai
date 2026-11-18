"""Centralized telemetry and continuous-improvement feedback dispatcher."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Optional, Union
import urllib.error
import urllib.request

from rhp_migrate.ai.evaluation_models import ContextMdProposal, MigrationCriticReport


DEFAULT_CENTRAL_URL_ENV = "RHP_CENTRAL_URL"
DEFAULT_CENTRAL_KEY_ENV = "RHP_CENTRAL_API_KEY"


class FeedbackDispatcher:
    """Manages local storage and remote dispatch of migration evaluation reports."""

    def __init__(
        self,
        base_dir: Union[str, Path] = Path("."),
        central_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.central_url = (
            central_url or os.environ.get(DEFAULT_CENTRAL_URL_ENV) or ""
        ).strip()
        self.api_key = (
            api_key or os.environ.get(DEFAULT_CENTRAL_KEY_ENV) or ""
        ).strip()

        self.feedback_dir = self.base_dir / ".rhp" / "feedback"
        self.critiques_dir = self.feedback_dir / "critiques"
        self.jsonl_path = self.feedback_dir / "feedback.jsonl"
        self.pending_context_path = self.feedback_dir / "pending_context.md"

    def ensure_directories(self) -> None:
        """Create necessary directories if they do not exist."""
        self.critiques_dir.mkdir(parents=True, exist_ok=True)

    def save_local(self, report: MigrationCriticReport) -> Path:
        """Persist report locally into .rhp/feedback/ for offline review."""
        self.ensure_directories()

        # Generate unique filename hash
        raw_id = f"{report.file_path}:{report.timestamp}"
        digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
        filename = f"{digest}.json"
        dest_file = self.critiques_dir / filename

        # 1. Write individual JSON critique
        dest_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

        # 2. Append to JSONL aggregate
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(report.model_dump_json() + "\n")

        # 3. Append context proposal if present
        if report.context_proposal:
            self._append_pending_context(report, digest)

        return dest_file

    def _append_pending_context(
        self,
        report: MigrationCriticReport,
        report_id: str,
    ) -> None:
        """Append a proposed context markdown block to pending_context.md."""
        proposal = report.context_proposal
        if not proposal:
            return

        header = (
            f"\n<!-- PENDING-CONTEXT-START id={report_id} "
            f"file={report.file_path} php={report.php_version} -->\n"
        )
        content = (
            f"### [PROPOSAL] {proposal.title}\n\n"
            f"- **Category:** `{proposal.category}`\n"
            f"- **Origin File:** `{report.file_path}` (PHP {report.php_version})\n"
            f"- **Date:** {report.timestamp}\n\n"
            f"{proposal.markdown_content.strip()}\n"
        )
        footer = f"\n<!-- PENDING-CONTEXT-END id={report_id} -->\n---\n"

        with open(self.pending_context_path, "a", encoding="utf-8") as f:
            f.write(header + content + footer)

    def send_remote(
        self,
        report: MigrationCriticReport,
        timeout: float = 5.0,
    ) -> tuple[bool, Optional[str]]:
        """Dispatch report payload to the centralized remote endpoint (e.g. VPS or Firebase).

        Returns:
            Tuple of (success: bool, response_or_error_message: Optional[str])
        """
        if not self.central_url:
            return False, "No central_url configured"

        payload = report.model_dump()
        payload_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url=self.central_url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "rhp-migrate-ai/0.1.0",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                resp_text = response.read().decode("utf-8", errors="replace")
                return True, resp_text
        except urllib.error.HTTPError as exc:
            err_msg = f"HTTP {exc.code}: {exc.reason}"
            return False, err_msg
        except Exception as exc:
            return False, str(exc)

    def dispatch(
        self,
        report: MigrationCriticReport,
    ) -> dict[str, Any]:
        """Save report locally and attempt remote transmission if configured."""
        local_path = self.save_local(report)
        remote_sent, remote_msg = False, None

        if self.central_url:
            remote_sent, remote_msg = self.send_remote(report)

        return {
            "local_path": str(local_path),
            "remote_sent": remote_sent,
            "remote_message": remote_msg,
            "has_proposal": report.context_proposal is not None,
        }

    def load_pending_contexts(self) -> list[dict[str, str]]:
        """Parse all pending context proposals from pending_context.md."""
        if not self.pending_context_path.is_file():
            return []

        text = self.pending_context_path.read_text(encoding="utf-8")
        pattern = re.compile(
            r"<!-- PENDING-CONTEXT-START id=(\S+) file=(\S+) php=(\S+) -->"
            r"(.*?)"
            r"<!-- PENDING-CONTEXT-END id=\1 -->",
            re.DOTALL,
        )
        proposals: list[dict[str, str]] = []
        for m in pattern.finditer(text):
            proposals.append({
                "id": m.group(1),
                "file": m.group(2),
                "php": m.group(3),
                "content": m.group(4).strip(),
            })
        return proposals

    def approve_all_pending(self, target_context_path: Union[str, Path]) -> int:
        """Approve all pending context blocks and append them to target context.md."""
        target = Path(target_context_path).resolve()
        proposals = self.load_pending_contexts()
        if not proposals:
            return 0

        target.parent.mkdir(parents=True, exist_ok=True)
        append_text = "\n\n" + "\n\n---\n\n".join(p["content"] for p in proposals) + "\n"

        with open(target, "a", encoding="utf-8") as f:
            f.write(append_text)

        # Clear pending file
        self.pending_context_path.write_text("", encoding="utf-8")
        return len(proposals)
