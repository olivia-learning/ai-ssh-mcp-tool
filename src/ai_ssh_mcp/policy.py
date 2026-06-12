from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import app_home, write_json_atomic
from .security import SENSITIVE_COMMAND_PATH_PATTERN


DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_CONFIRM = "require_user_confirmation"
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"


HIGH_RISK_PATTERN = re.compile(
    r"(^|\s)(reboot|poweroff|halt|shutdown|sysupgrade|opkg\s+upgrade|fw_setenv)\b|"
    r"(^|\s)(rm|mv|cp|chmod|chown|dd|mkfs|mount\s+-o\s+remount)\b|"
    r"(>|>>|;|&&|\|\||`|\$\()",
    re.IGNORECASE,
)

MEDIUM_RISK_OPERATIONS = {"files_download", "interactive_run_tool"}
HIGH_RISK_OPERATIONS = {"maint_apply_change", "maint_runbook", "maintenance"}


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    risk_level: str
    reason: str
    matched_rules: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def policy_path() -> Path:
    return app_home() / "policy.json"


def load_policy(path: Path | None = None) -> dict[str, Any]:
    target = path or policy_path()
    if not target.exists():
        return {
            "approval_mode": "policy_plus_human",
            "medium_requires_confirmation": True,
            "high_requires_confirmation": True,
        }
    raw = json.loads(target.read_text(encoding="utf-8"))
    raw.setdefault("approval_mode", "policy_plus_human")
    raw.setdefault("medium_requires_confirmation", True)
    raw.setdefault("high_requires_confirmation", True)
    return raw


def save_policy(policy: dict[str, Any], path: Path | None = None) -> None:
    write_json_atomic(path or policy_path(), policy)


def evaluate_operation(
    operation_type: str,
    commands: list[str] | None = None,
    user_confirmed: bool = False,
) -> PolicyDecision:
    commands = commands or []
    matched_rules: list[str] = []

    if any(is_sensitive_command(command) for command in commands):
        return PolicyDecision(
            decision=DECISION_DENY,
            risk_level=RISK_HIGH,
            reason="Operation references blocked sensitive paths.",
            matched_rules=["blocked_sensitive_path"],
        )

    risk_level = classify_risk(operation_type=operation_type, commands=commands)
    policy = load_policy()
    if risk_level == RISK_LOW:
        matched_rules.append("low_risk_auto_allow")
        return PolicyDecision(
            decision=DECISION_ALLOW,
            risk_level=risk_level,
            reason="Low-risk operation is allowed by policy.",
            matched_rules=matched_rules,
        )

    matched_rules.append(f"{risk_level}_risk_requires_confirmation")
    requires_confirmation = (
        risk_level == RISK_MEDIUM and bool(policy["medium_requires_confirmation"])
    ) or (risk_level == RISK_HIGH and bool(policy["high_requires_confirmation"]))
    if requires_confirmation and not user_confirmed:
        return PolicyDecision(
            decision=DECISION_CONFIRM,
            risk_level=risk_level,
            reason=f"{risk_level.title()}-risk operation requires user confirmation.",
            matched_rules=matched_rules,
        )

    return PolicyDecision(
        decision=DECISION_ALLOW,
        risk_level=risk_level,
        reason=f"{risk_level.title()}-risk operation allowed after confirmation.",
        matched_rules=matched_rules,
    )


def classify_risk(operation_type: str, commands: list[str]) -> str:
    normalized_type = operation_type.strip().lower()
    if normalized_type in HIGH_RISK_OPERATIONS:
        return RISK_HIGH
    if normalized_type in MEDIUM_RISK_OPERATIONS:
        return RISK_MEDIUM
    if any(HIGH_RISK_PATTERN.search(command) for command in commands):
        return RISK_HIGH
    if normalized_type.startswith("diag"):
        return RISK_LOW
    return RISK_MEDIUM


def is_sensitive_command(command: str) -> bool:
    return bool(SENSITIVE_COMMAND_PATH_PATTERN.search(command))

