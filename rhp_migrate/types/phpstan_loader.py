"""Loader for PHPStan and Psalm JSON analysis reports to extract high-confidence types."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Optional, Union
from pydantic import BaseModel, Field

from rhp_migrate.types.docblock import map_php_type_to_rhp


class StaticAnalysisType(BaseModel):
    """Represents a type deduced by static analysis tools (PHPStan or Psalm)."""

    file_path: str = Field(..., description="Normalized file path where the type applies")
    symbol_name: Optional[str] = Field(None, description="Class, method, property, or variable name")
    line: Optional[int] = Field(None, description="1-indexed line number where the issue or type applies")
    inferred_type: str = Field(..., description="Raw type string deduced by static analysis")
    rhp_type: Optional[str] = Field(None, description="Mapped RHP type string")
    kind: str = Field("unknown", description="Type role: 'return', 'param', 'property', 'dump', 'variable'")
    confidence: float = Field(0.9, description="Confidence score (defaults to 0.9 for static analysis)")
    raw_message: Optional[str] = Field(None, description="Original error or diagnostic message")


class PHPStanLoader:
    """Parses PHPStan (`--error-format=json`) and Psalm JSON reports to extract high-confidence types."""

    # Regex patterns for PHPStan diagnostic messages
    _PHPSTAN_PATTERNS = [
        # Dumped type: int
        (
            re.compile(r"^Dumped type:\s*(?P<type>.+)$", re.IGNORECASE),
            "dump",
            lambda m: None,
            lambda m: m.group("type").strip(),
        ),
        # Method Foo::bar() should return int but returns string.
        (
            re.compile(
                r"^Method\s+(?P<method>[^\s(]+)\(\)\s+should return\s+(?P<expected>[^\s]+)\s+but returns\s+(?P<actual>[^\s.]+)",
                re.IGNORECASE,
            ),
            "return",
            lambda m: m.group("method"),
            lambda m: m.group("actual").strip(),
        ),
        # Method Foo::bar() return type has no value type specified in iterable type array<string, int>.
        (
            re.compile(
                r"^Method\s+(?P<method>[^\s(]+)\(\)\s+return type has no value type specified in iterable type\s+(?P<type>[^\s.]+)",
                re.IGNORECASE,
            ),
            "return",
            lambda m: m.group("method"),
            lambda m: m.group("type").strip(),
        ),
        # Parameter #1 $id (int) of method User::find() does not accept string.
        (
            re.compile(
                r"^Parameter\s+#\d+\s+\$(?P<param>[a-zA-Z0-9_]+)\s+\((?P<type>[^)]+)\)\s+of method\s+(?P<method>[^\s(]+)",
                re.IGNORECASE,
            ),
            "param",
            lambda m: f"{m.group('method')}::${m.group('param')}",
            lambda m: m.group("type").strip(),
        ),
        # Parameter $id of method User::find() has invalid type string.
        (
            re.compile(
                r"^Parameter\s+\$(?P<param>[a-zA-Z0-9_]+)\s+of method\s+(?P<method>[^\s(]+)\(\)\s+has invalid type\s+(?P<type>[^\s.]+)",
                re.IGNORECASE,
            ),
            "param",
            lambda m: f"{m.group('method')}::${m.group('param')}",
            lambda m: m.group("type").strip(),
        ),
        # Parameter $id of function find() has invalid type string.
        (
            re.compile(
                r"^Parameter\s+\$(?P<param>[a-zA-Z0-9_]+)\s+of function\s+(?P<func>[^\s(]+)\(\)\s+has invalid type\s+(?P<type>[^\s.]+)",
                re.IGNORECASE,
            ),
            "param",
            lambda m: f"{m.group('func')}::${m.group('param')}",
            lambda m: m.group("type").strip(),
        ),
        # Parameter $options of method Foo::bar() has no value type specified in iterable type array<string, mixed>.
        (
            re.compile(
                r"^Parameter\s+\$(?P<param>[a-zA-Z0-9_]+)\s+of method\s+(?P<method>[^\s(]+)\(\)\s+has no value type specified in iterable type\s+(?P<type>[^\s.]+)",
                re.IGNORECASE,
            ),
            "param",
            lambda m: f"{m.group('method')}::${m.group('param')}",
            lambda m: m.group("type").strip(),
        ),
        # Property Foo::$bar (string) does not accept default value null.
        (
            re.compile(
                r"^Property\s+(?P<prop>[^\s]+)\s+\((?P<type>[^)]+)\)",
                re.IGNORECASE,
            ),
            "property",
            lambda m: m.group("prop"),
            lambda m: m.group("type").strip(),
        ),
        # Property Foo::$bar with generic type array<string, int> ...
        (
            re.compile(
                r"^Property\s+(?P<prop>[^\s]+)\s+with generic type\s+(?P<type>[^\s]+)",
                re.IGNORECASE,
            ),
            "property",
            lambda m: m.group("prop"),
            lambda m: m.group("type").strip(),
        ),
    ]

    # Regex patterns for Psalm diagnostic messages
    _PSALM_PATTERNS = [
        # Method Foo::bar should return int but saw string
        (
            re.compile(
                r"^Method\s+(?P<method>[^\s:]+(?:::[^\s]+)?)\s+should return\s+(?P<expected>[^\s]+)\s+but saw\s+(?P<actual>[^\s.]+)",
                re.IGNORECASE,
            ),
            "return",
            lambda m: m.group("method"),
            lambda m: m.group("actual").strip(),
        ),
        # Argument 1 of Foo::bar expects int, string provided
        (
            re.compile(
                r"^Argument\s+\d+\s+of\s+(?P<func>[^\s]+)\s+expects\s+(?P<expected>[^,]+),\s*(?P<actual>[^\s]+)\s+provided",
                re.IGNORECASE,
            ),
            "param",
            lambda m: m.group("func"),
            lambda m: m.group("actual").strip(),
        ),
        # Property Foo::$bar with type string ...
        (
            re.compile(
                r"^Property\s+(?P<prop>[^\s]+)\s+with type\s+(?P<type>[^\s]+)",
                re.IGNORECASE,
            ),
            "property",
            lambda m: m.group("prop"),
            lambda m: m.group("type").strip(),
        ),
    ]

    def __init__(self) -> None:
        self.all_entries: list[StaticAnalysisType] = []

    def load_file(self, file_path: Union[str, Path]) -> list[StaticAnalysisType]:
        """Read a JSON file containing PHPStan or Psalm output and extract deduced types."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.load_json(content)

    def load_json(self, data: Union[str, dict, list]) -> list[StaticAnalysisType]:
        """Parse JSON report string, dict, or list into deduced StaticAnalysisType objects."""
        if isinstance(data, str):
            parsed = json.loads(data)
        else:
            parsed = data

        extracted: list[StaticAnalysisType] = []

        if isinstance(parsed, list):
            # Could be a list of Psalm issues or a list of direct type exports
            for item in parsed:
                if isinstance(item, dict):
                    entry = self._parse_single_issue_or_export(item)
                    if entry:
                        extracted.append(entry)

        elif isinstance(parsed, dict):
            # Direct type export at root: {"types": [...]}
            if "types" in parsed and isinstance(parsed["types"], list):
                for item in parsed["types"]:
                    if isinstance(item, dict):
                        entry = self._parse_direct_type_dict(item, default_file="")
                        if entry:
                            extracted.append(entry)

            # PHPStan standard structure: {"totals": {...}, "files": {...}}
            elif "files" in parsed and isinstance(parsed["files"], dict):
                for file_key, file_data in parsed["files"].items():
                    if isinstance(file_data, dict):
                        # Standard PHPStan messages: {"messages": [...]}
                        messages = file_data.get("messages", [])
                        for msg_obj in messages:
                            if isinstance(msg_obj, dict):
                                entry = self._parse_phpstan_message(file_key, msg_obj)
                                if entry:
                                    extracted.append(entry)

                        # Direct type map inside file object: {"types": {...} or [...]}
                        if "types" in file_data:
                            types_val = file_data["types"]
                            if isinstance(types_val, dict):
                                for sym, t_val in types_val.items():
                                    rhp_t = map_php_type_to_rhp(str(t_val))
                                    extracted.append(
                                        StaticAnalysisType(
                                            file_path=self._normalize_path(file_key),
                                            symbol_name=sym,
                                            inferred_type=str(t_val),
                                            rhp_type=rhp_t,
                                            kind="unknown",
                                            confidence=0.9,
                                        )
                                    )
                            elif isinstance(types_val, list):
                                for item in types_val:
                                    if isinstance(item, dict):
                                        entry = self._parse_direct_type_dict(item, default_file=file_key)
                                        if entry:
                                            extracted.append(entry)

            # Psalm standard structure with error/warning arrays: {"error": [...], "warning": [...]}
            elif any(k in parsed for k in ("error", "warning", "deprecation")):
                for category in ("error", "warning", "deprecation"):
                    issues = parsed.get(category, [])
                    if isinstance(issues, list):
                        for item in issues:
                            if isinstance(item, dict):
                                entry = self._parse_psalm_issue(item)
                                if entry:
                                    extracted.append(entry)

        self.all_entries.extend(extracted)
        return extracted

    def get_types_for_file(self, file_path: Union[str, Path]) -> list[StaticAnalysisType]:
        """Retrieve all extracted types matching a given file path."""
        norm = self._normalize_path(str(file_path))
        return [
            e for e in self.all_entries
            if self._paths_match(e.file_path, norm)
        ]

    def get_type_for_symbol(
        self,
        symbol: str,
        file_path: Optional[Union[str, Path]] = None,
    ) -> Optional[StaticAnalysisType]:
        """Find static analysis type by symbol name (e.g. 'User::getName', '$id')."""
        norm_sym = symbol.lstrip("\\")
        norm_file = self._normalize_path(str(file_path)) if file_path else None

        for entry in self.all_entries:
            if norm_file and not self._paths_match(entry.file_path, norm_file):
                continue
            if entry.symbol_name and (
                entry.symbol_name == norm_sym
                or entry.symbol_name.endswith(f"::{norm_sym}")
                or norm_sym.endswith(f"::{entry.symbol_name}")
                or entry.symbol_name == f"${norm_sym.lstrip('$')}"
            ):
                return entry

        return None

    def get_type_at_line(
        self,
        line: int,
        file_path: Optional[Union[str, Path]] = None,
    ) -> Optional[StaticAnalysisType]:
        """Find static analysis type by line number within an optional file."""
        norm_file = self._normalize_path(str(file_path)) if file_path else None

        for entry in self.all_entries:
            if norm_file and not self._paths_match(entry.file_path, norm_file):
                continue
            if entry.line == line:
                return entry

        return None

    def find_type(
        self,
        file_path: Optional[Union[str, Path]] = None,
        symbol_name: Optional[str] = None,
        line: Optional[int] = None,
    ) -> Optional[str]:
        """Find the mapped RHP type for a symbol, file, and/or line.

        Returns:
            Mapped RHP type string if found, or None.
        """
        if symbol_name:
            entry = self.get_type_for_symbol(symbol_name, file_path=file_path)
            if entry and entry.rhp_type:
                return entry.rhp_type
        if line is not None:
            entry = self.get_type_at_line(line, file_path=file_path)
            if entry and entry.rhp_type:
                return entry.rhp_type
        return None

    def _parse_phpstan_message(self, file_path: str, msg_obj: dict[str, Any]) -> Optional[StaticAnalysisType]:
        message = str(msg_obj.get("message", ""))
        line = msg_obj.get("line")
        norm_path = self._normalize_path(file_path)

        for pattern, kind, sym_func, type_func in self._PHPSTAN_PATTERNS:
            match = pattern.search(message)
            if match:
                symbol = sym_func(match)
                raw_type = type_func(match)
                rhp_type = map_php_type_to_rhp(raw_type)
                return StaticAnalysisType(
                    file_path=norm_path,
                    symbol_name=symbol,
                    line=line if isinstance(line, int) else None,
                    inferred_type=raw_type,
                    rhp_type=rhp_type,
                    kind=kind,
                    confidence=0.9,
                    raw_message=message,
                )

        return None

    def _parse_psalm_issue(self, issue_obj: dict[str, Any]) -> Optional[StaticAnalysisType]:
        message = str(issue_obj.get("message", ""))
        file_path = str(issue_obj.get("file_path") or issue_obj.get("file_name") or "")
        line = issue_obj.get("line_from")
        norm_path = self._normalize_path(file_path)

        for pattern, kind, sym_func, type_func in self._PSALM_PATTERNS:
            match = pattern.search(message)
            if match:
                symbol = sym_func(match)
                raw_type = type_func(match)
                rhp_type = map_php_type_to_rhp(raw_type)
                return StaticAnalysisType(
                    file_path=norm_path,
                    symbol_name=symbol,
                    line=line if isinstance(line, int) else None,
                    inferred_type=raw_type,
                    rhp_type=rhp_type,
                    kind=kind,
                    confidence=0.9,
                    raw_message=message,
                )

        return None

    def _parse_single_issue_or_export(self, item: dict[str, Any]) -> Optional[StaticAnalysisType]:
        # If item has 'file' or 'file_path' and 'type' or 'inferred_type'
        if "type" in item or "inferred_type" in item:
            return self._parse_direct_type_dict(item)
        # Otherwise try psalm issue parsing
        return self._parse_psalm_issue(item)

    def _parse_direct_type_dict(
        self,
        item: dict[str, Any],
        default_file: str = "",
    ) -> Optional[StaticAnalysisType]:
        raw_type = str(item.get("type") or item.get("inferred_type") or "")
        if not raw_type:
            return None

        file_path = str(item.get("file") or item.get("file_path") or default_file)
        symbol = item.get("symbol") or item.get("symbol_name") or item.get("name")
        line = item.get("line")
        kind = str(item.get("kind", "unknown"))
        confidence = float(item.get("confidence", 0.9))

        rhp_type = map_php_type_to_rhp(raw_type)
        return StaticAnalysisType(
            file_path=self._normalize_path(file_path),
            symbol_name=str(symbol) if symbol else None,
            line=line if isinstance(line, int) else None,
            inferred_type=raw_type,
            rhp_type=rhp_type,
            kind=kind,
            confidence=confidence,
            raw_message=str(item.get("message")) if "message" in item else None,
        )

    @staticmethod
    def _normalize_path(path_str: str) -> str:
        """Normalize file path for consistent matching across operating systems."""
        if not path_str:
            return ""
        return Path(path_str).as_posix()

    @staticmethod
    def _paths_match(p1: str, p2: str) -> bool:
        """Check if two file paths match (handling relative vs absolute path basenames)."""
        if not p1 or not p2:
            return True
        if p1 == p2:
            return True
        # Check if one is a suffix of the other (e.g. 'src/User.php' vs '/var/www/src/User.php')
        return p1.endswith(p2) or p2.endswith(p1)
