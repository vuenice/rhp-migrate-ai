"""Unit tests for Layer 2: AST parser, symbols, unsupported scanner, dependency graph, and readiness analyzer."""

from pathlib import Path
import pytest
import networkx as nx

from rhp_migrate.analyzer import (
    AnalysisReport,
    CallGraph,
    DiagnosticSeverity,
    FileReport,
    PHPClass,
    PHPMethod,
    PHPParameter,
    PHPParser,
    PHPProperty,
    PHPUseImport,
    ProjectDependencyGraph,
    ReadinessAnalyzer,
    SymbolExtractor,
    SymbolTable,
    UnsupportedFeature,
    UnsupportedFinding,
    UnsupportedScanner,
    calculate_readiness_score,
)


# ============================================================================
# Task 1: Parser Tests
# ============================================================================

def test_parser_with_and_without_php_tags():
    """Verify parser handles code with and without <?php tags."""
    parser = PHPParser()

    with_tag = "<?php class WithTag {}"
    without_tag = "class WithoutTag {}"

    tree1 = parser.parse(with_tag)
    tree2 = parser.parse(without_tag)

    classes1 = parser.find_class_declarations(tree1.root_node)
    classes2 = parser.find_class_declarations(tree2.root_node)

    assert len(classes1) == 1
    assert len(classes2) == 1
    assert PHPParser.get_node_text(classes1[0]).startswith("class WithTag")
    assert PHPParser.get_node_text(classes2[0]).startswith("class WithoutTag")


def test_parser_docblock_extraction():
    """Verify extraction of docblock comments for class, method, property, function."""
    parser = PHPParser()
    code = r"""<?php
/**
 * Class docblock description.
 */
class SampleDoc {
    /**
     * Property docblock.
     */
    public string $title;

    /**
     * Method docblock.
     */
    public function render(): void {}
}

/**
 * Function docblock.
 */
function standalone(): void {}
"""
    tree = parser.parse(code)
    classes = parser.find_class_declarations(tree.root_node)
    assert len(classes) == 1
    cls_doc = parser.extract_docblock(classes[0])
    assert cls_doc is not None
    assert "Class docblock description." in cls_doc

    props = parser.find_properties(classes[0])
    assert len(props) == 1
    prop_doc = parser.extract_docblock(props[0])
    assert prop_doc is not None
    assert "Property docblock." in prop_doc

    methods = parser.find_methods(classes[0])
    assert len(methods) == 1
    m_doc = parser.extract_docblock(methods[0])
    assert m_doc is not None
    assert "Method docblock." in m_doc

    funcs = parser.find_functions(tree.root_node)
    assert len(funcs) == 1
    fn_doc = parser.extract_docblock(funcs[0])
    assert fn_doc is not None
    assert "Function docblock." in fn_doc


def test_parser_find_call_sites():
    """Verify call sites discovery for various call expressions."""
    parser = PHPParser()
    code = r"""<?php
function testCalls($obj) {
    globalFn();
    Scope::staticMethod();
    $obj->memberMethod();
    $obj?->nullsafeMethod();
}
"""
    tree = parser.parse(code)
    calls = parser.find_call_sites(tree.root_node)
    call_texts = [parser.get_node_text(c) for c in calls]

    assert any("globalFn()" in t for t in call_texts)
    assert any("Scope::staticMethod()" in t for t in call_texts)
    assert any("$obj->memberMethod()" in t for t in call_texts)
    assert any("$obj?->nullsafeMethod()" in t for t in call_texts)


# ============================================================================
# Task 2: Symbols and Call Graph Tests
# ============================================================================

def test_symbols_extraction_full():
    """Verify extraction of namespace, imports, class, inheritance, interfaces, traits, and members."""
    extractor = SymbolExtractor()
    code = r"""<?php
namespace App\Services;

use App\Models\User;
use App\Models\Post as Article;
use App\Contracts\LogInterface;
use App\Traits\Timestampable;

/**
 * Service implementation.
 */
class PostService extends BaseService implements LogInterface {
    use Timestampable;

    private ?Article $article = null;
    public string $status;
    protected $untypedProp;

    public function __construct(Article $article, $untypedParam) {
        $this->article = $article;
    }

    public function publish(int $id, string ...$options): bool {
        User::notify($id);
        $this->helper();
        return true;
    }

    private function helper(): void {}
}
"""
    symbols = extractor.extract_source(code)

    # Imports
    assert len(symbols.imports) == 4
    imp_names = {i.name for i in symbols.imports}
    assert r"App\Models\User" in imp_names
    assert r"App\Models\Post" in imp_names
    alias_post = next(i for i in symbols.imports if i.name == r"App\Models\Post")
    assert alias_post.alias == "Article"
    assert alias_post.effective_name == "Article"

    # Class
    cls = symbols.get_class(r"App\Services\PostService")
    assert cls is not None
    assert cls.name == "PostService"
    assert cls.namespace == r"App\Services"
    assert cls.fqcn == r"App\Services\PostService"
    assert cls.parent_class == "BaseService"
    assert cls.interfaces == ["LogInterface"]
    assert cls.traits == ["Timestampable"]

    # Properties
    assert len(cls.properties) == 3
    p1 = cls.get_property("article")
    assert p1 is not None and p1.type_hint == "?Article" and p1.visibility == "private"
    p2 = cls.get_property("status")
    assert p2 is not None and p2.type_hint == "string" and p2.visibility == "public"
    p3 = cls.get_property("untypedProp")
    assert p3 is not None and p3.is_untyped and p3.visibility == "protected"

    # Methods
    assert len(cls.methods) == 3
    m_ctor = cls.get_method("__construct")
    assert m_ctor is not None
    assert len(m_ctor.parameters) == 2
    assert m_ctor.parameters[0].name == "$article"
    assert m_ctor.parameters[0].type_hint == "Article"
    assert not m_ctor.parameters[0].is_untyped
    assert m_ctor.parameters[1].name == "$untypedParam"
    assert m_ctor.parameters[1].is_untyped
    assert m_ctor.untyped_param_count == 1

    m_pub = cls.get_method("publish")
    assert m_pub is not None
    assert m_pub.return_type == "bool"
    assert len(m_pub.parameters) == 2
    assert m_pub.parameters[1].is_variadic

    # Untyped parameter counts
    assert cls.untyped_param_count == 1
    assert symbols.total_untyped_params == 1

    # Call graph
    cg = symbols.call_graph
    publish_sig = r"App\Services\PostService::publish"
    callees = cg.get_callees(publish_sig)
    assert "User::notify" in callees
    assert "$this->helper" in callees


def test_call_graph_networkx_conversion():
    """Verify CallGraph conversion to networkx DiGraph."""
    cg = CallGraph()
    cg.add_call("A::foo", "B::bar")
    cg.add_call("B::bar", "C::baz")

    dg = cg.to_networkx()
    assert isinstance(dg, nx.DiGraph)
    assert dg.number_of_nodes() == 3
    assert dg.number_of_edges() == 2
    assert list(nx.topological_sort(dg)) == ["A::foo", "B::bar", "C::baz"]


# ============================================================================
# Task 3: Unsupported Scanner Tests
# ============================================================================

def test_unsupported_scanner_reflection():
    """Verify detection of Reflection API usage."""
    scanner = UnsupportedScanner()
    code = r"""<?php
use ReflectionClass;

function inspect(\ReflectionMethod $rm) {
    $rc = new \ReflectionClass('User');
    ReflectionClass::export('User');
    $isRef = $rm instanceof ReflectionMethod;
}
"""
    findings = scanner.scan_source(code)
    reflection_findings = [f for f in findings if f.feature == UnsupportedFeature.REFLECTION]
    assert len(reflection_findings) >= 4
    for rf in reflection_findings:
        assert rf.rule_id == "RHP-UNSUP-001"
        assert "Reflection" in rf.node_text


def test_unsupported_scanner_output_buffering():
    """Verify detection of output buffering functions."""
    scanner = UnsupportedScanner()
    code = r"""<?php
ob_start();
$content = ob_get_clean();
ob_end_flush();
ob_get_contents();
"""
    findings = scanner.scan_source(code)
    ob_findings = [f for f in findings if f.feature == UnsupportedFeature.OUTPUT_BUFFERING]
    assert len(ob_findings) == 4
    for ob in ob_findings:
        assert ob.rule_id == "RHP-UNSUP-002"
        assert "ob_" in ob.message


def test_unsupported_scanner_dynamic_constructs():
    """Verify detection of eval(), $$var, dynamic includes, and static include acceptance."""
    scanner = UnsupportedScanner()
    code = r"""<?php
eval('$x = 10;');
$$dynamicVar = 'hello';
include $runtimePath;
require $baseDir . '/file.php';
include_once "templates/{$name}.php";

// Static includes must NOT be flagged as dynamic:
include 'static/header.php';
require "static/footer.php";
"""
    findings = scanner.scan_source(code)
    features = {f.feature for f in findings}

    assert UnsupportedFeature.EVAL in features
    assert UnsupportedFeature.VARIABLE_VARIABLE in features
    assert UnsupportedFeature.DYNAMIC_INCLUDE in features

    # Verify rule IDs
    eval_findings = [f for f in findings if f.feature == UnsupportedFeature.EVAL]
    assert eval_findings[0].rule_id == "RHP-UNSUP-004"

    var_findings = [f for f in findings if f.feature == UnsupportedFeature.VARIABLE_VARIABLE]
    assert var_findings[0].rule_id == "RHP-UNSUP-005"

    inc_findings = [f for f in findings if f.feature == UnsupportedFeature.DYNAMIC_INCLUDE]
    assert len(inc_findings) == 3
    for inc in inc_findings:
        assert inc.rule_id == "RHP-UNSUP-003"
        assert "static" not in inc.node_text


def test_unsupported_scanner_magic_methods_and_curl_multi():
    """Verify detection of forbidden magic methods and curl_multi loops."""
    scanner = UnsupportedScanner()
    code = r"""<?php
class MagicClass {
    public function __construct() {} // Allowed!
    public function __destruct() {}  // Allowed!

    public function __get($name) {}
    public function __set($name, $val) {}
    public function __call($name, $args) {}
    public function __sleep() {}
    public function __wakeup() {}
    public function __set_state($arr) {}

    public function doHttp() {
        $ch = curl_init(); // Standard curl is allowed (handled by LLM_ASSIST)
        $mh = curl_multi_init();
        curl_multi_exec($mh, $running);
    }
}
"""
    findings = scanner.scan_source(code)
    magic_findings = [f for f in findings if f.feature == UnsupportedFeature.MAGIC_METHOD]
    curl_findings = [f for f in findings if f.feature == UnsupportedFeature.CURL_MULTI]

    # __construct and __destruct must not be flagged
    magic_names = [f.node_text for f in magic_findings]
    assert not any("__construct" in m for m in magic_names)
    assert not any("__destruct" in m for m in magic_names)
    assert len(magic_findings) == 6
    for mf in magic_findings:
        assert mf.rule_id == "RHP-UNSUP-006"

    # curl_multi_* must be flagged, standard curl_init must NOT be flagged as multi
    assert len(curl_findings) == 2
    for cf in curl_findings:
        assert cf.rule_id == "RHP-UNSUP-007"
        assert "curl_multi" in cf.node_text


# ============================================================================
# Task 4: Dependency Graph and Topological Ordering Tests
# ============================================================================

def test_dependency_graph_migration_ordering():
    """Verify DAG topological sorting produces leaf classes first, controllers last."""
    dep_graph = ProjectDependencyGraph()

    model_code = r"""<?php
namespace App\Models;

class Order {
    public int $id;
}
"""

    contract_code = r"""<?php
namespace App\Contracts;

interface PaymentInterface {
    public function pay(float $amount): bool;
}
"""

    base_service_code = r"""<?php
namespace App\Services;

class BaseService {}
"""

    service_code = r"""<?php
namespace App\Services;

use App\Models\Order;
use App\Contracts\PaymentInterface;

class OrderService extends BaseService implements PaymentInterface {
    public function pay(float $amount): bool {
        $order = new Order();
        return true;
    }
}
"""

    controller_code = r"""<?php
namespace App\Http\Controllers;

use App\Services\OrderService;

class OrderController {
    private OrderService $service;

    public function __construct(OrderService $service) {
        $this->service = $service;
    }
}
"""

    p_model = Path("/repo/app/Models/Order.php")
    p_contract = Path("/repo/app/Contracts/PaymentInterface.php")
    p_base = Path("/repo/app/Services/BaseService.php")
    p_service = Path("/repo/app/Services/OrderService.php")
    p_controller = Path("/repo/app/Http/Controllers/OrderController.php")

    # Add in mixed order
    dep_graph.add_file(p_controller, controller_code)
    dep_graph.add_file(p_service, service_code)
    dep_graph.add_file(p_base, base_service_code)
    dep_graph.add_file(p_model, model_code)
    dep_graph.add_file(p_contract, contract_code)

    file_order = dep_graph.get_migration_order()
    assert len(file_order) == 5

    # Leaves (Order, PaymentInterface, BaseService) must appear before OrderService
    assert file_order.index(p_model) < file_order.index(p_service)
    assert file_order.index(p_contract) < file_order.index(p_service)
    assert file_order.index(p_base) < file_order.index(p_service)

    # OrderService must appear before OrderController
    assert file_order.index(p_service) < file_order.index(p_controller)

    # Class-level ordering
    class_order = dep_graph.get_class_migration_order()
    assert class_order.index(r"App\Services\BaseService") < class_order.index(r"App\Services\OrderService")
    assert class_order.index(r"App\Services\OrderService") < class_order.index(r"App\Http\Controllers\OrderController")


def test_dependency_graph_cycle_breaking():
    """Verify circular dependencies are gracefully resolved without infinite loop or crash."""
    dep_graph = ProjectDependencyGraph()

    class_a_code = r"""<?php
namespace App\Circ;
use App\Circ\ClassB;

class ClassA {
    public function callB(ClassB $b) {}
}
"""

    class_b_code = r"""<?php
namespace App\Circ;
use App\Circ\ClassA;

class ClassB {
    public function callA(ClassA $a) {}
}
"""

    p_a = Path("/repo/app/Circ/ClassA.php")
    p_b = Path("/repo/app/Circ/ClassB.php")

    dep_graph.add_file(p_a, class_a_code)
    dep_graph.add_file(p_b, class_b_code)

    # Must complete and return both files
    order = dep_graph.get_migration_order()
    assert len(order) == 2
    assert p_a in order
    assert p_b in order


# ============================================================================
# Task 5: Readiness Scoring and Analysis Report Tests
# ============================================================================

def test_readiness_score_formula():
    """Verify readiness_percentage = 1.0 - (unsupported_count * 0.15 + untyped_param_count * 0.05)."""
    assert calculate_readiness_score(0, 0) == 1.0
    assert calculate_readiness_score(1, 0) == 0.85
    assert calculate_readiness_score(0, 1) == 0.95
    assert calculate_readiness_score(1, 1) == 0.80
    assert calculate_readiness_score(2, 4) == 0.50
    # Clamping boundaries
    assert calculate_readiness_score(10, 10) == 0.0
    assert calculate_readiness_score(-1, -1) == 1.0


def test_readiness_analyzer_on_fixture():
    """Verify detection against a fixture of PHP code with reflection, ob_*, and inheritance."""
    analyzer = ReadinessAnalyzer()

    fixture_base_service = r"""<?php
namespace App\Core;

/**
 * Base service foundation.
 */
class BaseService {
    public function log(string $message): void {}
}
"""

    fixture_problematic_service = r"""<?php
namespace App\Services;

use App\Core\BaseService;
use ReflectionClass;

/**
 * Problematic service with reflection, output buffering, and untyped parameters.
 */
class ProblematicService extends BaseService {
    /**
     * Executes legacy logic.
     */
    public function execute($untypedUser, string $role): string {
        $reflector = new ReflectionClass($untypedUser);
        ob_start();
        echo "Processing user";
        $output = ob_get_clean();

        return $output;
    }
}
"""

    p_base = Path("/fixture/BaseService.php")
    p_prob = Path("/fixture/ProblematicService.php")

    report_base = analyzer.analyze_file(p_base, source=fixture_base_service)
    assert report_base.readiness_percentage == 1.0
    assert report_base.unsupported_count == 0
    assert report_base.untyped_param_count == 0
    assert report_base.is_migration_ready

    report_prob = analyzer.analyze_file(p_prob, source=fixture_problematic_service)
    # ProblematicService has:
    # 2 unsupported: ReflectionClass (RHP-UNSUP-001) + ob_start/ob_get_clean (RHP-UNSUP-002)
    # wait: count reflections and ob_*
    # ReflectionClass: 2 occurrences (use ReflectionClass and new ReflectionClass)
    # ob_start: 1, ob_get_clean: 1
    # total unsupported = 4
    # untyped parameters: 1 ($untypedUser)
    # Score = 1.0 - (4 * 0.15 + 1 * 0.05) = 1.0 - (0.60 + 0.05) = 0.35
    assert report_prob.unsupported_count == 4
    assert report_prob.untyped_param_count == 1
    assert report_prob.readiness_percentage == 0.35
    assert not report_prob.is_migration_ready

    # Check rule IDs present in diagnostics
    rule_ids = {d.rule_id for d in report_prob.diagnostics}
    assert "RHP-UNSUP-001" in rule_ids
    assert "RHP-UNSUP-002" in rule_ids
    assert "RHP-PARAM-UNTYPED" in rule_ids


def test_analysis_report_project_aggregation(tmp_path: Path):
    """Verify end-to-end multi-file project analysis and report generation."""
    f1 = tmp_path / "BaseModel.php"
    f1.write_text(
        r"""<?php
namespace App\Models;

class BaseModel {
    public function getId(): int { return 1; }
}
""",
        encoding="utf-8",
    )

    f2 = tmp_path / "UserController.php"
    f2.write_text(
        r"""<?php
namespace App\Controllers;

use App\Models\BaseModel;

class UserController extends BaseModel {
    public function show($id) {
        ob_start();
        return $id;
    }
}
""",
        encoding="utf-8",
    )

    analyzer = ReadinessAnalyzer()
    report: AnalysisReport = analyzer.analyze_project(tmp_path)

    assert report.total_files == 2
    assert len(report.migration_order) == 2
    # Leaf BaseModel must come first
    assert report.migration_order[0] == f1.resolve()
    assert report.migration_order[1] == f2.resolve()

    assert report.total_unsupported_count == 1  # ob_start
    assert report.total_untyped_param_count == 1  # $id in show()
    assert "RHP-UNSUP-002" in report.unsupported_rule_counts

    text_summary = report.format_text()
    assert "RHP MIGRATION READINESS REPORT" in text_summary
    assert "BaseModel.php" in text_summary
    assert "UserController.php" in text_summary
    assert "RHP-UNSUP-002" in text_summary
