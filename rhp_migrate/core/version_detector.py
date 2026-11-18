"""PHP target version detector from composer.json, Dockerfile, and CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional, Union


@dataclass
class PHPVersionInfo:
    """Encapsulates detected PHP version metadata."""

    version: str
    raw: str
    source: str
    major: int
    minor: int

    @property
    def is_php8_plus(self) -> bool:
        return self.major >= 8


def parse_version_numbers(version_str: str) -> Optional[tuple[int, int]]:
    """Extract (major, minor) integers from a version string like '^8.2', '8.1.*', '>=7.4'."""
    match = re.search(r"(\d+)\.(\d+)", version_str)
    if match:
        return int(match.group(1)), int(match.group(2))
    single_match = re.search(r"(\d+)", version_str)
    if single_match:
        return int(single_match.group(1)), 0
    return None


def detect_php_version(target_path: Union[str, Path]) -> PHPVersionInfo:
    """Detect the PHP target version using a priority cascade:

    1. composer.json (require.php or config.platform.php)
    2. Dockerfile / docker-compose.yml
    3. System CLI `php -v`
    4. Fallback default (PHP 8.2)

    Args:
        target_path: File or directory path of the codebase to inspect.

    Returns:
        PHPVersionInfo with resolved version and origin source.
    """
    path = Path(target_path).resolve()
    search_dirs: list[Path] = []

    start_dir = path if path.is_dir() else path.parent
    curr: Optional[Path] = start_dir
    depth = 0
    while curr is not None and depth < 5:
        search_dirs.append(curr)
        if (curr / ".git").exists() or curr.parent == curr:
            break
        curr = curr.parent
        depth += 1

    # 1. Inspect composer.json
    for d in search_dirs:
        composer_file = d / "composer.json"
        if composer_file.is_file():
            try:
                data = json.loads(composer_file.read_text(encoding="utf-8"))
                # Check config.platform.php first (explicit deployment target)
                platform_php = data.get("config", {}).get("platform", {}).get("php")
                if platform_php and isinstance(platform_php, str):
                    nums = parse_version_numbers(platform_php)
                    if nums:
                        return PHPVersionInfo(
                            version=f"{nums[0]}.{nums[1]}",
                            raw=platform_php,
                            source="composer.json (config.platform)",
                            major=nums[0],
                            minor=nums[1],
                        )

                # Check require.php
                require_php = data.get("require", {}).get("php")
                if require_php and isinstance(require_php, str):
                    nums = parse_version_numbers(require_php)
                    if nums:
                        return PHPVersionInfo(
                            version=f"{nums[0]}.{nums[1]}",
                            raw=require_php,
                            source="composer.json",
                            major=nums[0],
                            minor=nums[1],
                        )
            except Exception:
                pass

    # 2. Inspect Dockerfile and docker-compose
    for d in search_dirs:
        docker_candidates = [
            d / "Dockerfile",
            d / "docker" / "Dockerfile",
            d / "docker-compose.yml",
            d / "docker-compose.yaml",
        ]
        for df in docker_candidates:
            if df.is_file():
                try:
                    content = df.read_text(encoding="utf-8", errors="ignore")
                    match = re.search(r"php:(\d+)\.(\d+)", content, re.IGNORECASE)
                    if match:
                        major, minor = int(match.group(1)), int(match.group(2))
                        return PHPVersionInfo(
                            version=f"{major}.{minor}",
                            raw=match.group(0),
                            source=df.name,
                            major=major,
                            minor=minor,
                        )
                except Exception:
                    pass

    # 3. Inspect System CLI `php -v`
    php_bin = shutil.which("php")
    if php_bin:
        try:
            res = subprocess.run(
                [php_bin, "-v"],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                match = re.search(r"PHP\s+(\d+)\.(\d+)", res.stdout)
                if match:
                    major, minor = int(match.group(1)), int(match.group(2))
                    return PHPVersionInfo(
                        version=f"{major}.{minor}",
                        raw=res.stdout.splitlines()[0],
                        source="cli (php -v)",
                        major=major,
                        minor=minor,
                    )
        except Exception:
            pass

    # 4. Fallback default to PHP 8.2
    return PHPVersionInfo(
        version="8.2",
        raw="default",
        source="fallback",
        major=8,
        minor=2,
    )
