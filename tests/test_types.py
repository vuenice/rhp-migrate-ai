"""Tests for Layer 3: Static Type Extractor (docblock, static analysis, and inference)."""

import pytest

from rhp_migrate.types.docblock import (
    DocblockParser,
    infer_list_or_dict,
    map_php_type_to_rhp,
)
from rhp_migrate.types.phpstan_loader import PHPStanLoader
from rhp_migrate.types.inference import (
    TypeConfidence,
    TypeInferenceEngine,
    infer_type_from_default_value,
)


class TestDocblockParsing:
    """Tests for parsing PHPDocs above functions, methods, and properties."""

    def test_basic_scalar_types(self):
        assert map_php_type_to_rhp("int") == "int"
        assert map_php_type_to_rhp("integer") == "int"
        assert map_php_type_to_rhp("string") == "string"
        assert map_php_type_to_rhp("bool") == "bool"
        assert map_php_type_to_rhp("boolean") == "bool"
        assert map_php_type_to_rhp("float") == "float"
        assert map_php_type_to_rhp("double") == "float"
        assert map_php_type_to_rhp("real") == "float"
        assert map_php_type_to_rhp("mixed") == "mixed"

    def test_void_return_removal(self):
        # RHP treats omitted return as void, so mapped to None
        assert map_php_type_to_rhp("void") is None
        assert map_php_type_to_rhp("VOID") is None

    def test_nullable_types_postfix(self):
        # ?Type -> Type?
        assert map_php_type_to_rhp("?int") == "int?"
        assert map_php_type_to_rhp("?string") == "string?"
        assert map_php_type_to_rhp("?User") == "User?"
        assert map_php_type_to_rhp("?string[]") == "list<string>?"
        # Union with null: Type|null -> Type?
        assert map_php_type_to_rhp("string|null") == "string?"
        assert map_php_type_to_rhp("null|int") == "int?"
        assert map_php_type_to_rhp("User|null") == "User?"

    def test_union_types_non_null(self):
        assert map_php_type_to_rhp("int|string") == "int|string"
        assert map_php_type_to_rhp("float|int") == "float|int"

    def test_function_docblock_parsing(self):
        doc = """/**
         * Calculates discount for order.
         *
         * @param int $userId The ID of the customer
         * @param float $subtotal Current order subtotal
         * @param ?string $coupon Coupon code if any
         * @return float
         */"""
        parsed = DocblockParser.parse(doc)
        assert parsed is not None
        assert parsed.summary == "Calculates discount for order."
        assert parsed.get_param_type("userId") == "int"
        assert parsed.get_param_type("$subtotal") == "float"
        assert parsed.get_param_type("coupon") == "string?"
        assert parsed.get_return_type() == "float"

    def test_property_docblock_parsing(self):
        doc = """/**
         * @var int[]
         */"""
        parsed = DocblockParser.parse(doc)
        assert parsed is not None
        assert parsed.get_var_type() == "list<int>"

        doc_with_name = """/**
         * @var string $title The book title
         */"""
        parsed_name = DocblockParser.parse(doc_with_name)
        assert parsed_name is not None
        assert parsed_name.get_var_type("title") == "string"
        assert parsed_name.get_var_type("$title") == "string"


class TestListVsDictDiscrimination:
    """Tests verifying discrimination between list and dict across docblocks and heuristics."""

    def test_array_bracket_syntax_is_list(self):
        # T[] is always list
        assert infer_list_or_dict("string[]") == "list"
        assert infer_list_or_dict("int[]") == "list"
        assert infer_list_or_dict("User[]") == "list"
        assert map_php_type_to_rhp("string[]") == "list<string>"
        assert map_php_type_to_rhp("int[]") == "list<int>"
        assert map_php_type_to_rhp("int[]", preserve_generics=False) == "list"

    def test_explicit_list_and_dict_syntax(self):
        assert infer_list_or_dict("list<string>") == "list"
        assert map_php_type_to_rhp("list<string>") == "list<string>"
        assert infer_list_or_dict("dict<string, int>") == "dict"
        assert map_php_type_to_rhp("dict<string, int>") == "dict<string, int>"
        assert infer_list_or_dict("dict") == "dict"

    def test_array_generic_single_arg_is_list(self):
        # array<T> is sequential list of T
        assert infer_list_or_dict("array<string>") == "list"
        assert map_php_type_to_rhp("array<string>") == "list<string>"
        assert map_php_type_to_rhp("array<User>") == "list<User>"

    def test_array_generic_int_key_is_list(self):
        # array<int, T> is sequential list of T
        assert infer_list_or_dict("array<int, string>") == "list"
        assert map_php_type_to_rhp("array<int, string>") == "list<string>"
        assert map_php_type_to_rhp("array<int, User>") == "list<User>"

    def test_array_generic_string_key_is_dict(self):
        # array<string, T> is key-value dictionary
        assert infer_list_or_dict("array<string, int>") == "dict"
        assert map_php_type_to_rhp("array<string, int>") == "dict<string, int>"
        assert map_php_type_to_rhp("array<string, mixed>") == "dict<string, mixed>"

    def test_array_shape_is_dict(self):
        assert infer_list_or_dict("array{id: int, name: string}") == "dict"
        assert map_php_type_to_rhp("array{id: int, name: string}") == "dict"

    def test_bare_array_naming_heuristics(self):
        # Names suggesting associative dictionary
        assert infer_list_or_dict("array", name="$options") == "dict"
        assert infer_list_or_dict("array", name="$config") == "dict"
        assert infer_list_or_dict("array", name="$settings") == "dict"
        assert infer_list_or_dict("array", name="$headers") == "dict"
        assert infer_list_or_dict("array", name="$payload") == "dict"
        assert infer_list_or_dict("array", name="$map") == "dict"
        assert infer_list_or_dict("array", name="$attributes") == "dict"

        # Names suggesting sequential list
        assert infer_list_or_dict("array", name="$items") == "list"
        assert infer_list_or_dict("array", name="$ids") == "list"
        assert infer_list_or_dict("array", name="$names") == "list"
        assert infer_list_or_dict("array", name="$rows") == "list"
        assert infer_list_or_dict("array", name="$users") == "list"
        assert infer_list_or_dict("array", name="$queue") == "list"

    def test_bare_array_description_heuristics(self):
        assert infer_list_or_dict("array", description="associative array of credentials") == "dict"
        assert infer_list_or_dict("array", description="key-value pairs") == "dict"
        assert infer_list_or_dict("array", description="list of user IDs") == "list"
        assert infer_list_or_dict("array", description="sequential collection of items") == "list"

    def test_default_literal_inference_list_vs_dict(self):
        # [1, 2, 3] -> list<int>
        t1, conf1 = infer_type_from_default_value("[1, 2, 3]")
        assert t1 == "list<int>"
        assert conf1 == TypeConfidence.DEFAULT_VALUE

        # ['a' => 1, 'b' => 2] -> dict<string, int>
        t2, conf2 = infer_type_from_default_value("['a' => 1, 'b' => 2]")
        assert t2 == "dict<string, int>"
        assert conf2 == TypeConfidence.DEFAULT_VALUE

        # [] with name heuristic
        t3, conf3 = infer_type_from_default_value("[]", name="$options")
        assert t3 == "dict"
        assert conf3 == TypeConfidence.DEFAULT_VALUE

        t4, conf4 = infer_type_from_default_value("[]", name="$items")
        assert t4 == "list"
        assert conf4 == TypeConfidence.DEFAULT_VALUE


class TestPHPStanLoader:
    """Tests for loading PHPStan / Psalm JSON outputs."""

    def test_load_phpstan_json_messages(self):
        report = """{
            "totals": {"errors": 2, "file_errors": 2},
            "files": {
                "/var/www/src/Product.php": {
                    "errors": 2,
                    "messages": [
                        {
                            "message": "Method App\\\\Product::getPrice() should return int but returns float.",
                            "line": 18
                        },
                        {
                            "message": "Parameter #1 $metadata (array<string, mixed>) of method App\\\\Product::setMeta() does not accept array.",
                            "line": 30
                        }
                    ]
                }
            }
        }"""
        loader = PHPStanLoader()
        entries = loader.load_json(report)
        assert len(entries) == 2

        price_type = loader.find_type(file_path="Product.php", symbol_name=r"App\Product::getPrice")
        assert price_type == "float"

        meta_type = loader.find_type(file_path="Product.php", symbol_name=r"App\Product::setMeta::$metadata")
        assert meta_type == "dict<string, mixed>"

    def test_load_psalm_json_issues(self):
        report = """{
            "error": [
                {
                    "message": "Method Product::getTitle should return string but saw int",
                    "file_name": "src/Product.php",
                    "line_from": 45
                }
            ],
            "warning": []
        }"""
        loader = PHPStanLoader()
        entries = loader.load_json(report)
        assert len(entries) == 1
        found = loader.find_type(file_path="Product.php", symbol_name="Product::getTitle")
        assert found == "int"


class TestTypeInferenceEngine:
    """Tests for combined type inference, confidence scores, and LLM triage."""

    def test_explicit_type_confidence_1_0(self):
        engine = TypeInferenceEngine()
        code = """<?php
        function calculate(int $a, float $b): string {
            return (string)($a + $b);
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["calculate"]
        assert fn.parameters["$a"].confidence == TypeConfidence.EXPLICIT
        assert fn.parameters["$a"].inferred_type == "int"
        assert not fn.parameters["$a"].needs_llm

        assert fn.parameters["$b"].confidence == TypeConfidence.EXPLICIT
        assert fn.parameters["$b"].inferred_type == "float"

        assert fn.return_type.confidence == TypeConfidence.EXPLICIT
        assert fn.return_type.inferred_type == "string"
        assert not fn.needs_llm

    def test_docblock_type_confidence_0_8(self):
        engine = TypeInferenceEngine()
        code = """<?php
        /**
         * @param string[] $names
         * @return bool
         */
        function register($names) {
            return true;
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["register"]
        p = fn.parameters["$names"]
        assert p.confidence == TypeConfidence.DOCBLOCK
        assert p.inferred_type == "list<string>"
        assert not p.needs_llm

        ret = fn.return_type
        assert ret.confidence == TypeConfidence.DOCBLOCK
        assert ret.inferred_type == "bool"
        assert not ret.needs_llm

    def test_default_value_confidence_0_7(self):
        engine = TypeInferenceEngine()
        code = """<?php
        function configure($timeout = 30, $debug = false, $prefix = 'rhp_') {
            return null;
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["configure"]
        assert fn.parameters["$timeout"].confidence == TypeConfidence.DEFAULT_VALUE
        assert fn.parameters["$timeout"].inferred_type == "int"
        assert not fn.parameters["$timeout"].needs_llm

        assert fn.parameters["$debug"].confidence == TypeConfidence.DEFAULT_VALUE
        assert fn.parameters["$debug"].inferred_type == "bool"

        assert fn.parameters["$prefix"].confidence == TypeConfidence.DEFAULT_VALUE
        assert fn.parameters["$prefix"].inferred_type == "string"

    def test_usage_heuristic_confidence_0_4_flagged_for_llm(self):
        engine = TypeInferenceEngine()
        code = """<?php
        function collectData($items, $bag) {
            $items[] = 42;
            $bag['count'] = 1;
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["collectData"]

        p_items = fn.parameters["$items"]
        assert p_items.confidence == TypeConfidence.USAGE_HEURISTIC
        assert p_items.inferred_type == "list"
        assert p_items.needs_llm  # 0.4 < 0.6 -> Flagged for LLM!

        p_bag = fn.parameters["$bag"]
        assert p_bag.confidence == TypeConfidence.USAGE_HEURISTIC
        assert p_bag.inferred_type == "dict"
        assert p_bag.needs_llm  # 0.4 < 0.6 -> Flagged for LLM!

    def test_unknown_type_confidence_0_0_flagged_for_llm(self):
        engine = TypeInferenceEngine()
        code = """<?php
        function mystery($foo, $bar) {
            // No usage clues, no types, no docblocks
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["mystery"]
        p_foo = fn.parameters["$foo"]
        assert p_foo.confidence == TypeConfidence.UNKNOWN
        assert p_foo.inferred_type is None
        assert p_foo.needs_llm
        assert res.needs_llm
        assert len(res.low_confidence_items) >= 2

    def test_static_analysis_integration(self):
        loader = PHPStanLoader()
        loader.load_json("""[
            {
                "file": "test.php",
                "symbol": "OrderService::dispatch::$order",
                "type": "App\\\\Models\\\\Order",
                "kind": "param",
                "confidence": 0.9
            }
        ]""")
        engine = TypeInferenceEngine(static_loader=loader)
        code = """<?php
        class OrderService {
            public function dispatch($order) {
            }
        }
        """
        res = engine.infer_source(code, file_path="test.php")
        param = res.classes["OrderService"].methods["dispatch"].parameters["$order"]
        assert param.confidence == TypeConfidence.STATIC_ANALYSIS
        assert param.inferred_type == "App\\Models\\Order"
        assert not param.needs_llm

    def test_void_omitted_return_handling(self):
        engine = TypeInferenceEngine()
        code = """<?php
        /**
         * @return void
         */
        function execute(): void {
            echo "done";
        }
        """
        res = engine.infer_source(code)
        fn = res.functions["execute"]
        assert fn.return_type.inferred_type is None
        assert fn.return_type.confidence == TypeConfidence.EXPLICIT
        assert not fn.return_type.needs_llm
