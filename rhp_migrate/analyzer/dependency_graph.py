"""Dependency graph analysis and topological migration ordering for PHP projects."""

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union
import networkx as nx

from rhp_migrate.analyzer.symbols import PHPClass, SymbolExtractor, SymbolTable


class ProjectDependencyGraph:
    """Builds and analyzes class and file dependency graphs using networkx.

    Dependencies are directed as: `dependency -> dependent`, such that
    topological sorting yields independent leaf classes first (models, base classes)
    and dependent classes last (services, controllers).
    """

    def __init__(self, extractor: Optional[SymbolExtractor] = None) -> None:
        self.extractor = extractor or SymbolExtractor()
        self.symbol_tables: dict[Path, SymbolTable] = {}
        self.classes_by_fqcn: dict[str, PHPClass] = {}
        self.classes_by_name: dict[str, list[PHPClass]] = defaultdict(list)
        self.files_by_class_fqcn: dict[str, Path] = {}
        self.all_files: set[Path] = set()

        self._class_graph: Optional[nx.DiGraph] = None
        self._file_graph: Optional[nx.DiGraph] = None

    def add_file(self, file_path: Union[str, Path], source: Optional[Union[str, bytes]] = None) -> SymbolTable:
        """Parse and add a PHP file to the dependency graph.

        Args:
            file_path: Path to the PHP file.
            source: Optional source code content. If omitted, reads from file_path.

        Returns:
            Extracted SymbolTable for the file.
        """
        path = Path(file_path).resolve()
        self.all_files.add(path)

        if source is not None:
            table = self.extractor.extract_source(source, file_path=path)
        else:
            table = self.extractor.extract_file(path)

        self.symbol_tables[path] = table

        # Index classes
        for php_class in table.classes.values():
            # Avoid duplicate registration when indexed by both FQCN and short name
            if php_class.fqcn not in self.classes_by_fqcn:
                self.classes_by_fqcn[php_class.fqcn] = php_class
                self.classes_by_name[php_class.name].append(php_class)
                self.files_by_class_fqcn[php_class.fqcn] = path

        # Invalidate cached graphs
        self._class_graph = None
        self._file_graph = None
        return table

    def add_files(self, file_paths: Iterable[Union[str, Path]]) -> None:
        """Add multiple PHP files to the dependency graph."""
        for path in file_paths:
            self.add_file(path)

    def scan_directory(self, root_dir: Union[str, Path], pattern: str = "**/*.php") -> None:
        """Recursively scan a directory and add all matching PHP files."""
        root = Path(root_dir).resolve()
        for php_file in root.glob(pattern):
            if php_file.is_file():
                self.add_file(php_file)

    def resolve_class_reference(self, target_name: str, referencing_class: PHPClass) -> Optional[PHPClass]:
        """Resolve a type or class reference to a project PHPClass if defined in the project."""
        clean = target_name.lstrip("\\")
        # Direct FQCN match
        if clean in self.classes_by_fqcn:
            return self.classes_by_fqcn[clean]

        # Check imports in referencing class
        for imp in referencing_class.imports:
            if imp.effective_name == clean:
                if imp.name in self.classes_by_fqcn:
                    return self.classes_by_fqcn[imp.name]

        # Check current namespace
        if referencing_class.namespace:
            same_ns = f"{referencing_class.namespace}\\{clean}"
            if same_ns in self.classes_by_fqcn:
                return self.classes_by_fqcn[same_ns]

        # Check short name if uniquely known in project
        candidates = self.classes_by_name.get(clean, [])
        if len(candidates) == 1:
            return candidates[0]

        return None

    def build_graphs(self) -> tuple[nx.DiGraph, nx.DiGraph]:
        """Build directed acyclic graphs for project classes and project files.

        Edges are directed `dependency -> dependent`.

        Returns:
            Tuple of (class_graph, file_graph).
        """
        class_graph = nx.DiGraph()
        file_graph = nx.DiGraph()

        # Add all project classes and files as nodes
        for fqcn in self.classes_by_fqcn:
            class_graph.add_node(fqcn)
        for path in self.all_files:
            file_graph.add_node(path)

        for fqcn, php_class in self.classes_by_fqcn.items():
            dependent_file = self.files_by_class_fqcn.get(fqcn)
            dependencies = self._collect_class_dependencies(php_class)

            for dep_class in dependencies:
                dep_fqcn = dep_class.fqcn
                if dep_fqcn != fqcn:
                    class_graph.add_edge(dep_fqcn, fqcn)

                dep_file = self.files_by_class_fqcn.get(dep_fqcn)
                if dep_file and dependent_file and dep_file != dependent_file:
                    file_graph.add_edge(dep_file, dependent_file)

        self._class_graph = class_graph
        self._file_graph = file_graph
        return class_graph, file_graph

    def _collect_class_dependencies(self, php_class: PHPClass) -> list[PHPClass]:
        """Collect all project classes that the given class depends on."""
        deps: dict[str, PHPClass] = {}

        def record(c: Optional[PHPClass]) -> None:
            if c is not None and c.fqcn not in deps:
                deps[c.fqcn] = c

        # 1. Extends parent class
        if php_class.parent_class:
            record(self.resolve_class_reference(php_class.parent_class, php_class))

        # 2. Implements interfaces
        for iface in php_class.interfaces:
            record(self.resolve_class_reference(iface, php_class))

        # 3. Uses traits
        for trait in php_class.traits:
            record(self.resolve_class_reference(trait, php_class))

        # 4. Property type hints
        for prop in php_class.properties:
            if prop.type_hint:
                record(self.resolve_class_reference(prop.type_hint, php_class))

        # 5. Method parameter type hints and return types
        for method in php_class.methods:
            if method.return_type:
                record(self.resolve_class_reference(method.return_type, php_class))
            for param in method.parameters:
                if param.type_hint:
                    record(self.resolve_class_reference(param.type_hint, php_class))

            # 6. Scoped calls and instantiations in methods
            for call in method.calls:
                # E.g. User::find or new User
                if "::" in call:
                    target_scope = call.split("::")[0]
                    record(self.resolve_class_reference(target_scope, php_class))
                elif call.startswith("new "):
                    target_type = call[4:].split("(")[0].strip()
                    record(self.resolve_class_reference(target_type, php_class))

        return list(deps.values())

    def get_class_graph(self) -> nx.DiGraph:
        """Get the class-level dependency graph."""
        if self._class_graph is None:
            self.build_graphs()
        assert self._class_graph is not None
        return self._class_graph

    def get_file_graph(self) -> nx.DiGraph:
        """Get the file-level dependency graph."""
        if self._file_graph is None:
            self.build_graphs()
        assert self._file_graph is not None
        return self._file_graph

    def get_migration_order(self) -> list[Path]:
        """Calculate topological migration ordering for all project files.

        Returns:
            List of Path objects ordered from leaf classes first (no project dependencies)
            to dependent services and controllers last.
        """
        file_graph = self.get_file_graph().copy()

        # Handle cycles if present in legacy code
        if not nx.is_directed_acyclic_graph(file_graph):
            self._break_cycles(file_graph)

        # In-degree 0 nodes come first (leaves with no project dependencies)
        return list(nx.topological_sort(file_graph))

    def get_class_migration_order(self) -> list[str]:
        """Calculate topological migration ordering for all project class FQCNs.

        Returns:
            List of class FQCN strings ordered leaf classes first to dependents last.
        """
        class_graph = self.get_class_graph().copy()

        if not nx.is_directed_acyclic_graph(class_graph):
            self._break_cycles(class_graph)

        return list(nx.topological_sort(class_graph))

    @staticmethod
    def _break_cycles(graph: nx.DiGraph) -> None:
        """Safely break circular dependency cycles to ensure a valid topological sort."""
        while not nx.is_directed_acyclic_graph(graph):
            cycles = list(nx.simple_cycles(graph))
            if not cycles:
                break
            cycle = cycles[0]
            # Remove feedback edge
            u, v = cycle[-1], cycle[0]
            if graph.has_edge(u, v):
                graph.remove_edge(u, v)
