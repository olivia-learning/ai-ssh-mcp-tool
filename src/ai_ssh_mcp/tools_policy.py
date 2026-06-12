from __future__ import annotations

import uuid
from typing import Any

from .policy import evaluate_operation


def policy_evaluate_operation(
    operation_type: str,
    commands: list[str] | None = None,
    user_confirmed: bool = False,
) -> dict[str, Any]:
    return {
        "ok": True,
        **evaluate_operation(
            operation_type=operation_type,
            commands=commands,
            user_confirmed=user_confirmed,
        ).to_dict(),
        "audit_id": str(uuid.uuid4()),
    }


POLICY_TOOLS = [policy_evaluate_operation]
