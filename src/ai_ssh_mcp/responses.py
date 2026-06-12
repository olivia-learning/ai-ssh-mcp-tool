from __future__ import annotations

import uuid
from typing import Any


def needs_confirmation(operation: str, risk_level: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": "require_user_confirmation",
        "risk_level": risk_level,
        "results": [],
        "audit_id": str(uuid.uuid4()),
        "message": message,
        "operation": operation,
    }


def policy_block(decision, audit_id: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "decision": decision.decision,
        "risk_level": decision.risk_level,
        "results": [],
        "audit_id": audit_id or str(uuid.uuid4()),
        "reason": decision.reason,
        "matched_rules": decision.matched_rules,
    }


def execution_result(
    decision,
    results: list[dict[str, Any]],
    audit_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "decision": decision.decision,
        "risk_level": decision.risk_level,
        "results": results,
        "audit_id": audit_id or str(uuid.uuid4()),
    }


def command_result_dict(item) -> dict[str, Any]:
    return {
        "command": item.command,
        "output": item.stdout,
        "stdout": item.stdout,
        "stderr": item.stderr,
        "exit_status": item.exit_status,
        "success": item.exit_status == 0,
        "duration_ms": item.duration_ms,
        "truncated": item.truncated,
    }
