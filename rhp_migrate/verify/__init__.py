"""Verification and Differential Testing package (Layer 6)."""

from rhp_migrate.verify.compiler_loop import (
    CodeUnit,
    clean_code_fence,
    find_code_unit,
    insert_todo_above_unit,
    is_syntactically_valid,
    replace_unit_in_source,
    verify_and_heal,
)
from rhp_migrate.verify.differential import (
    DifferentialResult,
    PhpError,
    PhpNotFoundError,
    execute_differential_test,
    find_php_binary,
    get_php_binary,
    run_differential,
    run_differential_snippet,
    run_differential_test,
)

__all__ = [
    "CodeUnit",
    "clean_code_fence",
    "find_code_unit",
    "insert_todo_above_unit",
    "is_syntactically_valid",
    "replace_unit_in_source",
    "verify_and_heal",
    "DifferentialResult",
    "PhpError",
    "PhpNotFoundError",
    "execute_differential_test",
    "find_php_binary",
    "get_php_binary",
    "run_differential",
    "run_differential_snippet",
    "run_differential_test",
]
