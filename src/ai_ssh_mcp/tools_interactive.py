from __future__ import annotations

from typing import Any

from .config import load_config
from .credentials import CredentialStore
from .policy import DECISION_ALLOW, evaluate_operation
from .responses import execution_result, policy_block
from .ssh_client import EmbeddedSSHSession


def interactive_run_tool(
    work_dir: str,
    tool_command: str,
    inputs: list[str],
    prompt_pattern: str,
    user_confirmed: bool = False,
    startup_timeout: int = 30,
    command_timeout: int = 30,
    prompt_settle_seconds: float = 0.8,
) -> dict[str, Any]:
    decision = evaluate_operation("interactive_run_tool", user_confirmed=user_confirmed)
    if decision.decision != DECISION_ALLOW:
        return policy_block(decision)

    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    with EmbeddedSSHSession(config, secrets) as session:
        results = session.run_interactive_tool(
            work_dir=work_dir,
            tool_command=tool_command,
            inputs=inputs,
            prompt_pattern=prompt_pattern,
            startup_timeout=startup_timeout,
            command_timeout=command_timeout,
            prompt_settle_seconds=prompt_settle_seconds,
        )
    return execution_result(
        decision,
        [
            {
                "input": item.input,
                "output": item.output,
                "duration_ms": item.duration_ms,
                "truncated": item.truncated,
            }
            for item in results
        ],
    )


INTERACTIVE_TOOLS = [interactive_run_tool]
