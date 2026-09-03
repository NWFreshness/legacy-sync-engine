"""Versioned YAML mapping layer (docs/mappings/*.yaml).

Loads TableMapping definitions, validates them with the Pydantic contract,
and applies them bidirectionally with type coercion and a deliberately tiny
transform sandbox (reading 3 in the D1 design reconciliations: only simple
`str`/`int`/`Decimal` expressions with `value` in scope, no builtins).

Anything the sandbox rejects raises MappingError; the sync engine turns that
into a dead-letter record rather than guessing.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml

from .schemas import MappingField, TableMapping


class MappingError(Exception):
    """A field could not be mapped. Poison-pill signal -> dead letter queue."""


# --- transform sandbox -------------------------------------------------------
# Whitelist of callables allowed inside YAML transform expressions. No
# builtins, no attribute access beyond what these objects expose, no imports.
# Rationale: mappings are config, not code execution surface.

_SAFE_GLOBALS: dict[str, Any] = {"__builtins__": {}}
_SAFE_NAMES: dict[str, Any] = {
    "str": str,
    "int": int,
    "float": float,
    "Decimal": Decimal,
    "len": len,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}

_FORBIDDEN = re.compile(r"(__|import|open|exec|eval|getattr|setattr|globals|locals)")


def _apply_transform(expr: str, value: Any) -> Any:
    if _FORBIDDEN.search(expr):
        raise MappingError(f"forbidden token in transform: {expr!r}")
    try:
        return eval(  # noqa: S307 — sandboxed by construction above
            expr, _SAFE_GLOBALS, {**_SAFE_NAMES, "value": value}
        )
    except MappingError:
        raise
    except Exception as exc:
        raise MappingError(f"transform failed ({expr!r}): {exc}") from exc


def _coerce(value: Any, type_name: str, direction: Literal["modern_to_legacy", "legacy_to_modern"]) -> Any:
    """Coerce between Python types implied by the mapping `type` field.

    Legacy DATE columns arrive as datetime.date; modern TIMESTAMPTZ expects
    datetime. Numeric legacy columns may arrive as strings; normalize.
    """
    if value is None:
        return None
    try:
        if type_name == "string":
            return str(value)
        if type_name == "integer":
            return int(value)
        if type_name == "numeric":
            return Decimal(str(value))
        if type_name == "datetime":
            if isinstance(value, dt.datetime):
                return value
            if isinstance(value, dt.date):
                # Legacy DATE -> modern TIMESTAMPTZ (start of day, UTC-naive).
                return dt.datetime.combine(value, dt.time.min)
            if isinstance(value, str):
                # ISO strings from either side; tolerate trailing Z.
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except (ValueError, TypeError) as exc:
        raise MappingError(
            f"cannot coerce {value!r} to {type_name} for {direction}"
        ) from exc


class MappingRegistry:
    """All loaded table mappings, keyed by table name."""

    def __init__(self, mappings: dict[str, TableMapping]):
        self._mappings = mappings

    @classmethod
    def load(cls, directory: Path) -> "MappingRegistry":
        mappings: dict[str, TableMapping] = {}
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text())
            mapping = TableMapping.model_validate(raw)
            mappings[mapping.table] = mapping
        if not mappings:
            raise MappingError(f"no mappings found in {directory}")
        return cls(mappings)

    def get(self, table: str) -> TableMapping:
        try:
            return self._mappings[table]
        except KeyError:
            raise MappingError(f"no mapping for table {table!r}") from None

    def summaries(self) -> list[TableMapping]:
        return list(self._mappings.values())


def _fields_for(mapping: TableMapping, direction: Literal["modern_to_legacy", "legacy_to_modern"]) -> list[MappingField]:
    return [f for f in mapping.fields if f.direction in ("both", direction)]


def apply_mapping(
    mapping: TableMapping,
    record: dict[str, Any],
    direction: Literal["modern_to_legacy", "legacy_to_modern"],
) -> dict[str, Any]:
    """Translate one record between schemas per the mapping.

    Missing required fields raise MappingError (poison pill). Optional
    missing fields fall back to their mapping default, else are omitted.
    """
    out: dict[str, Any] = {}
    for field in _fields_for(mapping, direction):
        src = field.modern_field if direction == "modern_to_legacy" else field.legacy_field
        dst = field.legacy_field if direction == "modern_to_legacy" else field.modern_field
        value = record.get(src)
        if value is None:
            if field.required:
                raise MappingError(
                    f"required field {src!r} missing for table {mapping.table!r}"
                )
            if field.default is not None:
                value = field.default
            else:
                continue
        transform = (field.transform or {}).get(direction)
        if transform:
            value = _apply_transform(transform, value)
        out[dst] = _coerce(value, field.type, direction)
    return out
