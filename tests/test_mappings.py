"""Contract tests for the YAML mapping layer (api_contract §Implementation
notes 6: round-trip in both directions) and the ADR-002 transform sandbox."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from app.mappings import MappingError, MappingRegistry, apply_mapping

MAPPINGS = Path(__file__).resolve().parent.parent / "docs" / "mappings"


@pytest.fixture(scope="module")
def registry() -> MappingRegistry:
    return MappingRegistry.load(MAPPINGS)


def test_all_three_tables_have_mappings(registry):
    assert {m.table for m in registry.summaries()} == {"customers", "orders", "inventory"}


def test_customer_roundtrip_modern_to_legacy_to_modern(registry):
    mapping = registry.get("customers")
    modern = {
        "id": "c001",
        "email": "alice@acme.com",
        "name": "Alice Smith",
        "company": "Acme Corp",
        "status": "active",
        "updated_at": dt.datetime(2026, 9, 3, 12, 0, 0),
    }
    legacy = apply_mapping(mapping, modern, "modern_to_legacy")
    assert legacy["CUST_ID"] == "C001"  # transform: upper
    assert legacy["STATUS_CD"] == "A"
    assert legacy["CUST_EMAIL"] == "alice@acme.com"

    back = apply_mapping(mapping, legacy, "legacy_to_modern")
    assert back["email"] == modern["email"]
    assert back["name"] == modern["name"]
    assert back["status"] == "active"


def test_legacy_date_coerces_to_datetime(registry):
    mapping = registry.get("customers")
    legacy = {
        "CUST_ID": "C9",
        "CUST_NAME": "N",
        "CUST_EMAIL": "e@x.com",
        "STATUS_CD": "I",
        "LAST_UPD_DT": dt.date(2026, 1, 15),
    }
    modern = apply_mapping(mapping, legacy, "legacy_to_modern")
    assert isinstance(modern["updated_at"], dt.datetime)
    assert modern["updated_at"].date() == dt.date(2026, 1, 15)
    assert modern["status"] == "inactive"


def test_missing_required_field_raises_mapping_error(registry):
    mapping = registry.get("customers")
    with pytest.raises(MappingError, match="required field"):
        apply_mapping(mapping, {"CUST_ID": "C1", "CUST_NAME": "No Email"}, "legacy_to_modern")


def test_orders_status_code_transform_both_directions(registry):
    mapping = registry.get("orders")
    modern = {
        "id": "O-1",
        "customer_id": "C1",
        "order_date": dt.datetime(2026, 9, 1),
        "total_amount": Decimal("42.50"),
        "status": "shipped",
    }
    legacy = apply_mapping(mapping, modern, "modern_to_legacy")
    assert legacy["ORD_STAT"] == "S"
    back = apply_mapping(mapping, legacy, "legacy_to_modern")
    assert back["status"] == "shipped"


def test_transform_sandbox_blocks_dangerous_expressions(registry, tmp_path):
    evil = tmp_path / "evil.yaml"
    evil.write_text(
        """
version: "9.9"
table: "customers"
source_of_truth: "modern"
conflict_strategy: "last_write_wins"
fields:
  - modern_field: id
    legacy_field: CUST_ID
    type: string
    direction: both
    required: true
    transform:
      modern_to_legacy: "__import__('os').system('id')"
"""
    )
    reg = MappingRegistry.load(tmp_path)
    mapping = reg.get("customers")
    with pytest.raises(MappingError, match="forbidden token"):
        apply_mapping(mapping, {"id": "x"}, "modern_to_legacy")


def test_transform_sandbox_has_no_builtins(registry, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        """
version: "9.9"
table: "customers"
source_of_truth: "modern"
conflict_strategy: "last_write_wins"
fields:
  - modern_field: id
    legacy_field: CUST_ID
    type: string
    direction: both
    required: true
    transform:
      modern_to_legacy: "open('/etc/passwd').read()"
"""
    )
    reg = MappingRegistry.load(tmp_path)
    with pytest.raises(MappingError):
        apply_mapping(reg.get("customers"), {"id": "x"}, "modern_to_legacy")


def test_invalid_mapping_rejected_at_load(tmp_path):
    bad = tmp_path / "nope.yaml"
    bad.write_text('version: "1"\ntable: "ghost"\n')  # missing required keys
    with pytest.raises(Exception):
        MappingRegistry.load(tmp_path)
