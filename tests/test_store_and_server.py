import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ssh_mcp.config import APP_DIR_ENV
from ai_ssh_mcp.security import generate_plan
from ai_ssh_mcp.tools_diag import diag_plan_task, diag_run_plan
from ai_ssh_mcp.store import AuditStore


class StoreAndServerTests(unittest.TestCase):
    def test_plan_round_trip_and_recent_runs_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AuditStore(Path(tmp) / "state.json")
            plan = generate_plan("检查磁盘空间")
            store.save_plan(plan)
            loaded = store.get_plan(plan.approval_id)
            self.assertEqual(loaded.command_hash, plan.command_hash)
            self.assertEqual(store.list_recent_runs(), [])

    def test_run_requires_user_confirmation_before_any_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get(APP_DIR_ENV)
            os.environ[APP_DIR_ENV] = tmp
            try:
                plan = diag_plan_task("检查网络")
                with patch("ai_ssh_mcp.tools_diag.EmbeddedSSHSession") as session:
                    result = diag_run_plan(plan["audit_id"], user_confirmed=False)
                self.assertFalse(result["ok"])
                self.assertEqual(result["decision"], "require_user_confirmation")
                session.assert_not_called()
            finally:
                if old is None:
                    os.environ.pop(APP_DIR_ENV, None)
                else:
                    os.environ[APP_DIR_ENV] = old


if __name__ == "__main__":
    unittest.main()
