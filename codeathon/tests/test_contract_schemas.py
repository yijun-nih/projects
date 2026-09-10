import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError


REPO_ROOT = Path(__file__).parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts"
CONTRACT_NAMES = (
    "hypothesis_spec",
    "eligibility",
    "synthesis",
    "judgment_provenance",
)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class ContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = {
            name: _load_json(SCHEMA_DIR / f"{name}.schema.json")
            for name in CONTRACT_NAMES
        }
        cls.valid_contracts = _load_json(FIXTURE_DIR / "valid_contracts.json")
        cls.invalid_contracts = _load_json(FIXTURE_DIR / "invalid_contracts.json")
        cls.format_checker = FormatChecker()

    def test_schemas_are_valid_draft_2020_12(self) -> None:
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_valid_contract_fixtures_are_accepted(self) -> None:
        for name, schema in self.schemas.items():
            with self.subTest(schema=name):
                validator = Draft202012Validator(
                    schema,
                    format_checker=self.format_checker,
                )
                validator.validate(self.valid_contracts[name])

    def test_invalid_contract_fixtures_are_rejected(self) -> None:
        for name, cases in self.invalid_contracts.items():
            validator = Draft202012Validator(
                self.schemas[name],
                format_checker=self.format_checker,
            )
            for case in cases:
                with self.subTest(schema=name, case=case["case"]):
                    with self.assertRaises(ValidationError):
                        validator.validate(case["instance"])


if __name__ == "__main__":
    unittest.main()
