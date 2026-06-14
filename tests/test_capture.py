import tempfile
import time
import unittest
from pathlib import Path

from ai_ssh_mcp.capture import (
    CaptureManager,
    CapturePlan,
    hash_capture_plan,
    parse_help_command_candidates,
    parse_top_process_candidates,
    validate_capture_prepare_request,
)
from ai_ssh_mcp.store import CommandResult


TOP_OUTPUT = """Mem: 1000K used, 2000K free
  PID USER       VSZ STAT COMMAND
 1234 root      100 S    module_proc_a
 2345 root      200 S    module_proc_b
"""

HELP_OUTPUT = """debugging help
chl <pid> <module> enable channel
rp <module> <tty> redirect stream
"""


class FakeChannel:
    def __init__(self):
        self.chunks: list[bytes] = []

    def push(self, text: str) -> None:
        self.chunks.append(text.encode("utf-8"))

    def recv_ready(self) -> bool:
        return bool(self.chunks)

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0)


class FakeSession:
    def __init__(self, role: str):
        self.role = role
        self.channel = FakeChannel()
        self.sent: list[str] = []
        self.closed = False

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def _run_shell_command(self, command: str, purpose: str = "", timeout: int = 30) -> CommandResult:
        if command.startswith("top"):
            stdout = TOP_OUTPUT
        elif command == "tty":
            stdout = "/dev/pts/7\n"
        else:
            stdout = "ok\n"
        return CommandResult(
            command=command,
            purpose=purpose,
            stdout=stdout,
            stderr="",
            exit_status=0,
            duration_ms=1,
        )

    def _send_line(self, value: str) -> None:
        self.sent.append(value)

    def _read_until_quiet(self, timeout: int) -> str:
        return ""

    def _read_until_prompt_quiet(self, prompt, timeout: int, settle_seconds: float) -> str:
        last = self.sent[-1] if self.sent else ""
        if last == "./stream_debugging":
            return "debug>"
        if last == "help":
            return "help\n" + HELP_OUTPUT + "\ndebug>"
        return f"{last}\nok\ndebug>"

    def _require_channel(self):
        return self.channel


class FakeSessionFactory:
    def __init__(self):
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        role = "a" if len(self.sessions) % 2 == 0 else "b"
        session = FakeSession(role)
        self.sessions.append(session)
        return session


class CaptureTests(unittest.TestCase):
    def make_prepared_manager(self) -> tuple[CaptureManager, FakeSessionFactory, str]:
        factory = FakeSessionFactory()
        manager = CaptureManager(session_factory=factory, timeout_seconds=3600)
        result = manager.prepare(
            work_dir="/opt/tools",
            tool_command="./stream_debugging",
            tool_prompt_pattern=r"debug>$",
        )
        return manager, factory, result["capture_id"]

    def test_validate_debugging_tool_and_work_dir(self):
        validate_capture_prepare_request(
            "/opt/tools",
            "./stream_debugging",
            r"debug>$",
            "top -bn1",
        )
        validate_capture_prepare_request(
            "/opt/tools",
            "./stream_debugging",
            r"debug>$",
            "top -b -n 1",
        )
        with self.assertRaises(ValueError):
            validate_capture_prepare_request("/opt/tools", "./stream_tool", r"debug>$", "top -bn1")
        with self.assertRaises(ValueError):
            validate_capture_prepare_request("relative", "./stream_debugging", r"debug>$", "top -bn1")
        with self.assertRaises(ValueError):
            validate_capture_prepare_request("/opt/tools", "./stream_debugging", r"debug>$", "top | sh")

    def test_parse_top_process_candidates(self):
        candidates = parse_top_process_candidates(TOP_OUTPUT)
        self.assertEqual(candidates[0].pid, "1234")
        self.assertEqual(candidates[0].name, "module_proc_a")

    def test_parse_help_command_candidates(self):
        candidates = parse_help_command_candidates(HELP_OUTPUT)
        self.assertEqual([item.command for item in candidates], ["chl", "rp"])

    def test_prepare_collects_top_tty_and_help(self):
        manager, _factory, capture_id = self.make_prepared_manager()
        status = manager.status(capture_id)
        self.assertEqual(status["tty"], "/dev/pts/7")
        self.assertEqual(status["status"], "prepared")
        prepared = manager.get(capture_id)
        self.assertIn("chl <pid>", prepared.help_output)

    def test_build_plan_requires_module_mapping_and_renders_commands(self):
        manager, _factory, capture_id = self.make_prepared_manager()
        plan = manager.build_plan(
            capture_id=capture_id,
            modules=["mod_a"],
            module_process_map={"mod_a": {"pid": "1234", "name": "module_proc_a"}},
            chl_command_template="chl {pid} {module}",
            rp_command_template="rp {module} {tty}",
        )
        self.assertEqual(plan["commands"], ["chl 1234 mod_a", "rp mod_a /dev/pts/7"])

    def test_apply_plan_requires_confirmation(self):
        manager, _factory, capture_id = self.make_prepared_manager()
        plan = manager.build_plan(
            capture_id,
            ["mod_a"],
            {"mod_a": "1234 module_proc_a"},
            "chl {pid} {module}",
            "rp {module} {tty}",
        )
        with self.assertRaises(PermissionError):
            manager.apply_plan(capture_id, plan["plan_id"], user_confirmed=False)

    def test_tampered_plan_is_rejected(self):
        manager, _factory, capture_id = self.make_prepared_manager()
        plan_dict = manager.build_plan(
            capture_id,
            ["mod_a"],
            {"mod_a": "1234 module_proc_a"},
            "chl {pid} {module}",
            "rp {module} {tty}",
        )
        session = manager.get(capture_id)
        session.plan = CapturePlan(**{**plan_dict, "commands": ["rp mod_a /dev/pts/999"]})
        with self.assertRaises(ValueError):
            manager.apply_plan(capture_id, plan_dict["plan_id"], user_confirmed=True)

    def test_confirmed_plan_executes_chl_and_rp(self):
        manager, factory, capture_id = self.make_prepared_manager()
        plan = manager.build_plan(
            capture_id,
            ["mod_a"],
            {"mod_a": "1234 module_proc_a"},
            "chl {pid} {module}",
            "rp {module} {tty}",
        )
        result = manager.apply_plan(capture_id, plan["plan_id"], user_confirmed=True)
        self.assertEqual([item["command"] for item in result["command_results"]], ["chl 1234 mod_a", "rp mod_a /dev/pts/7"])
        self.assertIn("chl 1234 mod_a", factory.sessions[0].sent)

    def test_recording_start_stop_captures_b_output(self):
        manager, factory, capture_id = self.make_prepared_manager()
        manager.start_recording(capture_id)
        factory.sessions[1].channel.push("line1\n")
        factory.sessions[1].channel.push("line2\n")
        time.sleep(0.2)
        result = manager.stop_recording(capture_id)
        self.assertEqual(result["line_count"], 2)
        self.assertGreater(result["size_bytes"], 0)

    def test_save_recording_refuses_overwrite_by_default(self):
        manager, factory, capture_id = self.make_prepared_manager()
        manager.start_recording(capture_id)
        factory.sessions[1].channel.push("line1\n")
        time.sleep(0.2)
        manager.stop_recording(capture_id)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "capture.log"
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                manager.save_recording(capture_id, str(destination))
            result = manager.save_recording(capture_id, str(destination), overwrite=True)
            self.assertEqual(result["destination_path"], str(destination.resolve()))


if __name__ == "__main__":
    unittest.main()
