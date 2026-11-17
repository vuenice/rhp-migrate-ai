"""Scanner detecting unsupported PHP features and patterns for RHP migration."""

from enum import Enum
from pathlib import Path
from typing import Optional, Sequence, Union
from pydantic import BaseModel, ConfigDict, Field
import tree_sitter

from rhp_migrate.analyzer.parser import PHPParser


class UnsupportedFeature(str, Enum):
    """Enumeration of unsupported PHP features in RHP."""
    REFLECTION = "REFLECTION"
    OUTPUT_BUFFERING = "OUTPUT_BUFFERING"
    DYNAMIC_INCLUDE = "DYNAMIC_INCLUDE"
    EVAL = "EVAL"
    VARIABLE_VARIABLE = "VARIABLE_VARIABLE"
    MAGIC_METHOD = "MAGIC_METHOD"
    CURL_MULTI = "CURL_MULTI"


class UnsupportedRuleMeta:
    """Metadata describing unsupported rules."""
    RULES = {
        UnsupportedFeature.REFLECTION: {
            "id": "RHP-UNSUP-001",
            "title": "Reflection API unsupported",
            "description": "The runtime Reflection API is not supported in RHP due to strict compile-time optimization.",
        },
        UnsupportedFeature.OUTPUT_BUFFERING: {
            "id": "RHP-UNSUP-002",
            "title": "Output buffering unsupported",
            "description": "Output buffering (ob_*) is not supported in RHP. Refactor to direct returns or templating.",
        },
        UnsupportedFeature.DYNAMIC_INCLUDE: {
            "id": "RHP-UNSUP-003",
            "title": "Dynamic file inclusion unsupported",
            "description": "Dynamic runtime file inclusions (include/require $path) are prohibited in RHP.",
        },
        UnsupportedFeature.EVAL: {
            "id": "RHP-UNSUP-004",
            "title": "Dynamic eval() execution unsupported",
            "description": "Dynamic code execution via eval() is prohibited in RHP.",
        },
        UnsupportedFeature.VARIABLE_VARIABLE: {
            "id": "RHP-UNSUP-005",
            "title": "Variable variables unsupported",
            "description": "Dynamic variable variables ($$var) are prohibited in RHP.",
        },
        UnsupportedFeature.MAGIC_METHOD: {
            "id": "RHP-UNSUP-006",
            "title": "Magic methods unsupported",
            "description": "Dynamic magic methods (__get, __set, __call, etc.) are unsupported in RHP.",
        },
        UnsupportedFeature.CURL_MULTI: {
            "id": "RHP-UNSUP-007",
            "title": "cURL multi-loop unsupported",
            "description": "Classic procedural cURL multi-loops (curl_multi_*) are unsupported in RHP.",
        },
    }

    @classmethod
    def get_rule_id(cls, feature: UnsupportedFeature) -> str:
        return cls.RULES[feature]["id"]

    @classmethod
    def get_title(cls, feature: UnsupportedFeature) -> str:
        return cls.RULES[feature]["title"]

    @classmethod
    def get_description(cls, feature: UnsupportedFeature) -> str:
        return cls.RULES[feature]["description"]


class UnsupportedFinding(BaseModel):
    """Represents a single detected unsupported feature violation in code."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., description="Unique rule ID, e.g. RHP-UNSUP-001")
    title: str = Field(..., description="Human-readable title describing the violation")
    feature: UnsupportedFeature = Field(..., description="The unsupported feature enum category")
    message: str = Field(..., description="Detailed diagnostic message explaining why this is forbidden")
    line: int = Field(..., description="1-indexed line number")
    column: int = Field(..., description="1-indexed column number")
    node_text: str = Field(..., description="Source code snippet triggering the violation")
    file_path: Optional[Path] = Field(None, description="Path to the file containing the violation")


FORBIDDEN_MAGIC_METHODS = {
    "__get",
    "__set",
    "__call",
    "__callStatic",
    "__sleep",
    "__wakeup",
    "__set_state",
    "__isset",
    "__unset",
}


class UnsupportedScanner:
    """AST visitor detecting forbidden RHP patterns in PHP code."""

    def __init__(self, parser: Optional[PHPParser] = None) -> None:
        self.parser = parser or PHPParser()

    def scan_source(
        self,
        source: Union[str, bytes],
        file_path: Optional[Union[str, Path]] = None,
    ) -> list[UnsupportedFinding]:
        """Scan PHP source code for unsupported patterns."""
        tree = self.parser.parse(source)
        resolved_path = Path(file_path) if file_path else None
        return self.scan_tree(tree, resolved_path)

    def scan_file(self, file_path: Union[str, Path]) -> list[UnsupportedFinding]:
        """Read and scan a PHP file for unsupported patterns."""
        path = Path(file_path)
        tree = self.parser.parse_file(path)
        return self.scan_tree(tree, path)

    def scan_tree(
        self,
        tree: tree_sitter.Tree,
        file_path: Optional[Path] = None,
    ) -> list[UnsupportedFinding]:
        """Scan an AST tree for unsupported patterns."""
        findings: list[UnsupportedFinding] = []
        seen_positions: set[tuple[int, int, str]] = set()

        def add_finding(
            feature: UnsupportedFeature,
            message: str,
            node: tree_sitter.Node,
            custom_text: Optional[str] = None,
        ) -> None:
            line = node.start_point.row + 1
            col = node.start_point.column + 1
            rule_id = UnsupportedRuleMeta.get_rule_id(feature)
            key = (line, col, rule_id)
            if key in seen_positions:
                return
            seen_positions.add(key)

            snippet = custom_text if custom_text is not None else PHPParser.get_node_text(node)
            # Truncate very long snippets for readability
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."

            findings.append(
                UnsupportedFinding(
                    rule_id=rule_id,
                    title=UnsupportedRuleMeta.get_title(feature),
                    feature=feature,
                    message=message,
                    line=line,
                    column=col,
                    node_text=snippet,
                    file_path=file_path,
                )
            )

        for node in PHPParser.traverse(tree.root_node):
            # 1. Variable variables: $$foo, ${$bar}
            if node.type == "dynamic_variable_name":
                # Skip if parent is also dynamic_variable_name to avoid duplicate on $$$foo
                if not (node.parent and node.parent.type == "dynamic_variable_name"):
                    var_text = PHPParser.get_node_text(node)
                    add_finding(
                        UnsupportedFeature.VARIABLE_VARIABLE,
                        f"Dynamic variable variable '{var_text}' is unsupported in RHP.",
                        node,
                    )

            # 2. Dynamic includes: include $path, require $file, etc.
            elif node.type in ("include_expression", "require_expression", "include_once_expression", "require_once_expression"):
                if self._is_dynamic_include(node):
                    inc_text = PHPParser.get_node_text(node)
                    add_finding(
                        UnsupportedFeature.DYNAMIC_INCLUDE,
                        f"Dynamic file inclusion '{inc_text}' is unsupported in RHP.",
                        node,
                    )

            # 3. Magic methods: __get, __set, __call, etc.
            elif node.type == "method_declaration":
                for child in node.children:
                    if child.type == "name":
                        method_name = PHPParser.get_node_text(child)
                        if method_name in FORBIDDEN_MAGIC_METHODS:
                            add_finding(
                                UnsupportedFeature.MAGIC_METHOD,
                                f"Magic method '{method_name}' is unsupported in RHP.",
                                child,
                                custom_text=f"function {method_name}(...)",
                            )
                        break

            # 4. Function calls: eval(), ob_*(), curl_multi_*()
            elif node.type == "function_call_expression":
                fn_name = None
                fn_node = None
                for child in node.children:
                    if child.type in ("name", "qualified_name"):
                        fn_name = PHPParser.get_node_text(child)
                        fn_node = child
                        break

                if fn_name and fn_node:
                    clean_name = fn_name.lstrip("\\").lower()
                    if clean_name == "eval":
                        add_finding(
                            UnsupportedFeature.EVAL,
                            "Dynamic eval() execution is unsupported in RHP.",
                            node,
                        )
                    elif clean_name.startswith("ob_"):
                        add_finding(
                            UnsupportedFeature.OUTPUT_BUFFERING,
                            f"Output buffering function '{fn_name}()' is unsupported in RHP.",
                            fn_node,
                        )
                    elif clean_name.startswith("curl_multi_"):
                        add_finding(
                            UnsupportedFeature.CURL_MULTI,
                            f"Classic cURL multi function '{fn_name}()' is unsupported in RHP.",
                            fn_node,
                        )

            # 5. Reflection API usage: ReflectionClass, ReflectionMethod, etc.
            elif node.type in ("name", "qualified_name"):
                # Avoid duplicate if child of qualified_name
                if node.parent and node.parent.type == "qualified_name":
                    continue
                name_text = PHPParser.get_node_text(node)
                clean_name = name_text.lstrip("\\")
                if clean_name.startswith("Reflection") and len(clean_name) > 10:
                    add_finding(
                        UnsupportedFeature.REFLECTION,
                        f"Reflection API usage '{name_text}' is unsupported in RHP.",
                        node,
                    )

        # Sort findings by line and column
        findings.sort(key=lambda f: (f.line, f.column))
        return findings

    @staticmethod
    def _is_dynamic_include(node: tree_sitter.Node) -> bool:
        """Check whether an include/require expression includes a dynamic operand."""
        operand = None
        for child in node.children:
            if child.type not in ("include", "require", "include_once", "require_once"):
                operand = child
                break

        if operand is None:
            return False

        # Static single-quoted string literal: 'file.php'
        if operand.type == "string":
            return False

        # Encapsed string: "file.php" or "prefix/{$var}.php"
        if operand.type == "encapsed_string":
            for sub in operand.children:
                if sub.type in ("variable_name", "embedded_expression", "dynamic_variable_name"):
                    return True
            return False

        # Any variable, concatenation, or expression is dynamic
        return True
