"""Docblock parser and PHP-to-RHP type mapping utilities."""

from __future__ import annotations

import re
from typing import Optional, Union
from pydantic import BaseModel, Field
import tree_sitter

from rhp_migrate.analyzer.parser import PHPParser


class DocblockParam(BaseModel):
    """Represents a parsed @param tag from a PHPDoc."""

    name: str = Field(..., description="Parameter name including '$' prefix, e.g. '$user'")
    type_str: str = Field(..., description="Raw PHP type string from the docblock")
    rhp_type: Optional[str] = Field(None, description="Mapped RHP type string")
    description: Optional[str] = Field(None, description="Tag description if present")


class DocblockReturn(BaseModel):
    """Represents a parsed @return tag from a PHPDoc."""

    type_str: str = Field(..., description="Raw PHP return type string from the docblock")
    rhp_type: Optional[str] = Field(None, description="Mapped RHP return type string (None for void)")
    description: Optional[str] = Field(None, description="Tag description if present")


class DocblockVar(BaseModel):
    """Represents a parsed @var tag from a PHPDoc."""

    name: Optional[str] = Field(None, description="Variable or property name if present")
    type_str: str = Field(..., description="Raw PHP type string from the docblock")
    rhp_type: Optional[str] = Field(None, description="Mapped RHP type string")
    description: Optional[str] = Field(None, description="Tag description if present")


class ParsedDocblock(BaseModel):
    """Contains parsed tags and summary extracted from a PHPDoc block."""

    raw_text: str = Field(..., description="Original raw docblock comment string")
    summary: Optional[str] = Field(None, description="Docblock summary/description before tags")
    params: dict[str, DocblockParam] = Field(default_factory=dict, description="Params indexed by $name and name")
    returns: Optional[DocblockReturn] = Field(None, description="Parsed return tag if present")
    vars: list[DocblockVar] = Field(default_factory=list, description="Parsed @var tags")

    def get_param_type(self, name: str) -> Optional[str]:
        """Retrieve the mapped RHP type for a parameter by name (with or without '$')."""
        normalized = name if name.startswith("$") else f"${name}"
        clean = name.lstrip("$")
        param = self.params.get(normalized) or self.params.get(clean)
        return param.rhp_type if param else None

    def get_return_type(self) -> Optional[str]:
        """Retrieve mapped RHP return type (None if void or absent)."""
        return self.returns.rhp_type if self.returns else None

    def get_var_type(self, name: Optional[str] = None) -> Optional[str]:
        """Retrieve mapped RHP type for a @var tag, optionally filtering by property name."""
        if not self.vars:
            return None
        if name:
            normalized = name if name.startswith("$") else f"${name}"
            clean = name.lstrip("$")
            for v in self.vars:
                if v.name in (normalized, clean):
                    return v.rhp_type
        # If no name specified or no match, return the first var tag's type
        return self.vars[0].rhp_type


# Keywords for discriminating associative arrays (dict) vs sequential arrays (list)
DICT_NAME_KEYWORDS = {
    "map", "dict", "config", "option", "options", "setting", "settings",
    "param", "params", "parameter", "parameters", "attr", "attrs", "attribute", "attributes",
    "assoc", "hash", "payload", "header", "headers", "context", "meta", "metadata",
    "props", "properties", "env", "record", "mapping", "dictionary", "keyvalue",
}

LIST_NAME_KEYWORDS = {
    "list", "item", "items", "elem", "elems", "element", "elements",
    "val", "vals", "value", "values", "row", "rows", "entry", "entries",
    "record", "records", "key", "keys", "id", "ids", "name", "names",
    "tag", "tags", "token", "tokens", "arg", "args", "queue", "stack",
    "collection", "lines", "steps",
}

DICT_DESC_KEYWORDS = (
    "assoc", "associative", "key-value", "key => value", "map", "dict", "hash", "dictionary", "keyed",
)

LIST_DESC_KEYWORDS = (
    "list of", "array of", "sequential", "collection of", "ordered",
)


def infer_list_or_dict(
    type_str: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """Infer whether an array type should be 'list' or 'dict'.

    Args:
        type_str: Raw PHP type string (e.g. 'int[]', 'array<string, int>', 'array').
        name: Associated parameter or property name.
        description: Tag or docblock description text.

    Returns:
        'list' or 'dict'.
    """
    raw = type_str.strip()
    low = raw.lower()

    # Postfix nullable removal for inspection
    if raw.startswith("?"):
        raw = raw[1:].strip()
        low = raw.lower()

    # Explicit T[] syntax always represents a list
    if raw.endswith("[]"):
        return "list"

    # Explicit list<T> or list
    if low == "list" or low.startswith("list<"):
        return "list"

    # Explicit dict<K, V> or dict
    if low == "dict" or low.startswith("dict<"):
        return "dict"

    # PHPStan/Psalm shapes like array{key: type} represent dictionaries
    if low.startswith("array{") and raw.endswith("}"):
        return "dict"

    # Generics like array<T> or array<K, V>
    if low.startswith("array<") and raw.endswith(">"):
        inner = raw[6:-1].strip()
        args = _split_generic_arguments(inner)
        if len(args) == 1:
            return "list"
        if len(args) >= 2:
            key_type = args[0].strip().lower()
            if key_type == "int":
                return "list"
            return "dict"

    # Bare 'array' - apply heuristics based on description and parameter/property name
    desc_low = (description or "").lower()
    clean_name = (name or "").lower().lstrip("$")

    # Check description first
    if any(k in desc_low for k in DICT_DESC_KEYWORDS):
        return "dict"
    if any(k in desc_low for k in LIST_DESC_KEYWORDS):
        return "list"

    # Check name keywords
    if any(k in clean_name for k in DICT_NAME_KEYWORDS):
        return "dict"
    if any(k in clean_name for k in LIST_NAME_KEYWORDS):
        return "list"

    # Plural names ending in 's' (excluding 'status', 'class', etc.) imply lists
    if clean_name.endswith("s") and not clean_name.endswith("ss") and clean_name not in ("status", "basis"):
        return "list"

    # Default fallback for untyped array in RHP is list
    return "list"


def _split_generic_arguments(inner: str) -> list[str]:
    """Split comma-separated generic arguments respecting nested brackets/parentheses."""
    args: list[str] = []
    current: list[str] = []
    depth = 0
    for char in inner:
        if char in "<{(":
            depth += 1
            current.append(char)
        elif char in ">})":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def map_php_type_to_rhp(
    php_type: Optional[str],
    name: Optional[str] = None,
    description: Optional[str] = None,
    preserve_generics: bool = True,
) -> Optional[str]:
    """Map a PHP type representation to its equivalent RHP type string.

    Mapping rules:
        - int / integer -> int
        - string -> string
        - bool / boolean -> bool
        - float / double / real -> float
        - array or T[] -> infer list or dict
        - mixed -> mixed
        - void -> None (RHP treats omitted return as void)
        - ?Type -> Type? (RHP uses postfix nullable)
        - Type|null -> Type?

    Args:
        php_type: PHP type string to map.
        name: Name of parameter/property for array disambiguation heuristics.
        description: Description text for array disambiguation heuristics.
        preserve_generics: Whether to retain generic parameters e.g. list<int> vs list.

    Returns:
        Mapped RHP type string, or None if void or empty.
    """
    if not php_type:
        return None

    raw = php_type.strip()
    if not raw or raw.lower() == "void":
        return None

    # Handle nullable prefix: ?Type -> Type?
    is_nullable = False
    if raw.startswith("?"):
        is_nullable = True
        raw = raw[1:].strip()

    # Handle union types containing null (e.g. Type|null or null|Type)
    if "|" in raw:
        parts = _split_union_types(raw)
        has_null = any(p.lower() == "null" for p in parts)
        if has_null:
            is_nullable = True
            non_null_parts = [p for p in parts if p.lower() != "null"]
            if len(non_null_parts) == 1:
                raw = non_null_parts[0]
            elif non_null_parts:
                mapped_parts = [
                    map_php_type_to_rhp(p, name=name, description=description, preserve_generics=preserve_generics)
                    for p in non_null_parts
                ]
                mapped_non_null = "|".join(m for m in mapped_parts if m)
                return f"({mapped_non_null})?" if mapped_non_null else None
            else:
                return "null"
        else:
            # Union of non-null types: map each component
            mapped_parts = [
                map_php_type_to_rhp(p, name=name, description=description, preserve_generics=preserve_generics)
                for p in parts
            ]
            valid_parts = [m for m in mapped_parts if m]
            return "|".join(valid_parts) if valid_parts else None

    low = raw.lower()

    # Scalar and primitive type mappings
    if low in ("int", "integer"):
        rhp = "int"
    elif low == "string":
        rhp = "string"
    elif low in ("bool", "boolean", "true", "false"):
        rhp = "bool"
    elif low in ("float", "double", "real"):
        rhp = "float"
    elif low == "mixed":
        rhp = "mixed"
    elif low == "never":
        rhp = "never"
    elif low == "object":
        rhp = "object"
    elif low in ("callable", "closure", "\\closure"):
        rhp = "callable"
    elif low in ("self", "static", "$this"):
        rhp = "self"

    # T[] array syntax
    elif raw.endswith("[]"):
        inner = raw[:-2].strip()
        if preserve_generics:
            mapped_inner = map_php_type_to_rhp(inner, preserve_generics=preserve_generics) or "mixed"
            rhp = f"list<{mapped_inner}>"
        else:
            rhp = "list"

    # list<T> or list
    elif low.startswith("list<") and raw.endswith(">"):
        inner = raw[5:-1].strip()
        if preserve_generics:
            mapped_inner = map_php_type_to_rhp(inner, preserve_generics=preserve_generics) or "mixed"
            rhp = f"list<{mapped_inner}>"
        else:
            rhp = "list"
    elif low == "list":
        rhp = "list"

    # dict<K, V> or dict
    elif low.startswith("dict<") and raw.endswith(">"):
        inner = raw[5:-1].strip()
        args = _split_generic_arguments(inner)
        if preserve_generics:
            k = map_php_type_to_rhp(args[0], preserve_generics=preserve_generics) if len(args) > 0 else "string"
            v = map_php_type_to_rhp(args[1], preserve_generics=preserve_generics) if len(args) > 1 else "mixed"
            rhp = f"dict<{k or 'string'}, {v or 'mixed'}>"
        else:
            rhp = "dict"
    elif low == "dict":
        rhp = "dict"

    # array{...} shape
    elif low.startswith("array{") and raw.endswith("}"):
        rhp = "dict"

    # array<T> or array<K, V>
    elif low.startswith("array<") and raw.endswith(">"):
        inner = raw[6:-1].strip()
        args = _split_generic_arguments(inner)
        if len(args) == 1:
            if preserve_generics:
                mapped_elem = map_php_type_to_rhp(args[0], preserve_generics=preserve_generics) or "mixed"
                rhp = f"list<{mapped_elem}>"
            else:
                rhp = "list"
        else:
            key_raw = args[0].strip()
            val_raw = args[1].strip()
            if key_raw.lower() == "int":
                if preserve_generics:
                    mapped_val = map_php_type_to_rhp(val_raw, preserve_generics=preserve_generics) or "mixed"
                    rhp = f"list<{mapped_val}>"
                else:
                    rhp = "list"
            else:
                if preserve_generics:
                    k = map_php_type_to_rhp(key_raw, preserve_generics=preserve_generics) or "string"
                    v = map_php_type_to_rhp(val_raw, preserve_generics=preserve_generics) or "mixed"
                    rhp = f"dict<{k}, {v}>"
                else:
                    rhp = "dict"

    # Bare array
    elif low == "array":
        rhp = infer_list_or_dict(low, name=name, description=description)

    # Class, interface, or custom type name
    else:
        # Strip leading backslash if present (e.g. \App\Models\User -> App\Models\User)
        rhp = raw.lstrip("\\")

    if is_nullable and rhp:
        return f"{rhp}?"

    return rhp


def _split_union_types(union_str: str) -> list[str]:
    """Split union types on '|' ignoring pipes inside generic or shape brackets."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in union_str:
        if char in "<{(":
            depth += 1
            current.append(char)
        elif char in ">})":
            depth -= 1
            current.append(char)
        elif char == "|" and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(char)
    last_part = "".join(current).strip()
    if last_part:
        parts.append(last_part)
    return parts


class DocblockParser:
    """Parser for PHPDoc comment blocks above functions, methods, and properties."""

    _PARAM_REGEX = re.compile(r"^@param\s+(?P<rest>.+)$", re.DOTALL)
    _RETURN_REGEX = re.compile(r"^@returns?\s+(?P<rest>.+)$", re.DOTALL)
    _VAR_REGEX = re.compile(r"^@var\s+(?P<rest>.+)$", re.DOTALL)
    _VAR_NAME_REGEX = re.compile(r"(?:&|\.{3})?\$[a-zA-Z_\x80-\xff][a-zA-Z0-9_\x80-\xff]*")

    @classmethod
    def parse(cls, docblock_text: Optional[str]) -> Optional[ParsedDocblock]:
        """Parse a raw PHPDoc block string into a structured ParsedDocblock.

        Args:
            docblock_text: Raw comment text starting with /** and ending with */.

        Returns:
            ParsedDocblock if valid docblock, or None if docblock_text is empty/invalid.
        """
        if not docblock_text:
            return None

        cleaned_lines = cls._clean_lines(docblock_text)
        if not cleaned_lines:
            return None

        summary_lines: list[str] = []
        params: dict[str, DocblockParam] = {}
        returns: Optional[DocblockReturn] = None
        vars_list: list[DocblockVar] = []

        is_tag_section = False

        for line in cleaned_lines:
            if line.startswith("@"):
                is_tag_section = True

            if not is_tag_section:
                summary_lines.append(line)
                continue

            # Process @param
            param_match = cls._PARAM_REGEX.match(line)
            if param_match:
                param = cls._parse_param_rest(param_match.group("rest"))
                if param:
                    params[param.name] = param
                    params[param.name.lstrip("$")] = param
                continue

            # Process @return / @returns
            return_match = cls._RETURN_REGEX.match(line)
            if return_match:
                returns = cls._parse_return_rest(return_match.group("rest"))
                continue

            # Process @var
            var_match = cls._VAR_REGEX.match(line)
            if var_match:
                v = cls._parse_var_rest(var_match.group("rest"))
                if v:
                    vars_list.append(v)
                continue

        summary = "\n".join(summary_lines).strip() if summary_lines else None

        return ParsedDocblock(
            raw_text=docblock_text,
            summary=summary,
            params=params,
            returns=returns,
            vars=vars_list,
        )

    @classmethod
    def extract_and_parse(cls, node: tree_sitter.Node) -> Optional[ParsedDocblock]:
        """Extract preceding docblock from a tree-sitter AST node and parse it."""
        raw = PHPParser.extract_docblock(node)
        return cls.parse(raw)

    @classmethod
    def _clean_lines(cls, docblock_text: str) -> list[str]:
        """Strip comment delimiters (/**, *, */) and return clean content lines."""
        lines: list[str] = []
        for line in docblock_text.splitlines():
            stripped = line.strip()
            if stripped in ("/**", "/*", "*/"):
                continue
            if stripped.startswith("/**"):
                stripped = stripped[3:].strip()
            elif stripped.startswith("/*"):
                stripped = stripped[2:].strip()
            if stripped.endswith("*/"):
                stripped = stripped[:-2].strip()
            if stripped.startswith("*"):
                stripped = stripped[1:].strip()
            if stripped:
                lines.append(stripped)
        return lines

    @classmethod
    def _parse_param_rest(cls, rest: str) -> Optional[DocblockParam]:
        """Parse the remainder of an @param line into type, name, and description."""
        rest = rest.strip()
        if not rest:
            return None

        # Look for the $param variable token
        var_match = cls._VAR_NAME_REGEX.search(rest)
        if var_match:
            start, end = var_match.span()
            name = var_match.group(0).lstrip("&.")
            type_part = rest[:start].strip()
            desc_part = rest[end:].strip()

            # Handle edge case where name preceded type (e.g. @param $name string)
            if not type_part and desc_part:
                type_tokens = desc_part.split(None, 1)
                type_part = type_tokens[0]
                desc_part = type_tokens[1] if len(type_tokens) > 1 else ""

            type_str = type_part if type_part else "mixed"
            description = desc_part if desc_part else None
            rhp_type = map_php_type_to_rhp(type_str, name=name, description=description)

            return DocblockParam(
                name=name,
                type_str=type_str,
                rhp_type=rhp_type,
                description=description,
            )

        # Fallback if no variable name is found
        type_str, desc = cls._extract_type_and_desc(rest)
        rhp_type = map_php_type_to_rhp(type_str, description=desc)
        return DocblockParam(
            name="$unknown",
            type_str=type_str,
            rhp_type=rhp_type,
            description=desc if desc else None,
        )

    @classmethod
    def _parse_return_rest(cls, rest: str) -> Optional[DocblockReturn]:
        """Parse the remainder of an @return line into type and description."""
        rest = rest.strip()
        if not rest:
            return None

        type_str, desc = cls._extract_type_and_desc(rest)
        description = desc if desc else None
        rhp_type = map_php_type_to_rhp(type_str, description=description)

        return DocblockReturn(
            type_str=type_str,
            rhp_type=rhp_type,
            description=description,
        )

    @classmethod
    def _parse_var_rest(cls, rest: str) -> Optional[DocblockVar]:
        """Parse the remainder of a @var line into type, optional name, and description."""
        rest = rest.strip()
        if not rest:
            return None

        var_match = cls._VAR_NAME_REGEX.search(rest)
        if var_match:
            start, end = var_match.span()
            name = var_match.group(0).lstrip("&.")
            type_part = rest[:start].strip()
            desc_part = rest[end:].strip()

            # Handle @var $prop type
            if not type_part and desc_part:
                tokens = desc_part.split(None, 1)
                type_part = tokens[0]
                desc_part = tokens[1] if len(tokens) > 1 else ""

            type_str = type_part if type_part else "mixed"
            description = desc_part if desc_part else None
            rhp_type = map_php_type_to_rhp(type_str, name=name, description=description)

            return DocblockVar(
                name=name,
                type_str=type_str,
                rhp_type=rhp_type,
                description=description,
            )

        # No variable name: @var <type> [<description>]
        type_str, desc = cls._extract_type_and_desc(rest)
        description = desc if desc else None
        rhp_type = map_php_type_to_rhp(type_str, description=description)

        return DocblockVar(
            name=None,
            type_str=type_str,
            rhp_type=rhp_type,
            description=description,
        )

    @staticmethod
    def _extract_type_and_desc(rest: str) -> tuple[str, str]:
        """Extract type and description by tracking balanced brackets and whitespace."""
        depth = 0
        i = 0
        while i < len(rest):
            char = rest[i]
            if char in "<{(":
                depth += 1
            elif char in ">})":
                depth -= 1
            elif char.isspace() and depth == 0:
                prev = rest[:i].rstrip()
                if prev and prev[-1] not in ("|", "&", ","):
                    next_part = rest[i:].lstrip()
                    if next_part and next_part[0] not in ("|", "&", ","):
                        return rest[:i].strip(), rest[i:].strip()
            i += 1
        return rest.strip(), ""
