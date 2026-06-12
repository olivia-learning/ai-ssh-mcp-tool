from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .config import load_config
from .credentials import CredentialStore
from .policy import DECISION_ALLOW, evaluate_operation
from .responses import command_result_dict, execution_result, needs_confirmation, policy_block
from .security import (
    add_allowlist_commands,
    delete_allowlist_commands,
    generate_plan,
    list_allowlist_commands,
    verify_plan_integrity,
)
from .ssh_client import EmbeddedSSHSession
from .store import AuditStore, make_run_record


def diag_list_whitelist(include_disabled: bool = True) -> dict[str, Any]:
    commands = [
        {
            "command_id": item.command_id,
            "command": item.command,
            "purpose": item.purpose,
            "source": item.source,
            "enabled": item.enabled,
        }
        for item in list_allowlist_commands(include_disabled=include_disabled)
    ]
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": {"commands": commands, "count": len(commands)},
        "audit_id": str(uuid.uuid4()),
    }


def diag_add_whitelist(commands: list[dict[str, str]]) -> dict[str, Any]:
    result = add_allowlist_commands(commands)
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": result,
        "audit_id": str(uuid.uuid4()),
    }


def diag_delete_whitelist(
    command_ids: list[str] | None = None, commands: list[str] | None = None
) -> dict[str, Any]:
    result = delete_allowlist_commands(command_ids=command_ids, commands=commands)
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": result,
        "audit_id": str(uuid.uuid4()),
    }


def diag_plan_task(task: str, command_ids: list[str] | None = None) -> dict[str, Any]:
    plan = generate_plan(task, command_ids=command_ids)
    AuditStore().save_plan(plan)
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": plan.to_dict(),
        "audit_id": plan.approval_id,
    }


def diag_run_plan(approval_id: str, user_confirmed: bool = False) -> dict[str, Any]:
    if not user_confirmed:
        return needs_confirmation("diag_run_plan", "low", "Show the plan to the user first.")

    store = AuditStore()
    plan = store.get_plan(approval_id)
    verify_plan_integrity(plan)
    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    started_at = datetime.now(timezone.utc)

    store.mark_plan_status(approval_id, "running")
    try:
        with EmbeddedSSHSession(config, secrets) as session:
            results = session.run_commands(plan.commands)
        run = make_run_record(
            approval_id=approval_id,
            task=plan.task,
            started_at=started_at,
            results=results,
        )
        store.save_run(run)
        store.mark_plan_status(approval_id, "executed")
        return {
            "ok": True,
            "decision": "allow",
            "risk_level": "low",
            "results": run.to_dict(),
            "audit_id": run.run_id,
        }
    except Exception:
        store.mark_plan_status(approval_id, "failed")
        raise


def diag_run_shell(
    commands: list[str],
    user_confirmed: bool = False,
    command_timeout: int = 30,
) -> dict[str, Any]:
    decision = evaluate_operation("diag_run_shell", commands=commands, user_confirmed=user_confirmed)
    if decision.decision != DECISION_ALLOW:
        return policy_block(decision)

    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    with EmbeddedSSHSession(config, secrets) as session:
        results = session.run_shell_commands(commands=commands, command_timeout=command_timeout)
    return execution_result(decision, [command_result_dict(item) for item in results])


DIAG_TOOLS = [
    diag_list_whitelist,
    diag_add_whitelist,
    diag_delete_whitelist,
    diag_plan_task,
    diag_run_plan,
    diag_run_shell,
]
