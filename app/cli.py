"""CLI — thin wrapper over SyncEngine (ADR-002 approved; no new HTTP surface).

Usage:
    python -m app.cli trigger --table=customers --direction=both
    python -m app.cli conflicts [--status=pending]
    python -m app.cli resolve <conflict_id> --choice=modern|legacy
    python -m app.cli audit [--limit=50] [--table=customers]
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID

from .engine import SyncEngine
from .main import build_engine
from .mappings import MappingError


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="legacy-sync")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_trigger = sub.add_parser("trigger", help="run one poll cycle")
    p_trigger.add_argument("--table", required=True, choices=["customers", "orders", "inventory"])
    p_trigger.add_argument("--direction", default="both",
                           choices=["modern_to_legacy", "legacy_to_modern", "both"])

    p_conf = sub.add_parser("conflicts", help="list conflicts")
    p_conf.add_argument("--status", default="pending", choices=["pending", "resolved"])

    p_res = sub.add_parser("resolve", help="resolve a conflict")
    p_res.add_argument("conflict_id", type=UUID)
    p_res.add_argument("--choice", required=True, choices=["modern", "legacy"])
    p_res.add_argument("--by", default="cli-admin")

    p_audit = sub.add_parser("audit", help="show audit log")
    p_audit.add_argument("--limit", type=int, default=50)
    p_audit.add_argument("--table", default=None)

    args = parser.parse_args(argv)
    engine: SyncEngine = build_engine()
    try:
        if args.cmd == "trigger":
            outcomes = engine.poll_once(args.table, args.direction)
            _print([o.__dict__ for o in outcomes] or {"message": "no pending changes"})
        elif args.cmd == "conflicts":
            rows = engine.store.list_conflicts(args.status)
            _print(rows)
        elif args.cmd == "resolve":
            conflict = engine.store.get_conflict(args.conflict_id)
            if conflict is None:
                print(f"conflict {args.conflict_id} not found", file=sys.stderr)
                return 1
            mapping = engine.mappings.get(conflict["table_name"])
            options = engine.proposed_resolutions(
                mapping, conflict["modern_state"] or {}, conflict["legacy_state"] or {}
            )
            chosen = next((o["state"] for o in options if o["name"] == args.choice), None)
            if chosen is None:
                print(f"no proposed resolution named {args.choice}", file=sys.stderr)
                return 1
            outcome = engine.resolve_conflict(args.conflict_id, chosen, args.by)
            _print(outcome.__dict__)
        elif args.cmd == "audit":
            _print([a.model_dump() for a in engine.list_audit(args.limit, args.table)])
    except MappingError as exc:
        print(f"mapping error: {exc}", file=sys.stderr)
        return 2
    finally:
        engine.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
