"""Tree-sitter PHP parser wrapper and AST traversal utilities."""

from pathlib import Path
from typing import Iterator, Optional, Sequence, Set, Union
import tree_sitter
import tree_sitter_php


class PHPParser:
    """Wrapper around tree-sitter-php with AST navigation and node extraction helpers."""

    def __init__(self) -> None:
        self._language = tree_sitter.Language(tree_sitter_php.language_php())
        self._language_only = tree_sitter.Language(tree_sitter_php.language_php_only())
        self._parser = tree_sitter.Parser(self._language)
        self._parser_only = tree_sitter.Parser(self._language_only)

    def parse(self, source: Union[str, bytes]) -> tree_sitter.Tree:
        """Parse PHP source code into a tree-sitter Tree.

        Automatically selects standard PHP parser (for files with <?php tags)
        or PHP-only parser (for tag-less PHP code snippets).

        Args:
            source: PHP source code as string or bytes.

        Returns:
            tree_sitter.Tree representing the parsed AST.
        """
        source_bytes = source.encode("utf-8") if isinstance(source, str) else source
        # If no PHP opening tag is present, parse directly as PHP statements
        if b"<?" not in source_bytes:
            return self._parser_only.parse(source_bytes)
        return self._parser.parse(source_bytes)

    def parse_file(self, file_path: Union[str, Path]) -> tree_sitter.Tree:
        """Read and parse a PHP file.

        Args:
            file_path: Path to the PHP file.

        Returns:
            tree_sitter.Tree representing the parsed AST.
        """
        path = Path(file_path)
        content = path.read_bytes()
        return self.parse(content)

    @staticmethod
    def get_node_text(node: tree_sitter.Node) -> str:
        """Extract decoded UTF-8 text from a node."""
        if node.text is None:
            return ""
        return node.text.decode("utf-8", errors="replace")

    @classmethod
    def traverse(cls, root: tree_sitter.Node) -> Iterator[tree_sitter.Node]:
        """Perform a depth-first traversal of the AST starting from root.

        Yields:
            tree_sitter.Node instances in pre-order traversal.
        """
        stack = [root]
        while stack:
            curr = stack.pop()
            yield curr
            for child in reversed(curr.children):
                stack.append(child)

    @classmethod
    def find_nodes_of_type(
        cls,
        root: tree_sitter.Node,
        types: Union[str, Set[str], Sequence[str]],
    ) -> list[tree_sitter.Node]:
        """Find all descendant nodes matching one or more node types.

        Args:
            root: Root node to search under.
            types: Single type name or collection of type names.

        Returns:
            List of matching tree-sitter Nodes.
        """
        match_types = {types} if isinstance(types, str) else set(types)
        return [node for node in cls.traverse(root) if node.type in match_types]

    @classmethod
    def extract_docblock(cls, node: tree_sitter.Node) -> Optional[str]:
        """Extract docblock comment (/** ... */) preceding an AST declaration node.

        Args:
            node: Declaration node (class, method, property, function).

        Returns:
            String containing the docblock text if found, or None.
        """
        curr = node.prev_named_sibling
        while curr is not None and curr.type == "comment":
            text = cls.get_node_text(curr).strip()
            if text.startswith("/**"):
                return text
            curr = curr.prev_named_sibling
        return None

    @classmethod
    def find_class_declarations(cls, root: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find class declarations within an AST node."""
        return cls.find_nodes_of_type(root, "class_declaration")

    @classmethod
    def find_interface_declarations(cls, root: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find interface declarations within an AST node."""
        return cls.find_nodes_of_type(root, "interface_declaration")

    @classmethod
    def find_trait_declarations(cls, root: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find trait declarations within an AST node."""
        return cls.find_nodes_of_type(root, "trait_declaration")

    @classmethod
    def find_enum_declarations(cls, root: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find enum declarations within an AST node."""
        return cls.find_nodes_of_type(root, "enum_declaration")

    @classmethod
    def find_methods(cls, class_or_body_node: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find method declarations directly within a class, trait, or interface body."""
        # Check if node is the body (declaration_list) or class/interface/trait
        body = class_or_body_node
        if body.type != "declaration_list":
            for child in body.children:
                if child.type == "declaration_list":
                    body = child
                    break
        if body.type != "declaration_list":
            return []
        return [c for c in body.children if c.type == "method_declaration"]

    @classmethod
    def find_properties(cls, class_or_body_node: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find property declarations directly within a class or trait body."""
        body = class_or_body_node
        if body.type != "declaration_list":
            for child in body.children:
                if child.type == "declaration_list":
                    body = child
                    break
        if body.type != "declaration_list":
            return []
        return [c for c in body.children if c.type == "property_declaration"]

    @classmethod
    def find_functions(cls, root: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find top-level or namespace-level function definitions."""
        return cls.find_nodes_of_type(root, "function_definition")

    @classmethod
    def find_call_sites(cls, node: tree_sitter.Node) -> list[tree_sitter.Node]:
        """Find function, method, static, and nullsafe call sites within a node.

        Includes:
            - function_call_expression
            - scoped_call_expression (e.g. Class::method())
            - member_call_expression (e.g. $obj->method())
            - nullsafe_member_call_expression (e.g. $obj?->method())
        """
        call_types = {
            "function_call_expression",
            "scoped_call_expression",
            "member_call_expression",
            "nullsafe_member_call_expression",
        }
        return cls.find_nodes_of_type(node, call_types)
