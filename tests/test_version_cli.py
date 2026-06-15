import subprocess
import sys
import unittest
from pathlib import Path


class VersionCliTests(unittest.TestCase):
    def test_module_version_does_not_require_mcp_dependency(self):
        project_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "ai_ssh_mcp", "--version"],
            cwd=str(project_root),
            env={"PYTHONPATH": str(project_root / "src")},
            text=True,
            capture_output=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ai-ssh-mcp 0.1.0", completed.stdout)


if __name__ == "__main__":
    unittest.main()
