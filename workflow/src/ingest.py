"""CSV ingestion and schema validation for the SPX weekly reporting workflow."""
import csv
import json
import os
from datetime import datetime


class IngestError(Exception):
    pass


def load_csv(path: str) -> "DataFrame":
    """Load a CSV file and return a lightweight DataFrame-like dict-of-lists."""
    if not path:
        raise ValueError("path must not be empty")
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return DataFrame([], [])

    columns = list(rows[0].keys())
    return DataFrame(rows, columns)


class DataFrame:
    """Minimal tabular data structure (avoids pandas dependency)."""

    def __init__(self, rows: list, columns: list):
        self.rows = rows
        self.columns = columns

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def column(self, name: str) -> list:
        return [r.get(name, "") for r in self.rows]

    def to_dicts(self) -> list:
        return list(self.rows)


def validate_schema(df: DataFrame, schema_name: str, schemas_dir: str) -> dict:
    """Validate a DataFrame against a named JSON schema.

    Returns:
        {
            "valid": bool,
            "errors": [str, ...],
            "row_count": int
        }
    """
    schema_path = os.path.join(schemas_dir, f"{schema_name}.json")
    if not os.path.exists(schema_path):
        raise IngestError(f"Unknown schema '{schema_name}': {schema_path} not found")

    with open(schema_path) as f:
        schema = json.load(f)

    errors = []

    # 1. Check required columns
    required = schema.get("required_columns", [])
    for col in required:
        if col not in df.columns:
            errors.append(f"Missing required column: '{col}'")

    if errors:
        return {"valid": False, "errors": errors, "row_count": len(df)}

    # 2. Type-check each column
    type_map = schema.get("types", {})
    for col, expected_type in type_map.items():
        if col not in df.columns:
            continue  # Already caught above
        col_errors = _check_column_type(df.column(col), col, expected_type)
        errors.extend(col_errors)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "row_count": len(df)
    }


def _check_column_type(values: list, col_name: str, expected_type: str) -> list:
    errors = []
    for i, val in enumerate(values):
        if val is None or str(val).strip() == "":
            continue  # Treat empty as missing, not a type error
        try:
            if expected_type == "float":
                float(val)
            elif expected_type == "int":
                int(val)
            elif expected_type == "date":
                datetime.strptime(str(val).strip(), "%Y-%m-%d")
            # str always passes
        except (ValueError, TypeError):
            errors.append(
                f"Column '{col_name}' row {i+2}: expected {expected_type}, got '{val}'"
            )
    return errors
