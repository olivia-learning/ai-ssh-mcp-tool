from __future__ import annotations

from typing import Any

from .config import load_config
from .credentials import CredentialStore
from .maintenance import (
    MaintenanceStore,
    load_runbook,
    plan_change,
    runbook_to_plan,
    verify_plan_integrity,
)
from .policy import DECISION_ALLOW, evaluate_operation
from .responses import command_result_dict, execution_result, policy_block
from .ssh_client import EmbeddedSSHSession


def maint_plan_change(
    change_type: str,
    target: str,
    summary: str | None = None,
) -> dict[str, Any]:
    plan = plan_change(change_type=change_type, target=target, summary=summary)
    return {
        "ok": True,
        "decision": "require_user_confirmation",
        "risk_level": plan.risk_level,
        "results": plan.to_dict(),
        "audit_id": plan.plan_id,
    }


def maint_apply_change(plan_id: str, user_confirmed: bool = False) -> dict[str, Any]:
    store = MaintenanceStore()
    plan = store.get_plan(plan_id)
    verify_plan_integrity(plan)
    decision = evaluate_operation(
        "maint_apply_change",
        commands=plan.steps,
        user_confirmed=user_confirmed,
    )
    if decision.decision != DECISION_ALLOW:
        return policy_block(decision, audit_id=plan_id)

    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    commands = plan.backup_steps + plan.steps + plan.verification_steps
    store.mark_status(plan_id, "running")
    try:
        with EmbeddedSSHSession(config, secrets) as session:
            results = [
                session._run_shell_command(command=command, purpose="maintenance plan step")
                for command in commands
            ]
        store.mark_status(plan_id, "executed")
        return execution_result(decision, [command_result_dict(item) for item in results], audit_id=plan_id)
    except Exception:
        store.mark_status(plan_id, "failed")
        raise


def maint_runbook(
    name: str,
    user_confirmed: bool = False,
    plan_id: str | None = None,
) -> dict[str, Any]:
    if plan_id:
        plan = MaintenanceStore().get_plan(plan_id)
        if plan.change_type != "runbook" or plan.target != name:
            raise ValueError("runbook plan_id does not match the requested runbook name")
        if not user_confirmed:
            return {
                "ok": False,
                "decision": "require_user_confirmation",
                "risk_level": plan.risk_level,
                "results": plan.to_dict(),
                "audit_id": plan.plan_id,
                "message": "Show this runbook execution plan to the user before execution.",
            }
        return maint_apply_change(plan.plan_id, user_confirmed=True)

    runbook = load_runbook(name)
    plan = runbook_to_plan(name, runbook)
    if not user_confirmed:
        return {
            "ok": False,
            "decision": "require_user_confirmation",
            "risk_level": plan.risk_level,
            "results": plan.to_dict(),
            "audit_id": plan.plan_id,
            "message": "Runbook was converted to a maintenance plan. Confirm before execution.",
        }
    return {
        "ok": False,
        "decision": "require_user_confirmation",
        "risk_level": plan.risk_level,
        "results": plan.to_dict(),
        "audit_id": plan.plan_id,
        "message": "Runbook execution requires confirming the displayed plan_id first.",
    }


MAINT_TOOLS = [
    maint_plan_change,
    maint_apply_change,
    maint_runbook,
]
