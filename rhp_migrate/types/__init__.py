"""Static type extraction and inference module for PHP to RHP migration."""

from rhp_migrate.types.docblock import (
    DocblockParam,
    DocblockReturn,
    DocblockVar,
    ParsedDocblock,
    DocblockParser,
    infer_list_or_dict,
    map_php_type_to_rhp,
)
from rhp_migrate.types.phpstan_loader import (
    StaticAnalysisType,
    PHPStanLoader,
)
from rhp_migrate.types.inference import (
    TypeConfidence,
    InferredType,
    FunctionTypeInference,
    ClassTypeInference,
    FileTypeInference,
    TypeInferenceEngine,
    infer_type_from_default_value,
)

__all__ = [
    "DocblockParam",
    "DocblockReturn",
    "DocblockVar",
    "ParsedDocblock",
    "DocblockParser",
    "infer_list_or_dict",
    "map_php_type_to_rhp",
    "StaticAnalysisType",
    "PHPStanLoader",
    "TypeConfidence",
    "InferredType",
    "FunctionTypeInference",
    "ClassTypeInference",
    "FileTypeInference",
    "TypeInferenceEngine",
    "infer_type_from_default_value",
]
