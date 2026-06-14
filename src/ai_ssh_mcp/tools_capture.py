from __future__ import annotations

import uuid
from typing import Any

from .capture import CAPTURE_MANAGER
from .responses import needs_confirmation


def capture_prepare(
    work_dir: str,
    tool_command: str,
    tool_prompt_pattern: str,
    top_command: str = "top -bn1",
    session_timeout_seconds: int = 3600,
    startup_timeout: int = 30,
    help_timeout: int = 30,
) -> dict[str, Any]:
    result = CAPTURE_MANAGER.prepare(
        work_dir=work_dir,
        tool_command=tool_command,
        tool_prompt_pattern=tool_prompt_pattern,
        top_command=top_command,
        session_timeout_seconds=session_timeout_seconds,
        startup_timeout=startup_timeout,
        help_timeout=help_timeout,
    )
    return capture_result(result, risk_level="medium")


def capture_build_plan(
    capture_id: str,
    modules: list[str],
    module_process_map: dict[str, Any],
    chl_command_template: str,
    rp_command_template: str,
) -> dict[str, Any]:
    result = CAPTURE_MANAGER.build_plan(
        capture_id=capture_id,
        modules=modules,
        module_process_map=module_process_map,
        chl_command_template=chl_command_template,
        rp_command_template=rp_command_template,
    )
    return {
        "ok": True,
        "decision": "require_user_confirmation",
        "risk_level": "medium",
        "results": result,
        "audit_id": result["plan_id"],
        "message": "Show this capture plan to the user and confirm before execution.",
    }


def capture_apply_plan(
    capture_id: str,
    plan_id: str,
    user_confirmed: bool = False,
) -> dict[str, Any]:
    if not user_confirmed:
        return needs_confirmation(
            "capture_apply_plan",
            "medium",
            "Show the capture plan to the user first, then call again with user_confirmed=true.",
        )
    result = CAPTURE_MANAGER.apply_plan(
        capture_id=capture_id,
        plan_id=plan_id,
        user_confirmed=True,
    )
    return capture_result(result, risk_level="medium", audit_id=plan_id)


def capture_start_recording(capture_id: str) -> dict[str, Any]:
    return capture_result(CAPTURE_MANAGER.start_recording(capture_id), risk_level="medium")


def capture_stop_recording(capture_id: str) -> dict[str, Any]:
    return capture_result(CAPTURE_MANAGER.stop_recording(capture_id), risk_level="medium")


def capture_save_recording(
    capture_id: str,
    destination_path: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    return capture_result(
        CAPTURE_MANAGER.save_recording(
            capture_id=capture_id,
            destination_path=destination_path,
            overwrite=overwrite,
        ),
        risk_level="medium",
    )


def capture_status(capture_id: str) -> dict[str, Any]:
    return capture_result(CAPTURE_MANAGER.status(capture_id), risk_level="low")


def capture_close(capture_id: str) -> dict[str, Any]:
    return capture_result(CAPTURE_MANAGER.close(capture_id), risk_level="low")


def capture_result(
    result: dict[str, Any],
    risk_level: str,
    audit_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": risk_level,
        "results": result,
        "audit_id": audit_id or str(uuid.uuid4()),
    }


CAPTURE_TOOLS = [
    capture_prepare,
    capture_build_plan,
    capture_apply_plan,
    capture_start_recording,
    capture_stop_recording,
    capture_save_recording,
    capture_status,
    capture_close,
]
