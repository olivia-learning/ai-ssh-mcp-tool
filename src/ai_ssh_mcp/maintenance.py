from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import app_home, state_path, write_json_atomic
from .policy import DECISION_ALLOW, evaluate_operation
from .security import dedupe


SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")
RUNBOOK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class MaintenancePlan:
    plan_id: str
    summary: str
    change_type: str
    target: str
    steps: list[str]
    backup_steps: list[str]
    verification_steps: list[str]
    rollback_steps: list[str]
    risk_level: str
    command_hash: str
    created_at: str
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MaintenancePlan":
        return cls(
            plan_id=data["plan_id"],
            summary=data["summary"],
            change_type=data["change_type"],
            target=data["target"],
            steps=list(data["steps"]),
            backup_steps=list(data["backup_steps"]),
            verification_steps=list(data["verification_steps"]),
            rollback_steps=list(data["rollback_steps"]),
            risk_level=data["risk_level"],
            command_hash=data["command_hash"],
            created_at=data["created_at"],
            status=data.get("status", "planned"),
        )


class MaintenanceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_path()

    def save_plan(self, plan: MaintenancePlan) -> None:
        state = self._load()
        state["maintenance_plans"][plan.plan_id] = plan.to_dict()
        self._save(state)

    def get_plan(self, plan_id: str) -> MaintenancePlan:
        state = self._load()
        try:
            return MaintenancePlan.from_dict(state["maintenance_plans"][plan_id])
        except KeyError as exc:
            raise KeyError(f"Unknown maintenance plan_id: {plan_id}") from exc

    def mark_status(self, plan_id: str, status: str) -> None:
        state = self._load()
        if plan_id not in state["maintenance_plans"]:
            raise KeyError(f"Unknown maintenance plan_id: {plan_id}")
        state["maintenance_plans"][plan_id]["status"] = status
        self._save(state)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"plans": {}, "runs": [], "maintenance_plans": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        raw.setdefault("plans", {})
        raw.setdefault("runs", [])
        raw.setdefault("maintenance_plans", {})
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        write_json_atomic(self.path, state)


def runbook_dir() -> Path:
    return app_home() / "runbooks"


def plan_change(
    change_type: str,
    target: str,
    summary: str | None = None,
) -> MaintenancePlan:
    normalized = change_type.strip().lower()
    if normalized == "service_restart":
        validate_service_name(target)
        steps = [f"service {target} restart"]
        verification_steps = [f"service {target} status"]
        rollback_steps = [f"service {target} restart"]
        backup_steps = [f"service {target} status"]
        summary = summary or f"Restart service {target}"
    elif normalized == "device_reboot":
        steps = ["reboot"]
        verification_steps = ["uptime"]
        rollback_steps = ["人工确认设备重新上线；设备重启不可自动回滚"]
        backup_steps = ["uptime", "sync"]
        summary = summary or "Reboot device"
    else:
        raise ValueError("change_type must be service_restart or device_reboot")

    decision = evaluate_operation("maint_apply_change", commands=steps)
    plan = MaintenancePlan(
        plan_id=str(uuid.uuid4()),
        summary=summary,
        change_type=normalized,
        target=target,
        steps=steps,
        backup_steps=backup_steps,
        verification_steps=verification_steps,
        rollback_steps=rollback_steps,
        risk_level=decision.risk_level,
        command_hash=hash_maintenance_commands(steps, verification_steps),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    MaintenanceStore().save_plan(plan)
    return plan


def verify_plan_integrity(plan: MaintenancePlan) -> None:
    if plan.status != "planned":
        raise ValueError(f"Maintenance plan is not executable because status is {plan.status!r}.")
    expected = hash_maintenance_commands(plan.steps, plan.verification_steps)
    if expected != plan.command_hash:
        raise ValueError("Maintenance plan command hash mismatch; refusing to execute.")
    if plan.risk_level == "high" and not plan.rollback_steps:
        raise ValueError("High-risk maintenance plan requires rollback_steps.")


def hash_maintenance_commands(steps: list[str], verification_steps: list[str]) -> str:
    payload = json.dumps(
        {"steps": steps, "verification_steps": verification_steps},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_runbook(name: str) -> dict[str, Any]:
    if not RUNBOOK_NAME_PATTERN.match(name):
        raise ValueError("runbook name must contain only letters, numbers, dot, dash, or underscore")
    path = runbook_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Runbook not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    validate_runbook(raw)
    return raw


def validate_runbook(runbook: dict[str, Any]) -> None:
    steps = [str(step) for step in runbook.get("steps", [])]
    verification_steps = [str(step) for step in runbook.get("verification_steps", [])]
    rollback_steps = [str(step) for step in runbook.get("rollback_steps", [])]
    if not steps:
        raise ValueError("runbook steps are required")
    if not verification_steps:
        raise ValueError("runbook verification_steps are required")
    decision = evaluate_operation("maint_runbook", commands=steps)
    if decision.risk_level == "high" and not rollback_steps:
        raise ValueError("high-risk runbook requires rollback_steps")


def runbook_to_plan(name: str, runbook: dict[str, Any]) -> MaintenancePlan:
    steps = dedupe([str(step) for step in runbook["steps"]])
    verification_steps = dedupe([str(step) for step in runbook["verification_steps"]])
    rollback_steps = [str(step) for step in runbook.get("rollback_steps", [])]
    backup_steps = [str(step) for step in runbook.get("backup_steps", [])]
    decision = evaluate_operation("maint_runbook", commands=steps)
    plan = MaintenancePlan(
        plan_id=str(uuid.uuid4()),
        summary=str(runbook.get("summary", f"Runbook {name}")),
        change_type="runbook",
        target=name,
        steps=steps,
        backup_steps=backup_steps,
        verification_steps=verification_steps,
        rollback_steps=rollback_steps,
        risk_level=decision.risk_level,
        command_hash=hash_maintenance_commands(steps, verification_steps),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    MaintenanceStore().save_plan(plan)
    return plan


def validate_service_name(value: str) -> None:
    if not value or not SERVICE_NAME_PATTERN.match(value):
        raise ValueError("service name contains unsupported characters")

