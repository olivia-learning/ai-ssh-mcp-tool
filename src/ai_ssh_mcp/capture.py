from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import load_config
from .credentials import CredentialStore
from .security import truncate_output
from .ssh_client import EmbeddedSSHSession, shell_single_quote, strip_interactive_echo_and_prompt
from .store import CommandResult


DEBUGGING_TOOL_PATTERN = re.compile(r"^\./[A-Za-z0-9._-]*debugging[A-Za-z0-9._-]*(?:\s+[A-Za-z0-9._=:/-]+)*$", re.IGNORECASE)
SAFE_CAPTURE_COMMAND_PATTERN = re.compile(r"^(chl|rp)\b[A-Za-z0-9_ ./:=,@+-]*$", re.IGNORECASE)
TTY_PATTERN = re.compile(r"^/dev/(pts/\d+|tty[A-Za-z0-9_.-]+)$")
PROCESS_LINE_PATTERN = re.compile(r"^\s*(?P<pid>\d+)\s+(?P<body>.+)$")
HELP_COMMAND_PATTERN = re.compile(r"^\s*(?P<command>chl|rp)\b(?P<usage>.*)$", re.IGNORECASE)
TOP_COMMAND_PATTERN = re.compile(
    r"^top(?:\s+-[A-Za-z0-9]+(?:\s+\d+)?)?(?:\s+-[A-Za-z0-9]+(?:\s+\d+)?)*"
    r"(?:\s+\|\s+head\s+-n\s+\d+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessCandidate:
    pid: str
    name: str
    raw_line: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HelpCommandCandidate:
    command: str
    usage: str
    raw_line: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class CapturePlan:
    plan_id: str
    capture_id: str
    modules: list[str]
    module_process_map: dict[str, Any]
    tty: str
    commands: list[str]
    command_hash: str
    created_at: str
    status: str = "planned"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecordingInfo:
    temp_path: str
    started_at: str
    stopped_at: str | None = None
    size_bytes: int = 0
    line_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CaptureSession:
    def __init__(
        self,
        capture_id: str,
        session_a,
        session_b,
        work_dir: str,
        tool_command: str,
        tool_prompt_pattern: str,
        top_output: str,
        tty: str,
        help_output: str,
        timeout_seconds: int,
    ) -> None:
        self.capture_id = capture_id
        self.session_a = session_a
        self.session_b = session_b
        self.work_dir = work_dir
        self.tool_command = tool_command
        self.tool_prompt_pattern = tool_prompt_pattern
        self.top_output = top_output
        self.tty = tty
        self.help_output = help_output
        self.created_at = datetime.now(timezone.utc)
        self.expires_at = self.created_at + timedelta(seconds=timeout_seconds)
        self.plan: CapturePlan | None = None
        self.status = "prepared"
        self.recording: RecordingInfo | None = None
        self._recording_thread: threading.Thread | None = None
        self._recording_stop: threading.Event | None = None
        self._lock = threading.RLock()

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "work_dir": self.work_dir,
            "tool_command": self.tool_command,
            "tty": self.tty,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "plan": self.plan.to_dict() if self.plan else None,
            "recording": self.recording.to_dict() if self.recording else None,
        }

    def close(self) -> dict[str, Any]:
        with self._lock:
            if self._recording_thread and self._recording_thread.is_alive():
                self.stop_recording()
            try:
                self.session_a._send_line("\x03")
            except Exception:
                pass
            for session in (self.session_a, self.session_b):
                try:
                    session.close()
                except Exception:
                    pass
            self.status = "closed"
            return self.to_dict()

    def start_recording(self) -> dict[str, Any]:
        with self._lock:
            if self.status == "closed":
                raise ValueError("capture session is closed")
            if self._recording_thread and self._recording_thread.is_alive():
                raise ValueError("recording is already active")
            drain_available_output(self.session_b)
            fd, temp_path = tempfile.mkstemp(prefix=f"capture_{self.capture_id}_", suffix=".log")
            Path(temp_path).parent.mkdir(parents=True, exist_ok=True)
            self._recording_stop = threading.Event()
            started_at = datetime.now(timezone.utc).isoformat()
            self.recording = RecordingInfo(temp_path=temp_path, started_at=started_at)
            self._recording_thread = threading.Thread(
                target=record_channel_output,
                args=(self.session_b, temp_path, self._recording_stop, fd),
                daemon=True,
            )
            self._recording_thread.start()
            self.status = "recording"
            return self.recording.to_dict()

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            if not self._recording_thread or not self._recording_thread.is_alive() or not self._recording_stop:
                raise ValueError("recording is not active")
            self._recording_stop.set()
            self._recording_thread.join(timeout=5)
            if self._recording_thread.is_alive():
                raise TimeoutError("recording thread did not stop in time")
            assert self.recording is not None
            stats = file_stats(Path(self.recording.temp_path))
            self.recording = RecordingInfo(
                temp_path=self.recording.temp_path,
                started_at=self.recording.started_at,
                stopped_at=datetime.now(timezone.utc).isoformat(),
                size_bytes=stats["size_bytes"],
                line_count=stats["line_count"],
            )
            self.status = "applied"
            return self.recording.to_dict()

    def save_recording(self, destination_path: str, overwrite: bool = False) -> dict[str, Any]:
        if not destination_path or not destination_path.strip():
            raise ValueError("destination_path is required")
        if "\x00" in destination_path or "\n" in destination_path or "\r" in destination_path:
            raise ValueError("destination_path must be a single path")
        if self.recording is None:
            raise ValueError("no recording is available to save")
        source = Path(self.recording.temp_path)
        if not source.exists():
            raise FileNotFoundError(f"recording temp file not found: {source}")
        destination = Path(destination_path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(f"destination already exists: {destination}")
        existed_before = destination.exists()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        stats = file_stats(destination)
        return {
            "source_path": str(source),
            "destination_path": str(destination),
            "size_bytes": stats["size_bytes"],
            "line_count": stats["line_count"],
            "overwritten": existed_before and overwrite,
        }


class CaptureManager:
    def __init__(
        self,
        session_factory: Callable[[], Any] | None = None,
        timeout_seconds: int = 3600,
    ) -> None:
        self._session_factory = session_factory or make_default_session
        self._timeout_seconds = timeout_seconds
        self._sessions: dict[str, CaptureSession] = {}
        self._lock = threading.RLock()

    def prepare(
        self,
        work_dir: str,
        tool_command: str,
        tool_prompt_pattern: str,
        top_command: str = "top -bn1",
        session_timeout_seconds: int | None = None,
        startup_timeout: int = 30,
        help_timeout: int = 30,
    ) -> dict[str, Any]:
        validate_capture_prepare_request(work_dir, tool_command, tool_prompt_pattern, top_command)
        session_a = self._new_connected_session()
        session_b = self._new_connected_session()
        try:
            top_result = session_a._run_shell_command(
                top_command,
                purpose="capture top snapshot",
                timeout=startup_timeout,
            )
            tty_result = session_b._run_shell_command("tty", purpose="capture output tty", timeout=10)
            tty = tty_result.stdout.strip().splitlines()[-1].strip()
            validate_tty(tty)
            prompt = re.compile(tool_prompt_pattern, re.MULTILINE)
            session_a._send_line(f"cd {shell_single_quote(work_dir)}")
            session_a._read_until_quiet(timeout=2)
            session_a._send_line(tool_command)
            startup_output = session_a._read_until_prompt_quiet(prompt, startup_timeout, 0.8)
            if not prompt.search(startup_output):
                raise ValueError("debugging tool prompt was not detected")
            session_a._send_line("help")
            raw_help = session_a._read_until_prompt_quiet(prompt, help_timeout, 0.8)
            help_output = strip_interactive_echo_and_prompt(raw_help, "help", prompt)
            capture_id = str(uuid.uuid4())
            session = CaptureSession(
                capture_id=capture_id,
                session_a=session_a,
                session_b=session_b,
                work_dir=work_dir,
                tool_command=tool_command,
                tool_prompt_pattern=tool_prompt_pattern,
                top_output=top_result.stdout,
                tty=tty,
                help_output=help_output,
                timeout_seconds=session_timeout_seconds or self._timeout_seconds,
            )
            with self._lock:
                self._sessions[capture_id] = session
            return prepare_result(session)
        except Exception:
            safe_close(session_a)
            safe_close(session_b)
            raise

    def build_plan(
        self,
        capture_id: str,
        modules: list[str],
        module_process_map: dict[str, Any],
        chl_command_template: str,
        rp_command_template: str,
    ) -> dict[str, Any]:
        session = self.get(capture_id)
        commands = render_capture_commands(
            modules=modules,
            module_process_map=module_process_map,
            tty=session.tty,
            chl_command_template=chl_command_template,
            rp_command_template=rp_command_template,
        )
        plan = CapturePlan(
            plan_id=str(uuid.uuid4()),
            capture_id=capture_id,
            modules=list(modules),
            module_process_map=dict(module_process_map),
            tty=session.tty,
            commands=commands,
            command_hash=hash_capture_plan(capture_id, modules, module_process_map, session.tty, commands),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.plan = plan
        session.status = "planned"
        return plan.to_dict()

    def apply_plan(self, capture_id: str, plan_id: str, user_confirmed: bool = False) -> dict[str, Any]:
        if not user_confirmed:
            raise PermissionError("capture plan requires final user confirmation")
        session = self.get(capture_id)
        if session.plan is None:
            raise ValueError("capture plan has not been built")
        plan = session.plan
        if plan.plan_id != plan_id:
            raise ValueError("plan_id does not match the current capture plan")
        verify_capture_plan_integrity(plan)
        prompt = re.compile(session.tool_prompt_pattern, re.MULTILINE)
        results: list[dict[str, Any]] = []
        for command in plan.commands:
            started = time.monotonic()
            session.session_a._send_line(command)
            raw = session.session_a._read_until_prompt_quiet(prompt, 30, 0.8)
            output = strip_interactive_echo_and_prompt(raw, command, prompt)
            output, truncated = truncate_output(output)
            results.append(
                {
                    "command": command,
                    "output": output,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "truncated": truncated,
                }
            )
        session.plan = CapturePlan(**{**plan.to_dict(), "status": "applied"})
        session.status = "applied"
        return {"plan": session.plan.to_dict(), "command_results": results}

    def start_recording(self, capture_id: str) -> dict[str, Any]:
        return self.get(capture_id).start_recording()

    def stop_recording(self, capture_id: str) -> dict[str, Any]:
        return self.get(capture_id).stop_recording()

    def save_recording(self, capture_id: str, destination_path: str, overwrite: bool = False) -> dict[str, Any]:
        return self.get(capture_id).save_recording(destination_path, overwrite=overwrite)

    def status(self, capture_id: str) -> dict[str, Any]:
        return self.get(capture_id).to_dict()

    def close(self, capture_id: str) -> dict[str, Any]:
        session = self.get(capture_id)
        result = session.close()
        with self._lock:
            self._sessions.pop(capture_id, None)
        return result

    def get(self, capture_id: str) -> CaptureSession:
        with self._lock:
            try:
                session = self._sessions[capture_id]
            except KeyError as exc:
                raise KeyError(f"Unknown capture_id: {capture_id}") from exc
            if session.is_expired():
                session.close()
                self._sessions.pop(capture_id, None)
                raise TimeoutError(f"capture session expired: {capture_id}")
            return session

    def _new_connected_session(self):
        session = self._session_factory()
        session.connect()
        return session


def make_default_session() -> EmbeddedSSHSession:
    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    return EmbeddedSSHSession(config, secrets)


def prepare_result(session: CaptureSession) -> dict[str, Any]:
    top_output, top_truncated = truncate_output(session.top_output)
    help_output, help_truncated = truncate_output(session.help_output)
    return {
        "capture_id": session.capture_id,
        "tty": session.tty,
        "work_dir": session.work_dir,
        "tool_command": session.tool_command,
        "top_output": top_output,
        "top_truncated": top_truncated,
        "process_candidates": [item.to_dict() for item in parse_top_process_candidates(session.top_output)],
        "help_output": help_output,
        "help_truncated": help_truncated,
        "help_command_candidates": [item.to_dict() for item in parse_help_command_candidates(session.help_output)],
        "expires_at": session.expires_at.isoformat(),
    }


def validate_capture_prepare_request(
    work_dir: str,
    tool_command: str,
    tool_prompt_pattern: str,
    top_command: str,
) -> None:
    if not work_dir or not work_dir.startswith("/"):
        raise ValueError("work_dir must be an absolute path")
    if "\x00" in work_dir or "\n" in work_dir or "\r" in work_dir or ".." in work_dir.split("/"):
        raise ValueError("work_dir must be a single safe absolute path")
    if not DEBUGGING_TOOL_PATTERN.match(tool_command.strip()):
        raise ValueError("tool_command must look like './...debugging...' with simple arguments")
    if not TOP_COMMAND_PATTERN.match(top_command.strip()):
        raise ValueError("top_command must be a simple top snapshot command")
    if not tool_prompt_pattern:
        raise ValueError("tool_prompt_pattern is required")
    re.compile(tool_prompt_pattern)


def validate_tty(value: str) -> None:
    if not TTY_PATTERN.match(value):
        raise ValueError(f"tty output is not a supported terminal path: {value!r}")


def parse_top_process_candidates(output: str) -> list[ProcessCandidate]:
    candidates: list[ProcessCandidate] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        match = PROCESS_LINE_PATTERN.match(line)
        if not match:
            continue
        pid = match.group("pid")
        columns = match.group("body").split()
        if not columns:
            continue
        name = columns[-1]
        key = (pid, name)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(ProcessCandidate(pid=pid, name=name, raw_line=line.strip()))
    return candidates[:100]


def parse_help_command_candidates(output: str) -> list[HelpCommandCandidate]:
    candidates: list[HelpCommandCandidate] = []
    for line in output.splitlines():
        match = HELP_COMMAND_PATTERN.match(line)
        if not match:
            continue
        candidates.append(
            HelpCommandCandidate(
                command=match.group("command").lower(),
                usage=match.group("usage").strip(),
                raw_line=line.strip(),
            )
        )
    return candidates


def render_capture_commands(
    modules: list[str],
    module_process_map: dict[str, Any],
    tty: str,
    chl_command_template: str,
    rp_command_template: str,
) -> list[str]:
    if not modules:
        raise ValueError("modules is required")
    if not isinstance(module_process_map, dict):
        raise ValueError("module_process_map must be an object")
    commands: list[str] = []
    for module in modules:
        module = str(module).strip()
        if not module:
            raise ValueError("module names must not be empty")
        if module not in module_process_map:
            raise ValueError(f"module_process_map is missing module: {module}")
        process_value = module_process_map[module]
        pid, process = normalize_process_value(process_value)
        context = {
            "module": module,
            "process": process,
            "pid": pid,
            "tty": tty,
        }
        commands.append(render_command_template(chl_command_template, context))
        commands.append(render_command_template(rp_command_template, context))
    deduped: list[str] = []
    for command in commands:
        if command not in deduped:
            validate_capture_command_text(command, allowed_prefixes=("chl", "rp"))
            deduped.append(command)
    return deduped


def normalize_process_value(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        pid = str(value.get("pid", "")).strip()
        process = str(value.get("name") or value.get("process") or value.get("module") or "").strip()
    else:
        text = str(value).strip()
        pid_match = re.search(r"\b\d+\b", text)
        pid = pid_match.group(0) if pid_match else text
        process = text
    if not pid:
        raise ValueError("process mapping must include a pid or process value")
    return pid, process or pid


def render_command_template(template: str, context: dict[str, str]) -> str:
    if not template or not template.strip():
        raise ValueError("command template is required")
    try:
        command = template.format(**context).strip()
    except KeyError as exc:
        raise ValueError(f"unsupported command template placeholder: {exc}") from exc
    return command


def validate_capture_command_text(
    command: str,
    allowed_prefixes: tuple[str, ...],
    allow_pipe: bool = False,
) -> None:
    if not command or not command.strip():
        raise ValueError("capture command must not be empty")
    if "\x00" in command or "\n" in command or "\r" in command:
        raise ValueError("capture command must be a single line")
    blocked = [";", "&&", "||", "`", "$(", ">", "<"]
    if not allow_pipe:
        blocked.append("|")
    if any(token in command for token in blocked):
        raise ValueError(f"capture command contains unsupported shell syntax: {command}")
    if not command.strip().lower().startswith(tuple(prefix.lower() for prefix in allowed_prefixes)):
        raise ValueError(f"capture command must start with one of: {', '.join(allowed_prefixes)}")
    if allowed_prefixes == ("chl", "rp") and not SAFE_CAPTURE_COMMAND_PATTERN.match(command.strip()):
        raise ValueError(f"capture command contains unsupported characters: {command}")


def hash_capture_plan(
    capture_id: str,
    modules: list[str],
    module_process_map: dict[str, Any],
    tty: str,
    commands: list[str],
) -> str:
    payload = json.dumps(
        {
            "capture_id": capture_id,
            "modules": modules,
            "module_process_map": module_process_map,
            "tty": tty,
            "commands": commands,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_capture_plan_integrity(plan: CapturePlan) -> None:
    if plan.status != "planned":
        raise ValueError(f"capture plan is not executable because status is {plan.status!r}")
    expected = hash_capture_plan(
        capture_id=plan.capture_id,
        modules=plan.modules,
        module_process_map=plan.module_process_map,
        tty=plan.tty,
        commands=plan.commands,
    )
    if expected != plan.command_hash:
        raise ValueError("capture plan command hash mismatch; refusing to execute")


def record_channel_output(session, temp_path: str, stop_event: threading.Event, fd: int) -> None:
    with open(fd, "w", encoding="utf-8", errors="replace", newline="") as handle:
        while not stop_event.is_set():
            chunk = read_available_output(session)
            if chunk:
                handle.write(chunk)
                handle.flush()
            else:
                time.sleep(0.05)
        chunk = read_available_output(session)
        if chunk:
            handle.write(chunk)
            handle.flush()


def drain_available_output(session) -> str:
    chunks: list[str] = []
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        chunk = read_available_output(session)
        if chunk:
            chunks.append(chunk)
            deadline = time.monotonic() + 0.1
        else:
            time.sleep(0.05)
    return "".join(chunks)


def read_available_output(session) -> str:
    channel = session._require_channel()
    chunks: list[str] = []
    while channel.recv_ready():
        chunks.append(channel.recv(65535).decode("utf-8", errors="replace"))
    return "".join(chunks)


def file_stats(path: Path) -> dict[str, int]:
    data = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return {
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "line_count": len(data.splitlines()),
    }


def safe_close(session) -> None:
    try:
        session.close()
    except Exception:
        pass


CAPTURE_MANAGER = CaptureManager()
