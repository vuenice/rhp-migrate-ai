"""Registry for managing, querying, and exporting RHP migration rules."""

from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union
import yaml
from pydantic import ValidationError

from rhp_migrate.rules.schema import MigrationStrategy, Rule, RuleCategory

_DEFAULT_RULES_PATH = Path(__file__).parent / "rules.yaml"


class RuleRegistry:
    """Registry maintaining migration rules for deterministic and LLM-assisted transforms."""

    def __init__(self, rules: Optional[Iterable[Rule]] = None) -> None:
        self._rules: dict[str, Rule] = {}
        if rules:
            for rule in rules:
                self.register(rule)

    def register(self, rule: Rule, overwrite: bool = False) -> None:
        """Register a new rule in the registry.

        Args:
            rule: The Rule instance to register.
            overwrite: Whether to overwrite an existing rule with the same ID.

        Raises:
            ValueError: If a rule with the same ID exists and overwrite is False.
        """
        if rule.id in self._rules and not overwrite:
            raise ValueError(f"Rule with ID '{rule.id}' already exists in registry.")
        self._rules[rule.id] = rule

    def get(self, rule_id: str, default: Optional[Rule] = None) -> Optional[Rule]:
        """Retrieve a rule by ID, or return default if not found."""
        return self._rules.get(rule_id, default)

    def __getitem__(self, rule_id: str) -> Rule:
        """Retrieve a rule by ID or raise KeyError."""
        if rule_id not in self._rules:
            raise KeyError(f"Rule '{rule_id}' not found in registry.")
        return self._rules[rule_id]

    def __contains__(self, rule_id: str) -> bool:
        """Check if a rule ID is registered."""
        return rule_id in self._rules

    def __len__(self) -> int:
        """Return the number of registered rules."""
        return len(self._rules)

    def __iter__(self):
        """Iterate over all registered rules."""
        return iter(self._rules.values())

    def all_rules(self) -> list[Rule]:
        """Return a list of all registered rules."""
        return list(self._rules.values())

    def filter(
        self,
        category: Optional[Union[RuleCategory, str]] = None,
        strategy: Optional[Union[MigrationStrategy, str]] = None,
        unsupported: Optional[bool] = None,
    ) -> list[Rule]:
        """Filter rules by category, strategy, and/or unsupported status.

        Args:
            category: Optional RuleCategory or string representation.
            strategy: Optional MigrationStrategy or string representation.
            unsupported: Optional boolean filter.

        Returns:
            List of matching Rule instances.
        """
        cat_val = None
        if category is not None:
            cat_val = category.value if isinstance(category, RuleCategory) else str(category).upper()

        strat_val = None
        if strategy is not None:
            strat_val = strategy.value if isinstance(strategy, MigrationStrategy) else str(strategy).upper()

        results: list[Rule] = []
        for rule in self._rules.values():
            if cat_val is not None and rule.category.value != cat_val:
                continue
            if strat_val is not None and rule.strategy.value != strat_val:
                continue
            if unsupported is not None and rule.unsupported is not unsupported:
                continue
            results.append(rule)
        return results

    def get_by_category(self, category: Union[RuleCategory, str]) -> list[Rule]:
        """Retrieve all rules matching a specific category."""
        return self.filter(category=category)

    def get_by_strategy(self, strategy: Union[MigrationStrategy, str]) -> list[Rule]:
        """Retrieve all rules matching a specific migration strategy."""
        return self.filter(strategy=strategy)

    def get_unsupported(self) -> list[Rule]:
        """Retrieve all rules for PHP features unsupported in RHP."""
        return self.filter(unsupported=True)

    def get_deterministic(self) -> list[Rule]:
        """Retrieve all rules that can be migrated deterministically."""
        return self.filter(strategy=MigrationStrategy.DETERMINISTIC)

    def export_prompt_context(
        self,
        rules: Optional[Sequence[Rule]] = None,
        category: Optional[Union[RuleCategory, str]] = None,
        strategy: Optional[Union[MigrationStrategy, str]] = None,
        unsupported: Optional[bool] = None,
        include_header: bool = True,
    ) -> str:
        """Export rule contexts formatted for LLM system/user prompts.

        Args:
            rules: Specific rules to export. If None, uses filtered rules from registry.
            category: Optional category filter when rules is None.
            strategy: Optional strategy filter when rules is None.
            unsupported: Optional unsupported filter when rules is None.
            include_header: Whether to include a contextual introduction header.

        Returns:
            Markdown-formatted string containing rule guidance and examples.
        """
        target_rules = (
            list(rules)
            if rules is not None
            else self.filter(category=category, strategy=strategy, unsupported=unsupported)
        )

        blocks: list[str] = []
        if include_header:
            blocks.append("# RHP Migration Rules Reference\n")
            blocks.append(
                "You are migrating PHP/Laravel code to RHP (a high-performance strictly-typed PHP superset).\n"
                "Adhere strictly to the following migration rules, syntax replacements, and architectural guidelines:\n"
            )

        if not target_rules:
            blocks.append("No migration rules matching criteria.")
            return "\n".join(blocks)

        for rule in target_rules:
            blocks.append(rule.format_prompt_block())
            blocks.append("")  # Empty separator line

        return "\n".join(blocks).strip()

    @classmethod
    def from_yaml_string(cls, content: str) -> "RuleRegistry":
        """Load and parse rules from a YAML string."""
        raw_data = yaml.safe_load(content)
        if raw_data is None:
            return cls([])

        if isinstance(raw_data, dict):
            if "rules" not in raw_data:
                raise ValueError("YAML mapping must contain a top-level 'rules' key.")
            rule_items = raw_data["rules"]
        elif isinstance(raw_data, list):
            rule_items = raw_data
        else:
            raise ValueError(f"Expected YAML list or mapping, got {type(raw_data).__name__}")

        if not isinstance(rule_items, list):
            raise ValueError(f"Expected list of rules, got {type(rule_items).__name__}")

        parsed_rules: list[Rule] = []
        for idx, item in enumerate(rule_items):
            if not isinstance(item, dict):
                raise ValueError(f"Rule at index {idx} must be a dictionary.")
            try:
                parsed_rules.append(Rule.model_validate(item))
            except ValidationError as err:
                rule_id = item.get("id", f"index-{idx}")
                raise ValueError(f"Validation failed for rule '{rule_id}': {err}") from err

        return cls(parsed_rules)

    @classmethod
    def from_yaml_file(cls, path: Union[str, Path]) -> "RuleRegistry":
        """Load and parse rules from a YAML file."""
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"Rules file not found at: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return cls.from_yaml_string(content)

    @classmethod
    def default(cls) -> "RuleRegistry":
        """Load the bundled default rules.yaml or environment configured rules path."""
        import os
        env_yaml = os.environ.get("RHP_RULES_YAML_PATH")
        if env_yaml and Path(env_yaml).is_file():
            return cls.from_yaml_file(env_yaml)
        env_dir = os.environ.get("RHP_RULES_DIR")
        if env_dir and (Path(env_dir) / "rules.yaml").is_file():
            return cls.from_yaml_file(Path(env_dir) / "rules.yaml")
        return cls.from_yaml_file(_DEFAULT_RULES_PATH)
