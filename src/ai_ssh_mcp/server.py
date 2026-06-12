from __future__ import annotations

from .tools_core import (
    CORE_TOOLS,
    core_configure_device,
    core_get_run_detail,
    core_list_recent_runs,
    core_test_connection,
)
from .tools_diag import (
    DIAG_TOOLS,
    diag_add_whitelist,
    diag_delete_whitelist,
    diag_list_whitelist,
    diag_plan_task,
    diag_run_plan,
    diag_run_shell,
)
from .tools_files import FILES_TOOLS, files_download
from .tools_interactive import INTERACTIVE_TOOLS, interactive_run_tool
from .tools_maint import MAINT_TOOLS, maint_apply_change, maint_plan_change, maint_runbook
from .tools_policy import POLICY_TOOLS, policy_evaluate_operation


SERVER_NAME = "ai-ssh-device"

TOOL_GROUPS = {
    "core": CORE_TOOLS,
    "diag": DIAG_TOOLS,
    "files": FILES_TOOLS,
    "interactive": INTERACTIVE_TOOLS,
    "policy": POLICY_TOOLS,
    "maint": MAINT_TOOLS,
}

REGISTERED_TOOLS = [
    tool
    for tools in TOOL_GROUPS.values()
    for tool in tools
]

REGISTERED_TOOL_NAMES = [tool.__name__ for tool in REGISTERED_TOOLS]


def build_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "mcp is not installed. Install project dependencies with `pip install -e .` first."
        ) from exc

    mcp = FastMCP(SERVER_NAME)
    for tool in REGISTERED_TOOLS:
        mcp.tool()(tool)
    return mcp


def main() -> None:
    build_mcp_server().run()
