"""Symbol table, AST symbol extraction, and call graph data models for PHP."""

from pathlib import Path
from typing import Optional, Union, Sequence, Set
import networkx as nx
from pydantic import BaseModel, ConfigDict, Field
import tree_sitter

from rhp_migrate.analyzer.parser import PHPParser


class PHPParameter(BaseModel):
    """Represents a parameter in a PHP method or function."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Parameter name including '$' prefix, e.g. '$user'")
    type_hint: Optional[str] = Field(None, description="Declared type hint, e.g. 'string', '?int', or None if untyped")
    default_value: Optional[str] = Field(None, description="Default parameter expression string if present")
    is_variadic: bool = Field(False, description="Whether this is a variadic parameter (...$rest)")
    is_reference: bool = Field(False, description="Whether this parameter is passed by reference (&$param)")

    @property
    def is_untyped(self) -> bool:
        """True if the parameter lacks an explicit type hint."""
        return self.type_hint is None


class PHPProperty(BaseModel):
    """Represents a class property."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Property name including '$' prefix, e.g. '$name'")
    visibility: str = Field("public", description="'public', 'protected', or 'private'")
    type_hint: Optional[str] = Field(None, description="Declared property type hint")
    default_value: Optional[str] = Field(None, description="Default expression value")
    is_static: bool = Field(False, description="Whether the property is static")
    is_readonly: bool = Field(False, description="Whether the property is readonly")
    docblock: Optional[str] = Field(None, description="Associated docblock comment")
    line: int = Field(1, description="1-indexed line number")

    @property
    def is_untyped(self) -> bool:
        """True if the property lacks an explicit type hint."""
        return self.type_hint is None


class PHPMethod(BaseModel):
    """Represents a class, interface, or trait method."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Method name")
    visibility: str = Field("public", description="'public', 'protected', or 'private'")
    parameters: list[PHPParameter] = Field(default_factory=list, description="List of method parameters")
    return_type: Optional[str] = Field(None, description="Declared return type hint")
    is_static: bool = Field(False, description="Whether the method is static")
    is_abstract: bool = Field(False, description="Whether the method is abstract")
    is_final: bool = Field(False, description="Whether the method is final")
    docblock: Optional[str] = Field(None, description="Associated docblock comment")
    line: int = Field(1, description="1-indexed line number")
    calls: list[str] = Field(default_factory=list, description="Target expressions called inside the method body")

    @property
    def untyped_param_count(self) -> int:
        """Count of untyped parameters in this method."""
        return sum(1 for p in self.parameters if p.is_untyped)


class PHPFunction(BaseModel):
    """Represents a standalone or namespace-level PHP function."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Function name")
    namespace: Optional[str] = Field(None, description="Namespace of the function if any")
    parameters: list[PHPParameter] = Field(default_factory=list, description="List of function parameters")
    return_type: Optional[str] = Field(None, description="Declared return type hint")
    docblock: Optional[str] = Field(None, description="Associated docblock comment")
    line: int = Field(1, description="1-indexed line number")
    calls: list[str] = Field(default_factory=list, description="Target expressions called inside the function body")

    @property
    def untyped_param_count(self) -> int:
        """Count of untyped parameters in this function."""
        return sum(1 for p in self.parameters if p.is_untyped)

    @property
    def fqfn(self) -> str:
        """Fully-qualified function name."""
        return f"{self.namespace}\\{self.name}" if self.namespace else self.name


class PHPUseImport(BaseModel):
    """Represents a namespace import (use statement)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Imported target name, e.g. 'App\\Models\\User'")
    alias: Optional[str] = Field(None, description="Alias name if aliased with 'as'")
    is_function: bool = Field(False, description="True for 'use function ...'")
    is_const: bool = Field(False, description="True for 'use const ...'")

    @property
    def effective_name(self) -> str:
        """Name used in the file to reference the import."""
        if self.alias:
            return self.alias
        return self.name.split("\\")[-1]


class PHPClass(BaseModel):
    """Represents a PHP class, interface, trait, or enum."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Short class name")
    namespace: Optional[str] = Field(None, description="Namespace of the class")
    parent_class: Optional[str] = Field(None, description="Parent class name from 'extends'")
    interfaces: list[str] = Field(default_factory=list, description="Implemented interface names")
    traits: list[str] = Field(default_factory=list, description="Used trait names")
    methods: list[PHPMethod] = Field(default_factory=list, description="Methods defined in this class")
    properties: list[PHPProperty] = Field(default_factory=list, description="Properties defined in this class")
    imports: list[PHPUseImport] = Field(default_factory=list, description="Use imports available in this class file")
    docblock: Optional[str] = Field(None, description="Associated docblock comment")
    is_abstract: bool = Field(False, description="Whether the class is abstract")
    is_final: bool = Field(False, description="Whether the class is final")
    is_interface: bool = Field(False, description="Whether this is an interface declaration")
    is_trait: bool = Field(False, description="Whether this is a trait declaration")
    is_enum: bool = Field(False, description="Whether this is an enum declaration")
    file_path: Optional[Path] = Field(None, description="Path to the file where class is declared")
    line: int = Field(1, description="1-indexed line number")

    @property
    def fqcn(self) -> str:
        """Fully-qualified class name."""
        return f"{self.namespace}\\{self.name}" if self.namespace else self.name

    @property
    def untyped_param_count(self) -> int:
        """Total count of untyped parameters across all methods in this class."""
        return sum(m.untyped_param_count for m in self.methods)

    def get_method(self, name: str) -> Optional[PHPMethod]:
        """Retrieve method by name."""
        for m in self.methods:
            if m.name == name:
                return m
        return None

    def get_property(self, name: str) -> Optional[PHPProperty]:
        """Retrieve property by name (with or without '$')."""
        normalized = name if name.startswith("$") else f"${name}"
        for p in self.properties:
            if p.name == normalized:
                return p
        return None


class CallGraph:
    """Directed call graph tracking callers and callees."""

    def __init__(self) -> None:
        self._caller_to_callees: dict[str, set[str]] = {}
        self._callee_to_callers: dict[str, set[str]] = {}

    def add_call(self, caller: str, callee: str) -> None:
        """Record a call from caller to callee."""
        self._caller_to_callees.setdefault(caller, set()).add(callee)
        self._callee_to_callers.setdefault(callee, set()).add(caller)

    def get_callees(self, caller: str) -> set[str]:
        """Get all callees invoked by caller."""
        return self._caller_to_callees.get(caller, set()).copy()

    def get_callers(self, callee: str) -> set[str]:
        """Get all callers that invoke callee."""
        return self._callee_to_callers.get(callee, set()).copy()

    def all_callers(self) -> set[str]:
        """Return all callers present in the graph."""
        return set(self._caller_to_callees.keys())

    def all_callees(self) -> set[str]:
        """Return all callees present in the graph."""
        return set(self._callee_to_callers.keys())

    def to_dict(self) -> dict[str, list[str]]:
        """Export call graph as a dictionary mapping caller to sorted list of callees."""
        return {caller: sorted(callees) for caller, callees in self._caller_to_callees.items()}

    def to_networkx(self) -> nx.DiGraph:
        """Convert call graph to a networkx DiGraph."""
        dg = nx.DiGraph()
        for caller, callees in self._caller_to_callees.items():
            dg.add_node(caller)
            for callee in callees:
                dg.add_node(callee)
                dg.add_edge(caller, callee)
        return dg


class SymbolTable:
    """Symbol table containing all extracted classes, functions, imports, and call graph."""

    def __init__(self) -> None:
        self.classes: dict[str, PHPClass] = {}
        self.functions: dict[str, PHPFunction] = {}
        self.imports: list[PHPUseImport] = []
        self.call_graph: CallGraph = CallGraph()

    def add_class(self, php_class: PHPClass) -> None:
        """Register a PHP class by FQCN and short name."""
        self.classes[php_class.fqcn] = php_class
        if php_class.name not in self.classes:
            self.classes[php_class.name] = php_class

    def add_function(self, php_func: PHPFunction) -> None:
        """Register a PHP function by FQFN and short name."""
        self.functions[php_func.fqfn] = php_func
        if php_func.name not in self.functions:
            self.functions[php_func.name] = php_func

    def get_class(self, name: str) -> Optional[PHPClass]:
        """Lookup class by FQCN or short name."""
        clean = name.lstrip("\\")
        return self.classes.get(clean) or self.classes.get(name)

    @property
    def total_untyped_params(self) -> int:
        """Total untyped parameters across all classes and functions."""
        # Sum from unique classes by FQCN
        unique_classes = {c.fqcn: c for c in self.classes.values()}.values()
        unique_funcs = {f.fqfn: f for f in self.functions.values()}.values()
        return sum(c.untyped_param_count for c in unique_classes) + sum(f.untyped_param_count for f in unique_funcs)


class SymbolExtractor:
    """Extracts symbols, declarations, and call sites from PHP ASTs."""

    def __init__(self, parser: Optional[PHPParser] = None) -> None:
        self.parser = parser or PHPParser()

    def extract_source(
        self,
        source: Union[str, bytes],
        file_path: Optional[Union[str, Path]] = None,
    ) -> SymbolTable:
        """Parse source code and extract all symbols and call graph."""
        tree = self.parser.parse(source)
        resolved_path = Path(file_path) if file_path else None
        return self._extract_from_tree(tree, resolved_path)

    def extract_file(self, file_path: Union[str, Path]) -> SymbolTable:
        """Read and parse a PHP file and extract all symbols and call graph."""
        path = Path(file_path)
        tree = self.parser.parse_file(path)
        return self._extract_from_tree(tree, path)

    def _extract_from_tree(
        self,
        tree: tree_sitter.Tree,
        file_path: Optional[Path],
    ) -> SymbolTable:
        symbols = SymbolTable()
        root = tree.root_node

        self._process_container(root, current_namespace=None, symbols=symbols, file_path=file_path)
        return symbols

    def _process_container(
        self,
        container_node: tree_sitter.Node,
        current_namespace: Optional[str],
        symbols: SymbolTable,
        file_path: Optional[Path],
    ) -> None:
        """Process nodes in a container (program or compound_statement)."""
        active_namespace = current_namespace
        active_imports: list[PHPUseImport] = []

        for child in container_node.children:
            if child.type == "namespace_definition":
                ns_name = None
                has_compound = False
                for sub in child.children:
                    if sub.type == "namespace_name":
                        ns_name = PHPParser.get_node_text(sub)
                    elif sub.type == "compound_statement":
                        has_compound = True
                        self._process_container(
                            sub,
                            current_namespace=ns_name,
                            symbols=symbols,
                            file_path=file_path,
                        )

                if not has_compound and ns_name is not None:
                    active_namespace = ns_name

            elif child.type == "namespace_use_declaration":
                extracted_imports = self._extract_use_declarations(child)
                active_imports.extend(extracted_imports)
                symbols.imports.extend(extracted_imports)

            elif child.type in ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"):
                php_class = self._extract_class(
                    child,
                    namespace=active_namespace,
                    imports=list(active_imports),
                    file_path=file_path,
                    call_graph=symbols.call_graph,
                )
                if php_class:
                    symbols.add_class(php_class)

            elif child.type == "function_definition":
                php_func = self._extract_function(
                    child,
                    namespace=active_namespace,
                    call_graph=symbols.call_graph,
                )
                if php_func:
                    symbols.add_function(php_func)

    def _extract_use_declarations(self, use_node: tree_sitter.Node) -> list[PHPUseImport]:
        """Extract all imports from a namespace_use_declaration node."""
        imports: list[PHPUseImport] = []
        is_fn = False
        is_cst = False

        for c in use_node.children:
            if c.type == "function":
                is_fn = True
            elif c.type == "const":
                is_cst = True

        prefix = ""
        for c in use_node.children:
            if c.type == "namespace_name":
                prefix = PHPParser.get_node_text(c)
            elif c.type == "namespace_use_clause":
                imp = self._parse_use_clause(c, prefix, is_fn, is_cst)
                if imp:
                    imports.append(imp)
            elif c.type == "namespace_use_group":
                for sub in c.children:
                    if sub.type == "namespace_use_clause":
                        imp = self._parse_use_clause(sub, prefix, is_fn, is_cst)
                        if imp:
                            imports.append(imp)

        return imports

    def _parse_use_clause(
        self,
        clause_node: tree_sitter.Node,
        prefix: str,
        is_function: bool,
        is_const: bool,
    ) -> Optional[PHPUseImport]:
        name_node = None
        alias_node = None
        has_as = False

        for c in clause_node.children:
            if c.type in ("name", "qualified_name"):
                if not has_as and name_node is None:
                    name_node = c
                elif has_as and alias_node is None:
                    alias_node = c
            elif c.type == "as":
                has_as = True

        if not name_node:
            return None

        target = PHPParser.get_node_text(name_node)
        if prefix:
            target = f"{prefix}\\{target}"

        alias = PHPParser.get_node_text(alias_node) if alias_node else None
        return PHPUseImport(
            name=target,
            alias=alias,
            is_function=is_function,
            is_const=is_const,
        )

    def _extract_class(
        self,
        class_node: tree_sitter.Node,
        namespace: Optional[str],
        imports: list[PHPUseImport],
        file_path: Optional[Path],
        call_graph: CallGraph,
    ) -> Optional[PHPClass]:
        name = ""
        parent_class = None
        interfaces: list[str] = []
        traits: list[str] = []
        methods: list[PHPMethod] = []
        properties: list[PHPProperty] = []
        is_abstract = False
        is_final = False

        is_interface = class_node.type == "interface_declaration"
        is_trait = class_node.type == "trait_declaration"
        is_enum = class_node.type == "enum_declaration"

        for child in class_node.children:
            if child.type == "name":
                name = PHPParser.get_node_text(child)
            elif child.type == "abstract_modifier":
                is_abstract = True
            elif child.type == "final_modifier":
                is_final = True
            elif child.type == "base_clause":
                for sub in child.children:
                    if sub.type in ("name", "qualified_name"):
                        parent_class = PHPParser.get_node_text(sub)
            elif child.type == "class_interface_clause":
                for sub in child.children:
                    if sub.type in ("name", "qualified_name"):
                        interfaces.append(PHPParser.get_node_text(sub))
            elif child.type == "declaration_list":
                self._extract_declaration_list(
                    child,
                    traits=traits,
                    methods=methods,
                    properties=properties,
                    class_name=name,
                    namespace=namespace,
                    call_graph=call_graph,
                )

        if not name:
            return None

        docblock = PHPParser.extract_docblock(class_node)
        line = class_node.start_point.row + 1

        return PHPClass(
            name=name,
            namespace=namespace,
            parent_class=parent_class,
            interfaces=interfaces,
            traits=traits,
            methods=methods,
            properties=properties,
            imports=imports,
            docblock=docblock,
            is_abstract=is_abstract,
            is_final=is_final,
            is_interface=is_interface,
            is_trait=is_trait,
            is_enum=is_enum,
            file_path=file_path,
            line=line,
        )

    def _extract_declaration_list(
        self,
        list_node: tree_sitter.Node,
        traits: list[str],
        methods: list[PHPMethod],
        properties: list[PHPProperty],
        class_name: str,
        namespace: Optional[str],
        call_graph: CallGraph,
    ) -> None:
        fqcn = f"{namespace}\\{class_name}" if namespace else class_name

        for child in list_node.children:
            if child.type == "use_declaration":
                # Trait use
                for sub in child.children:
                    if sub.type in ("name", "qualified_name"):
                        traits.append(PHPParser.get_node_text(sub))

            elif child.type == "property_declaration":
                props = self._extract_properties(child)
                properties.extend(props)

            elif child.type == "method_declaration":
                method = self._extract_method(child, fqcn=fqcn, call_graph=call_graph)
                if method:
                    methods.append(method)

    def _extract_properties(self, prop_node: tree_sitter.Node) -> list[PHPProperty]:
        visibility = "public"
        is_static = False
        is_readonly = False
        type_hint = None
        docblock = PHPParser.extract_docblock(prop_node)
        line = prop_node.start_point.row + 1

        elements: list[tree_sitter.Node] = []
        for child in prop_node.children:
            if child.type == "visibility_modifier":
                visibility = PHPParser.get_node_text(child)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "readonly_modifier":
                is_readonly = True
            elif child.type in (
                "primitive_type",
                "named_type",
                "optional_type",
                "union_type",
                "intersection_type",
                "disjunctive_normal_form_type",
            ):
                type_hint = PHPParser.get_node_text(child)
            elif child.type == "property_element":
                elements.append(child)

        results: list[PHPProperty] = []
        for el in elements:
            prop_name = ""
            default_val = None
            is_eq = False
            for sub in el.children:
                if sub.type == "variable_name":
                    prop_name = PHPParser.get_node_text(sub)
                elif sub.type == "=":
                    is_eq = True
                elif is_eq and default_val is None:
                    default_val = PHPParser.get_node_text(sub)

            if prop_name:
                results.append(
                    PHPProperty(
                        name=prop_name,
                        visibility=visibility,
                        type_hint=type_hint,
                        default_value=default_val,
                        is_static=is_static,
                        is_readonly=is_readonly,
                        docblock=docblock,
                        line=line,
                    )
                )

        return results

    def _extract_method(
        self,
        method_node: tree_sitter.Node,
        fqcn: str,
        call_graph: CallGraph,
    ) -> Optional[PHPMethod]:
        name = ""
        visibility = "public"
        is_static = False
        is_abstract = False
        is_final = False
        return_type = None
        parameters: list[PHPParameter] = []
        calls: list[str] = []
        line = method_node.start_point.row + 1
        docblock = PHPParser.extract_docblock(method_node)

        is_colon = False
        for child in method_node.children:
            if child.type == "name":
                name = PHPParser.get_node_text(child)
            elif child.type == "visibility_modifier":
                visibility = PHPParser.get_node_text(child)
            elif child.type == "static_modifier":
                is_static = True
            elif child.type == "abstract_modifier":
                is_abstract = True
            elif child.type == "final_modifier":
                is_final = True
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child)
            elif child.type == ":":
                is_colon = True
            elif is_colon and return_type is None and child.type not in (";", "{", "compound_statement"):
                return_type = PHPParser.get_node_text(child)
            elif child.type == "compound_statement":
                caller_sig = f"{fqcn}::{name}"
                calls = self._extract_calls_from_body(child, caller_sig=caller_sig, call_graph=call_graph)

        if not name:
            return None

        return PHPMethod(
            name=name,
            visibility=visibility,
            parameters=parameters,
            return_type=return_type,
            is_static=is_static,
            is_abstract=is_abstract,
            is_final=is_final,
            docblock=docblock,
            line=line,
            calls=calls,
        )

    def _extract_function(
        self,
        func_node: tree_sitter.Node,
        namespace: Optional[str],
        call_graph: CallGraph,
    ) -> Optional[PHPFunction]:
        name = ""
        return_type = None
        parameters: list[PHPParameter] = []
        calls: list[str] = []
        line = func_node.start_point.row + 1
        docblock = PHPParser.extract_docblock(func_node)

        is_colon = False
        for child in func_node.children:
            if child.type == "name":
                name = PHPParser.get_node_text(child)
            elif child.type == "formal_parameters":
                parameters = self._extract_parameters(child)
            elif child.type == ":":
                is_colon = True
            elif is_colon and return_type is None and child.type not in (";", "{", "compound_statement"):
                return_type = PHPParser.get_node_text(child)
            elif child.type == "compound_statement":
                caller_sig = f"{namespace}\\{name}" if namespace else name
                calls = self._extract_calls_from_body(child, caller_sig=caller_sig, call_graph=call_graph)

        if not name:
            return None

        return PHPFunction(
            name=name,
            namespace=namespace,
            parameters=parameters,
            return_type=return_type,
            docblock=docblock,
            line=line,
            calls=calls,
        )

    def _extract_parameters(self, params_node: tree_sitter.Node) -> list[PHPParameter]:
        results: list[PHPParameter] = []
        for child in params_node.children:
            if "parameter" in child.type:
                param = self._extract_single_parameter(child)
                if param:
                    results.append(param)
        return results

    def _extract_single_parameter(self, param_node: tree_sitter.Node) -> Optional[PHPParameter]:
        name = ""
        type_hint = None
        default_val = None
        is_variadic = param_node.type == "variadic_parameter"
        is_reference = False

        is_eq = False
        for child in param_node.children:
            if child.type == "...":
                is_variadic = True
            elif child.type in ("reference_modifier", "&"):
                is_reference = True
            elif child.type in (
                "primitive_type",
                "named_type",
                "optional_type",
                "union_type",
                "intersection_type",
                "disjunctive_normal_form_type",
                "bottom_type",
            ):
                type_hint = PHPParser.get_node_text(child)
            elif child.type == "variable_name":
                name = PHPParser.get_node_text(child)
            elif child.type == "=":
                is_eq = True
            elif is_eq and default_val is None:
                default_val = PHPParser.get_node_text(child)

        if not name:
            return None

        return PHPParameter(
            name=name,
            type_hint=type_hint,
            default_value=default_val,
            is_variadic=is_variadic,
            is_reference=is_reference,
        )

    def _extract_calls_from_body(
        self,
        body_node: tree_sitter.Node,
        caller_sig: str,
        call_graph: CallGraph,
    ) -> list[str]:
        calls: list[str] = []
        call_nodes = PHPParser.find_call_sites(body_node)

        for call_node in call_nodes:
            callee_str = self._resolve_call_target(call_node)
            if callee_str:
                calls.append(callee_str)
                call_graph.add_call(caller_sig, callee_str)

        return calls

    def _resolve_call_target(self, call_node: tree_sitter.Node) -> Optional[str]:
        """Convert a call expression AST node into a target descriptor string."""
        if call_node.type == "function_call_expression":
            for child in call_node.children:
                if child.type in ("name", "qualified_name"):
                    return PHPParser.get_node_text(child)
            return None

        if call_node.type == "scoped_call_expression":
            scope = None
            method = None
            for child in call_node.children:
                if child.type in ("name", "qualified_name", "relative_scope"):
                    if scope is None:
                        scope = PHPParser.get_node_text(child)
                    elif method is None:
                        method = PHPParser.get_node_text(child)
            if scope and method:
                return f"{scope}::{method}"
            return None

        if call_node.type in ("member_call_expression", "nullsafe_member_call_expression"):
            arrow = "?->" if call_node.type == "nullsafe_member_call_expression" else "->"
            obj = None
            member = None
            for child in call_node.children:
                if child.type in ("variable_name", "member_call_expression", "function_call_expression"):
                    if obj is None:
                        obj = PHPParser.get_node_text(child)
                elif child.type == "name":
                    member = PHPParser.get_node_text(child)
            if obj and member:
                return f"{obj}{arrow}{member}"
            if member:
                return f"{arrow}{member}"
            return None

        return None
