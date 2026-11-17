"""Type inference combining AST defaults, docblocks, static analysis, and heuristics."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import re
from typing import Any, Optional, Union
from pydantic import BaseModel, ConfigDict, Field
import tree_sitter

from rhp_migrate.analyzer.parser import PHPParser
from rhp_migrate.analyzer.symbols import PHPClass, PHPFunction, PHPMethod, PHPParameter, PHPProperty, SymbolExtractor
from rhp_migrate.types.docblock import (
    DocblockParser,
    ParsedDocblock,
    infer_list_or_dict,
    map_php_type_to_rhp,
)
from rhp_migrate.types.phpstan_loader import PHPStanLoader


class TypeConfidence(float, Enum):
    """Confidence levels assigned to extracted and inferred types."""

    EXPLICIT = 1.0          # Already in PHP 7/8 type hint
    STATIC_ANALYSIS = 0.9   # From PHPStan or Psalm JSON export
    DOCBLOCK = 0.8          # From @param, @return, or @var
    DEFAULT_VALUE = 0.7     # Inferred from default literal expression
    USAGE_HEURISTIC = 0.4   # Inferred from array usage (e.g. $arr[] = implies list)
    UNKNOWN = 0.0           # Needs human or LLM intervention


class InferredType(BaseModel):
    """Holds an inferred type, confidence score, source, and LLM triage flag."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Parameter, property, or 'return' identifier")
    inferred_type: Optional[str] = Field(None, description="Mapped RHP type string, or None for void/unknown")
    confidence: TypeConfidence = Field(TypeConfidence.UNKNOWN, description="Confidence level")
    source: str = Field("unknown", description="Detection origin (e.g. 'explicit', 'docblock', 'default_value')")
    details: Optional[str] = Field(None, description="Additional context or rationale")
    needs_llm: bool = Field(False, description="Flagged for LLM stage if confidence < 0.6 or UNKNOWN")

    def model_post_init(self, __context: Any) -> None:
        """Automatically flag items requiring LLM assistance."""
        if self.confidence.value < 0.6 or self.confidence == TypeConfidence.UNKNOWN:
            self.needs_llm = True
        elif self.inferred_type is None and self.source not in ("explicit_void", "docblock_void", "implicit_void"):
            self.needs_llm = True


class FunctionTypeInference(BaseModel):
    """Aggregated type inference results for a function or method."""

    model_config = ConfigDict(extra="forbid")

    function_name: str = Field(..., description="Function or method name")
    parameters: dict[str, InferredType] = Field(default_factory=dict, description="Inferred parameter types")
    return_type: InferredType = Field(..., description="Inferred return type")
    needs_llm: bool = Field(False, description="Whether any parameter or return type requires LLM stage")

    def model_post_init(self, __context: Any) -> None:
        if any(p.needs_llm for p in self.parameters.values()) or self.return_type.needs_llm:
            self.needs_llm = True


class ClassTypeInference(BaseModel):
    """Aggregated type inference results for a class, trait, or interface."""

    model_config = ConfigDict(extra="forbid")

    class_name: str = Field(..., description="Class name")
    properties: dict[str, InferredType] = Field(default_factory=dict, description="Inferred property types")
    methods: dict[str, FunctionTypeInference] = Field(default_factory=dict, description="Inferred method types")
    needs_llm: bool = Field(False, description="Whether any property or method requires LLM stage")

    def model_post_init(self, __context: Any) -> None:
        if any(p.needs_llm for p in self.properties.values()) or any(m.needs_llm for m in self.methods.values()):
            self.needs_llm = True


class FileTypeInference(BaseModel):
    """Type inference report for an entire file or source snippet."""

    model_config = ConfigDict(extra="forbid")

    file_path: Optional[str] = Field(None, description="Path of analyzed file")
    classes: dict[str, ClassTypeInference] = Field(default_factory=dict, description="Classes analyzed")
    functions: dict[str, FunctionTypeInference] = Field(default_factory=dict, description="Standalone functions analyzed")
    low_confidence_items: list[InferredType] = Field(default_factory=list, description="All items flagged for LLM (< 0.6)")

    @property
    def needs_llm(self) -> bool:
        """True if any types need human or LLM triage."""
        return len(self.low_confidence_items) > 0


def infer_type_from_default_value(
    default_expr: Optional[str],
    name: Optional[str] = None,
) -> tuple[Optional[str], Optional[TypeConfidence]]:
    """Infer RHP type from a PHP default value expression string.

    Args:
        default_expr: Expression string, e.g. '10', "'hello'", '[]', "['k' => 1]".
        name: Associated parameter or property name for array heuristics.

    Returns:
        (rhp_type, TypeConfidence) or (None, None).
    """
    if not default_expr:
        return None, None

    expr = default_expr.strip()

    # Integer literals (decimal, hex, binary, octal)
    if re.match(r"^-?(?:0|[1-9]\d*|0x[0-9a-fA-F]+|0b[01]+|0[0-7]+)$", expr):
        return "int", TypeConfidence.DEFAULT_VALUE

    # Float literals
    if re.match(r"^-?(?:\d+\.\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+)$", expr):
        return "float", TypeConfidence.DEFAULT_VALUE

    # Boolean literals
    if expr.lower() in ("true", "false"):
        return "bool", TypeConfidence.DEFAULT_VALUE

    # String literals
    if (expr.startswith("'") and expr.endswith("'")) or (expr.startswith('"') and expr.endswith('"')):
        return "string", TypeConfidence.DEFAULT_VALUE

    # Null literal: by itself, the base type cannot be determined without other clues
    if expr.lower() == "null":
        return None, TypeConfidence.UNKNOWN

    # Empty array literals: [] or array()
    if expr in ("[]", "array()"):
        kind = infer_list_or_dict("array", name=name)
        return kind, TypeConfidence.DEFAULT_VALUE

    # Array literals with elements
    if (expr.startswith("[") and expr.endswith("]")) or (expr.startswith("array(") and expr.endswith(")")):
        inner = expr[1:-1] if expr.startswith("[") else expr[6:-1]
        # Check if => is present inside the array literal
        if "=>" in inner:
            # Associative dictionary
            parsed_kv = _inspect_array_literal_elements(inner)
            if parsed_kv:
                k_type, v_type = parsed_kv
                return f"dict<{k_type}, {v_type}>", TypeConfidence.DEFAULT_VALUE
            return "dict", TypeConfidence.DEFAULT_VALUE
        else:
            # Sequential list
            elem_type = _inspect_list_literal_elements(inner)
            if elem_type:
                return f"list<{elem_type}>", TypeConfidence.DEFAULT_VALUE
            return "list", TypeConfidence.DEFAULT_VALUE

    return None, None


def _inspect_list_literal_elements(inner: str) -> Optional[str]:
    """Inspect elements in a sequential array literal to infer homogeneous type."""
    items = [item.strip() for item in inner.split(",") if item.strip()]
    if not items:
        return None
    inferred_types: set[str] = set()
    for item in items:
        t, _ = infer_type_from_default_value(item)
        if t:
            inferred_types.add(t)
        else:
            return None
    if len(inferred_types) == 1:
        return next(iter(inferred_types))
    return None


def _inspect_array_literal_elements(inner: str) -> Optional[tuple[str, str]]:
    """Inspect key-value pairs in an associative array literal."""
    pairs = [p.strip() for p in inner.split(",") if p.strip()]
    if not pairs:
        return None
    key_types: set[str] = set()
    val_types: set[str] = set()
    for pair in pairs:
        if "=>" not in pair:
            return None
        k_str, v_str = pair.split("=>", 1)
        k_t, _ = infer_type_from_default_value(k_str.strip())
        v_t, _ = infer_type_from_default_value(v_str.strip())
        if k_t:
            key_types.add(k_t)
        if v_t:
            val_types.add(v_t)
    if len(key_types) == 1 and len(val_types) == 1:
        return next(iter(key_types)), next(iter(val_types))
    return None


class TypeInferenceEngine:
    """Combines AST defaults, docblocks, static analysis, and usage heuristics."""

    def __init__(
        self,
        parser: Optional[PHPParser] = None,
        static_loader: Optional[PHPStanLoader] = None,
    ) -> None:
        self.parser = parser or PHPParser()
        self.static_loader = static_loader or PHPStanLoader()
        self.extractor = SymbolExtractor(self.parser)

    def infer_parameter(
        self,
        param: PHPParameter,
        docblock: Optional[ParsedDocblock] = None,
        body_node: Optional[tree_sitter.Node] = None,
        static_loader: Optional[PHPStanLoader] = None,
        class_name: Optional[str] = None,
        function_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> InferredType:
        """Infer type and confidence for a single parameter."""
        loader = static_loader or self.static_loader
        clean_name = param.name.lstrip("$")
        context_symbol = f"{class_name}::{function_name}" if class_name and function_name else function_name

        # 1. Explicit PHP type hint
        if param.type_hint:
            raw_hint = param.type_hint.strip()

            # Array type hint in PHP lacks list vs dict specification: disambiguate
            if raw_hint.lower() == "array":
                # Check static analysis for array specialization
                sa_type = self._lookup_static_analysis(
                    loader, file_path=file_path, symbol_prefix=context_symbol, param_name=param.name
                )
                if sa_type and sa_type.lower() != "array":
                    return InferredType(
                        name=param.name,
                        inferred_type=sa_type,
                        confidence=TypeConfidence.STATIC_ANALYSIS,
                        source="static_analysis",
                        details="Specialized PHP explicit array via static analysis",
                    )

                # Check docblock
                if docblock:
                    doc_t = docblock.get_param_type(param.name)
                    if doc_t and doc_t.lower() != "array":
                        return InferredType(
                            name=param.name,
                            inferred_type=doc_t,
                            confidence=TypeConfidence.DOCBLOCK,
                            source="docblock",
                            details="Specialized PHP explicit array via docblock",
                        )

                # Check default value
                if param.default_value:
                    def_t, def_conf = infer_type_from_default_value(param.default_value, name=param.name)
                    if def_t:
                        return InferredType(
                            name=param.name,
                            inferred_type=def_t,
                            confidence=TypeConfidence.DEFAULT_VALUE,
                            source="default_value",
                            details="Specialized PHP explicit array via default literal",
                        )

                # Check usage heuristic in body
                if body_node:
                    usage_t, usage_conf = self._infer_usage_heuristic(body_node, param.name)
                    if usage_t:
                        return InferredType(
                            name=param.name,
                            inferred_type=usage_t,
                            confidence=TypeConfidence.USAGE_HEURISTIC,
                            source="usage_heuristic",
                            details="Specialized PHP explicit array via AST usage",
                        )

                # Default fallback for explicit array
                kind = infer_list_or_dict("array", name=param.name)
                return InferredType(
                    name=param.name,
                    inferred_type=kind,
                    confidence=TypeConfidence.USAGE_HEURISTIC,
                    source="usage_heuristic",
                    details=f"Inferred {kind} from parameter name '{param.name}'",
                )

            # Concrete explicit type hint
            mapped = map_php_type_to_rhp(raw_hint, name=param.name)
            # If default is null and type hint was not already nullable, make nullable
            if param.default_value and param.default_value.lower() == "null" and mapped and not mapped.endswith("?"):
                mapped = f"{mapped}?"

            return InferredType(
                name=param.name,
                inferred_type=mapped,
                confidence=TypeConfidence.EXPLICIT,
                source="explicit",
                details=f"PHP declared type hint '{raw_hint}'",
            )

        # 2. Static analysis (PHPStan / Psalm)
        sa_type = self._lookup_static_analysis(
            loader, file_path=file_path, symbol_prefix=context_symbol, param_name=param.name
        )
        if sa_type:
            return InferredType(
                name=param.name,
                inferred_type=sa_type,
                confidence=TypeConfidence.STATIC_ANALYSIS,
                source="static_analysis",
                details="Deduced by static analysis",
            )

        # 3. Docblock @param
        if docblock:
            doc_t = docblock.get_param_type(param.name)
            if doc_t:
                if param.default_value and param.default_value.lower() == "null" and not doc_t.endswith("?"):
                    doc_t = f"{doc_t}?"
                return InferredType(
                    name=param.name,
                    inferred_type=doc_t,
                    confidence=TypeConfidence.DOCBLOCK,
                    source="docblock",
                    details=f"Extracted from @param for {param.name}",
                )

        # 4. AST default value
        if param.default_value:
            def_t, def_conf = infer_type_from_default_value(param.default_value, name=param.name)
            if def_t and def_conf:
                return InferredType(
                    name=param.name,
                    inferred_type=def_t,
                    confidence=def_conf,
                    source="default_value",
                    details=f"Inferred from default expression '{param.default_value}'",
                )

        # 5. Usage heuristic from function body
        if body_node:
            usage_t, usage_conf = self._infer_usage_heuristic(body_node, param.name)
            if usage_t and usage_conf:
                return InferredType(
                    name=param.name,
                    inferred_type=usage_t,
                    confidence=usage_conf,
                    source="usage_heuristic",
                    details=f"Inferred from array usage in body ({usage_t})",
                )

        # 6. Unknown / Low confidence -> Flagged for LLM
        return InferredType(
            name=param.name,
            inferred_type=None,
            confidence=TypeConfidence.UNKNOWN,
            source="unknown",
            details="Untyped parameter without docblock, default, or static analysis",
            needs_llm=True,
        )

    def infer_return_type(
        self,
        return_type_hint: Optional[str],
        docblock: Optional[ParsedDocblock] = None,
        body_node: Optional[tree_sitter.Node] = None,
        static_loader: Optional[PHPStanLoader] = None,
        class_name: Optional[str] = None,
        function_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> InferredType:
        """Infer return type and confidence for a function or method."""
        loader = static_loader or self.static_loader
        context_symbol = f"{class_name}::{function_name}" if class_name and function_name else function_name

        # 1. Explicit return type hint
        if return_type_hint:
            raw = return_type_hint.strip()
            if raw.lower() == "void":
                return InferredType(
                    name="return",
                    inferred_type=None,
                    confidence=TypeConfidence.EXPLICIT,
                    source="explicit_void",
                    details="Declared void return type (omitted in RHP)",
                    needs_llm=False,
                )
            if raw.lower() == "array":
                # Disambiguate list vs dict
                sa_t = loader.find_type(file_path=file_path, symbol_name=context_symbol)
                if sa_t and sa_t.lower() != "array":
                    return InferredType(
                        name="return",
                        inferred_type=sa_t,
                        confidence=TypeConfidence.STATIC_ANALYSIS,
                        source="static_analysis",
                        details="Specialized PHP explicit array return via static analysis",
                    )
                if docblock and docblock.get_return_type():
                    return InferredType(
                        name="return",
                        inferred_type=docblock.get_return_type(),
                        confidence=TypeConfidence.DOCBLOCK,
                        source="docblock",
                        details="Specialized PHP explicit array return via docblock",
                    )
                if body_node:
                    body_ret = self._infer_from_return_statements(body_node)
                    if body_ret and body_ret[0]:
                        return InferredType(
                            name="return",
                            inferred_type=body_ret[0],
                            confidence=TypeConfidence.DEFAULT_VALUE,
                            source="default_value",
                            details="Specialized PHP explicit array return via return expressions",
                        )
                return InferredType(
                    name="return",
                    inferred_type="list",
                    confidence=TypeConfidence.USAGE_HEURISTIC,
                    source="usage_heuristic",
                    details="Defaulted array return to list",
                )

            mapped = map_php_type_to_rhp(raw)
            return InferredType(
                name="return",
                inferred_type=mapped,
                confidence=TypeConfidence.EXPLICIT,
                source="explicit",
                details=f"PHP declared return type '{raw}'",
                needs_llm=False,
            )

        # 2. Static analysis
        sa_t = loader.find_type(file_path=file_path, symbol_name=context_symbol)
        if sa_t:
            return InferredType(
                name="return",
                inferred_type=sa_t,
                confidence=TypeConfidence.STATIC_ANALYSIS,
                source="static_analysis",
                details="Deduced by static analysis",
                needs_llm=False,
            )

        # 3. Docblock @return
        if docblock and docblock.returns:
            if docblock.returns.type_str.lower() == "void":
                return InferredType(
                    name="return",
                    inferred_type=None,
                    confidence=TypeConfidence.DOCBLOCK,
                    source="docblock_void",
                    details="Docblock @return void (omitted in RHP)",
                    needs_llm=False,
                )
            doc_t = docblock.get_return_type()
            if doc_t:
                return InferredType(
                    name="return",
                    inferred_type=doc_t,
                    confidence=TypeConfidence.DOCBLOCK,
                    source="docblock",
                    details="Extracted from @return tag",
                    needs_llm=False,
                )

        # 4. AST return statements in body
        if body_node:
            body_ret = self._infer_from_return_statements(body_node)
            if body_ret:
                ret_t, ret_conf, ret_src = body_ret
                return InferredType(
                    name="return",
                    inferred_type=ret_t,
                    confidence=ret_conf,
                    source=ret_src,
                    details="Inferred from return statements AST",
                )

        # 5. Unknown
        return InferredType(
            name="return",
            inferred_type=None,
            confidence=TypeConfidence.UNKNOWN,
            source="unknown",
            details="Untyped return without docblock or return statements",
            needs_llm=True,
        )

    def infer_property(
        self,
        prop: PHPProperty,
        docblock: Optional[ParsedDocblock] = None,
        static_loader: Optional[PHPStanLoader] = None,
        class_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> InferredType:
        """Infer type and confidence for a class property."""
        loader = static_loader or self.static_loader
        prop_sym = f"{class_name}::{prop.name}" if class_name else prop.name

        # 1. Explicit PHP type hint
        if prop.type_hint:
            raw = prop.type_hint.strip()
            if raw.lower() == "array":
                # Disambiguate
                sa_t = loader.find_type(file_path=file_path, symbol_name=prop_sym)
                if sa_t and sa_t.lower() != "array":
                    return InferredType(
                        name=prop.name,
                        inferred_type=sa_t,
                        confidence=TypeConfidence.STATIC_ANALYSIS,
                        source="static_analysis",
                    )
                if docblock:
                    var_t = docblock.get_var_type(prop.name)
                    if var_t and var_t.lower() != "array":
                        return InferredType(
                            name=prop.name,
                            inferred_type=var_t,
                            confidence=TypeConfidence.DOCBLOCK,
                            source="docblock",
                        )
                if prop.default_value:
                    def_t, def_conf = infer_type_from_default_value(prop.default_value, name=prop.name)
                    if def_t:
                        return InferredType(
                            name=prop.name,
                            inferred_type=def_t,
                            confidence=TypeConfidence.DEFAULT_VALUE,
                            source="default_value",
                        )
                kind = infer_list_or_dict("array", name=prop.name)
                return InferredType(
                    name=prop.name,
                    inferred_type=kind,
                    confidence=TypeConfidence.USAGE_HEURISTIC,
                    source="usage_heuristic",
                )

            mapped = map_php_type_to_rhp(raw, name=prop.name)
            if prop.default_value and prop.default_value.lower() == "null" and mapped and not mapped.endswith("?"):
                mapped = f"{mapped}?"
            return InferredType(
                name=prop.name,
                inferred_type=mapped,
                confidence=TypeConfidence.EXPLICIT,
                source="explicit",
            )

        # 2. Static analysis
        sa_t = loader.find_type(file_path=file_path, symbol_name=prop_sym)
        if sa_t:
            return InferredType(
                name=prop.name,
                inferred_type=sa_t,
                confidence=TypeConfidence.STATIC_ANALYSIS,
                source="static_analysis",
            )

        # 3. Docblock @var
        if docblock:
            var_t = docblock.get_var_type(prop.name)
            if var_t:
                return InferredType(
                    name=prop.name,
                    inferred_type=var_t,
                    confidence=TypeConfidence.DOCBLOCK,
                    source="docblock",
                )

        # 4. AST default value
        if prop.default_value:
            def_t, def_conf = infer_type_from_default_value(prop.default_value, name=prop.name)
            if def_t and def_conf:
                return InferredType(
                    name=prop.name,
                    inferred_type=def_t,
                    confidence=def_conf,
                    source="default_value",
                )

        # 5. Unknown
        return InferredType(
            name=prop.name,
            inferred_type=None,
            confidence=TypeConfidence.UNKNOWN,
            source="unknown",
            needs_llm=True,
        )

    def infer_method(
        self,
        method: PHPMethod,
        class_name: Optional[str] = None,
        body_node: Optional[tree_sitter.Node] = None,
        static_loader: Optional[PHPStanLoader] = None,
        file_path: Optional[str] = None,
    ) -> FunctionTypeInference:
        """Infer parameter and return types for a class method."""
        docblock = DocblockParser.parse(method.docblock)
        params: dict[str, InferredType] = {}

        for p in method.parameters:
            inferred_p = self.infer_parameter(
                p,
                docblock=docblock,
                body_node=body_node,
                static_loader=static_loader,
                class_name=class_name,
                function_name=method.name,
                file_path=file_path,
            )
            params[p.name] = inferred_p

        inferred_ret = self.infer_return_type(
            method.return_type,
            docblock=docblock,
            body_node=body_node,
            static_loader=static_loader,
            class_name=class_name,
            function_name=method.name,
            file_path=file_path,
        )

        return FunctionTypeInference(
            function_name=f"{class_name}::{method.name}" if class_name else method.name,
            parameters=params,
            return_type=inferred_ret,
        )

    def infer_function(
        self,
        func: PHPFunction,
        body_node: Optional[tree_sitter.Node] = None,
        static_loader: Optional[PHPStanLoader] = None,
        file_path: Optional[str] = None,
    ) -> FunctionTypeInference:
        """Infer parameter and return types for a standalone PHP function."""
        docblock = DocblockParser.parse(func.docblock)
        params: dict[str, InferredType] = {}

        for p in func.parameters:
            inferred_p = self.infer_parameter(
                p,
                docblock=docblock,
                body_node=body_node,
                static_loader=static_loader,
                class_name=None,
                function_name=func.name,
                file_path=file_path,
            )
            params[p.name] = inferred_p

        inferred_ret = self.infer_return_type(
            func.return_type,
            docblock=docblock,
            body_node=body_node,
            static_loader=static_loader,
            class_name=None,
            function_name=func.name,
            file_path=file_path,
        )

        return FunctionTypeInference(
            function_name=func.name,
            parameters=params,
            return_type=inferred_ret,
        )

    def infer_class(
        self,
        cls: PHPClass,
        static_loader: Optional[PHPStanLoader] = None,
        file_path: Optional[str] = None,
        body_node_map: Optional[dict[str, tree_sitter.Node]] = None,
    ) -> ClassTypeInference:
        """Infer types for all properties and methods within a class."""
        properties: dict[str, InferredType] = {}
        methods: dict[str, FunctionTypeInference] = {}

        for prop in cls.properties:
            docblock = DocblockParser.parse(prop.docblock)
            inferred_prop = self.infer_property(
                prop,
                docblock=docblock,
                static_loader=static_loader,
                class_name=cls.name,
                file_path=file_path,
            )
            properties[prop.name] = inferred_prop

        for m in cls.methods:
            b_node = body_node_map.get(m.name) if body_node_map else None
            inferred_method = self.infer_method(
                m,
                class_name=cls.name,
                body_node=b_node,
                static_loader=static_loader,
                file_path=file_path,
            )
            methods[m.name] = inferred_method

        return ClassTypeInference(
            class_name=cls.name,
            properties=properties,
            methods=methods,
        )

    def infer_source(
        self,
        source: Union[str, bytes],
        file_path: Optional[str] = None,
        static_loader: Optional[PHPStanLoader] = None,
    ) -> FileTypeInference:
        """Parse source code and perform end-to-end type inference across all symbols."""
        tree = self.parser.parse(source)
        symbols = self.extractor.extract_source(source, file_path=file_path)
        loader = static_loader or self.static_loader

        # Map methods and functions to AST compound_statements
        body_map = self._collect_body_nodes(tree.root_node)

        classes_res: dict[str, ClassTypeInference] = {}
        functions_res: dict[str, FunctionTypeInference] = {}
        low_confidence: list[InferredType] = []

        # Unique classes
        unique_classes = {c.fqcn: c for c in symbols.classes.values()}
        for c in unique_classes.values():
            class_methods_body_map = body_map.get(c.name, {})
            c_inf = self.infer_class(
                c,
                static_loader=loader,
                file_path=file_path,
                body_node_map=class_methods_body_map,
            )
            classes_res[c.name] = c_inf
            for p_inf in c_inf.properties.values():
                if p_inf.needs_llm:
                    low_confidence.append(p_inf)
            for m_inf in c_inf.methods.values():
                for p_inf in m_inf.parameters.values():
                    if p_inf.needs_llm:
                        low_confidence.append(p_inf)
                if m_inf.return_type.needs_llm:
                    low_confidence.append(m_inf.return_type)

        # Unique functions
        unique_funcs = {f.fqfn: f for f in symbols.functions.values()}
        for f in unique_funcs.values():
            fn_body = body_map.get("__root__", {}).get(f.name)
            f_inf = self.infer_function(
                f,
                body_node=fn_body,
                static_loader=loader,
                file_path=file_path,
            )
            functions_res[f.name] = f_inf
            for p_inf in f_inf.parameters.values():
                if p_inf.needs_llm:
                    low_confidence.append(p_inf)
            if f_inf.return_type.needs_llm:
                low_confidence.append(f_inf.return_type)

        return FileTypeInference(
            file_path=file_path,
            classes=classes_res,
            functions=functions_res,
            low_confidence_items=low_confidence,
        )

    def infer_file(
        self,
        file_path: Union[str, Path],
        static_loader: Optional[PHPStanLoader] = None,
    ) -> FileTypeInference:
        """Read a file and run full type inference."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        return self.infer_source(content, file_path=str(path), static_loader=static_loader)

    def _lookup_static_analysis(
        self,
        loader: PHPStanLoader,
        file_path: Optional[str],
        symbol_prefix: Optional[str],
        param_name: str,
    ) -> Optional[str]:
        """Lookup parameter type in static analysis loader under various symbol naming styles."""
        candidates = [param_name]
        if symbol_prefix:
            candidates.insert(0, f"{symbol_prefix}::{param_name}")
            candidates.insert(1, f"{symbol_prefix}::{param_name.lstrip('$')}")

        for sym in candidates:
            found = loader.find_type(file_path=file_path, symbol_name=sym)
            if found:
                return found
        return None

    def _infer_usage_heuristic(
        self,
        body_node: tree_sitter.Node,
        var_name: str,
    ) -> tuple[Optional[str], Optional[TypeConfidence]]:
        """Scan AST body for array usage patterns on a variable.

        E.g.
            - $arr[] = $val  -> implies 'list' (USAGE_HEURISTIC 0.4)
            - $arr['key'] =  -> implies 'dict' (USAGE_HEURISTIC 0.4)
            - array_push($arr, ...) -> implies 'list'
            - array_key_exists(..., $arr) -> implies 'dict'
        """
        clean_name = var_name if var_name.startswith("$") else f"${var_name}"

        # 1. Inspect subscript expressions ($arr[...] ...)
        subscripts = PHPParser.find_nodes_of_type(body_node, "subscript_expression")
        for sub in subscripts:
            children = sub.children
            if not children:
                continue
            target_node = children[0]
            target_text = PHPParser.get_node_text(target_node).strip()
            if target_text == clean_name:
                # If children count is 3: target, '[', ']' -> appending to list!
                if len(children) == 3 and children[1].type == "[" and children[2].type == "]":
                    return "list", TypeConfidence.USAGE_HEURISTIC

                # If child between '[' and ']' is string literal -> associative dict!
                if len(children) >= 4:
                    idx_node = children[2]
                    if idx_node.type in ("string", "encapsed_string"):
                        return "dict", TypeConfidence.USAGE_HEURISTIC
                    if idx_node.type == "integer":
                        return "list", TypeConfidence.USAGE_HEURISTIC

        # 2. Inspect standard PHP function calls targeting $var
        calls = PHPParser.find_call_sites(body_node)
        for call in calls:
            call_text = PHPParser.get_node_text(call)
            if clean_name not in call_text:
                continue
            if re.match(r"^array_push\s*\(", call_text) or re.match(r"^array_pop\s*\(", call_text):
                return "list", TypeConfidence.USAGE_HEURISTIC
            if re.match(r"^array_shift\s*\(", call_text) or re.match(r"^array_unshift\s*\(", call_text):
                return "list", TypeConfidence.USAGE_HEURISTIC
            if re.match(r"^array_keys\s*\(", call_text) or re.match(r"^array_key_exists\s*\(", call_text):
                return "dict", TypeConfidence.USAGE_HEURISTIC
            if re.match(r"^array_values\s*\(", call_text):
                return "dict", TypeConfidence.USAGE_HEURISTIC

        return None, None

    def _infer_from_return_statements(
        self,
        body_node: tree_sitter.Node,
    ) -> Optional[tuple[Optional[str], TypeConfidence, str]]:
        """Inspect all return statements inside a compound_statement node."""
        return_nodes = PHPParser.find_nodes_of_type(body_node, "return_statement")

        # If no returns in the entire body: implicit void
        if not return_nodes:
            return None, TypeConfidence.DEFAULT_VALUE, "implicit_void"

        # Check if all return statements are empty `return;`
        is_all_empty = True
        return_types: set[str] = set()

        for ret in return_nodes:
            # Children: 'return', [expr], ';'
            expr_child = None
            for c in ret.children:
                if c.type not in ("return", ";"):
                    expr_child = c
                    break

            if expr_child is not None:
                is_all_empty = False
                expr_text = PHPParser.get_node_text(expr_child).strip()
                t, _ = infer_type_from_default_value(expr_text)
                if t:
                    return_types.add(t)

        if is_all_empty:
            return None, TypeConfidence.DEFAULT_VALUE, "explicit_void"

        if len(return_types) == 1:
            return next(iter(return_types)), TypeConfidence.DEFAULT_VALUE, "default_value"

        return None

    def _collect_body_nodes(self, root: tree_sitter.Node) -> dict[str, dict[str, tree_sitter.Node]]:
        """Map class/method and standalone functions to their compound_statement AST nodes."""
        result: dict[str, dict[str, tree_sitter.Node]] = {"__root__": {}}

        # Standalone functions
        for fn in PHPParser.find_functions(root):
            name_node = None
            body_node = None
            for c in fn.children:
                if c.type == "name":
                    name_node = c
                elif c.type == "compound_statement":
                    body_node = c
            if name_node and body_node:
                fn_name = PHPParser.get_node_text(name_node)
                result["__root__"][fn_name] = body_node

        # Classes
        for cls in PHPParser.find_class_declarations(root):
            cls_name = ""
            for c in cls.children:
                if c.type == "name":
                    cls_name = PHPParser.get_node_text(c)
            if not cls_name:
                continue

            result[cls_name] = {}
            for m in PHPParser.find_methods(cls):
                m_name = ""
                m_body = None
                for c in m.children:
                    if c.type == "name":
                        m_name = PHPParser.get_node_text(c)
                    elif c.type == "compound_statement":
                        m_body = c
                if m_name and m_body:
                    result[cls_name][m_name] = m_body

        return result
