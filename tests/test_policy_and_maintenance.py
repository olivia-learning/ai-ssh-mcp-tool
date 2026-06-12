import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ssh_mcp.config import APP_DIR_ENV
from ai_ssh_mcp.maintenance import (
    MaintenanceStore,
    hash_maintenance_commands,
    load_runbook,
    plan_change,
    runbook_dir,
    runbook_to_plan,
    verify_plan_integrity,
)
from ai_ssh_mcp.policy import evaluate_operation
from ai_ssh_mcp.server import (
    REGISTERED_TOOL_NAMES,
    TOOL_GROUPS,
    maint_apply_change,
    maint_plan_change,
    maint_runbook,
)


class PolicyAndMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get(APP_DIR_ENV)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ[APP_DIR_ENV] = self._tmp.name

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop(APP_DIR_ENV, None)
        else:
            os.environ[APP_DIR_ENV] = self._old_home
        self._tmp.cleanup()

    def test_policy_allows_low_risk(self):
        decision = evaluate_operation("diag_run_shell", commands=["ls -l"])
        self.assertEqual(decision.decision, "allow")
        self.assertEqual(decision.risk_level, "low")

    def test_policy_requires_confirmation_for_medium_risk(self):
        decision = evaluate_operation("files_download")
        self.assertEqual(decision.decision, "require_user_confirmation")
        self.assertEqual(decision.risk_level, "medium")

    def test_policy_requires_confirmation_for_high_risk(self):
        decision = evaluate_operation("maint_apply_change", commands=["reboot"])
        self.assertEqual(decision.decision, "require_user_confirmation")
        self.assertEqual(decision.risk_level, "high")

    def test_policy_denies_sensitive_paths(self):
        decision = evaluate_operation("diag_run_shell", commands=["cat /etc/shadow"])
        self.assertEqual(decision.decision, "deny")

    def test_prefixed_tools_registered_and_old_names_absent(self):
        self.assertIn("core_configure_device", REGISTERED_TOOL_NAMES)
        self.assertIn("diag_run_shell", REGISTERED_TOOL_NAMES)
        self.assertIn("files_download", REGISTERED_TOOL_NAMES)
        self.assertIn("interactive_run_tool", REGISTERED_TOOL_NAMES)
        self.assertIn("maint_apply_change", REGISTERED_TOOL_NAMES)
        self.assertNotIn("configure_device", REGISTERED_TOOL_NAMES)
        self.assertNotIn("run_shell_commands", REGISTERED_TOOL_NAMES)

    def test_single_mcp_keeps_internal_tool_groups(self):
        self.assertEqual(
            set(TOOL_GROUPS),
            {"core", "diag", "files", "interactive", "policy", "maint"},
        )
        self.assertEqual(
            [tool.__name__ for tool in TOOL_GROUPS["core"]],
            [
                "core_configure_device",
                "core_test_connection",
                "core_list_recent_runs",
                "core_get_run_detail",
            ],
        )
        self.assertEqual(
            [tool.__name__ for tool in TOOL_GROUPS["files"]],
            ["files_download"],
        )

    def test_maintenance_plan_generation_does_not_connect(self):
        with patch("ai_ssh_mcp.tools_maint.EmbeddedSSHSession") as session:
            result = maint_plan_change("service_restart", "network")
        self.assertTrue(result["ok"])
        self.assertEqual(result["results"]["change_type"], "service_restart")
        session.assert_not_called()

    def test_apply_change_requires_confirmation_before_connection(self):
        plan = plan_change("service_restart", "network")
        with patch("ai_ssh_mcp.tools_maint.EmbeddedSSHSession") as session:
            result = maint_apply_change(plan.plan_id, user_confirmed=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "require_user_confirmation")
        session.assert_not_called()

    def test_tampered_maintenance_plan_is_rejected(self):
        plan = plan_change("service_restart", "network")
        store = MaintenanceStore()
        state = store._load()
        state["maintenance_plans"][plan.plan_id]["steps"] = ["reboot"]
        store._save(state)
        with self.assertRaises(ValueError):
            verify_plan_integrity(store.get_plan(plan.plan_id))

    def test_runbook_validation_requires_rollback_for_high_risk(self):
        target = runbook_dir()
        target.mkdir(parents=True, exist_ok=True)
        path = target / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "summary": "Bad reboot",
                    "steps": ["reboot"],
                    "verification_steps": ["uptime"],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            load_runbook("bad")

    def test_runbook_creates_plan_without_execution_when_unconfirmed(self):
        target = runbook_dir()
        target.mkdir(parents=True, exist_ok=True)
        path = target / "restart_network.json"
        path.write_text(
            json.dumps(
                {
                    "summary": "Restart network service",
                    "backup_steps": ["service network status"],
                    "steps": ["service network restart"],
                    "verification_steps": ["service network status"],
                    "rollback_steps": ["service network restart"],
                }
            ),
            encoding="utf-8",
        )
        with patch("ai_ssh_mcp.tools_maint.EmbeddedSSHSession") as session:
            result = maint_runbook("restart_network", user_confirmed=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "require_user_confirmation")
        session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
