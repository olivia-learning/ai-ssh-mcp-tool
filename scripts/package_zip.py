from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path


EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    "ut-cover-agent-tool",
    ".ut-cover",
}

EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_FILE_NAMES = {"ai-ssh-mcp-tool.zip", "ut-cover-agent-tool.zip"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build ai-ssh-mcp-tool.zip")
    parser.add_argument("--output", default="ai-ssh-mcp-tool.zip", help="Output zip path.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    output = (project_root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        names: list[str] = []
        for path in sorted(project_root.rglob("*")):
            if path == output or should_exclude(path, project_root):
                continue
            arcname = f"ai-ssh-mcp-tool/{path.relative_to(project_root).as_posix()}"
            archive.write(path, arcname)
            if path.is_file():
                names.append(arcname)
        manifest_name = "ai-ssh-mcp-tool/ZIP_MANIFEST.json"
        manifest = build_manifest(project_root, names + [manifest_name])
        archive.writestr(manifest_name, json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"Wrote {output}")
    return 0


def should_exclude(path: Path, project_root: Path) -> bool:
    rel = path.relative_to(project_root)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return True
    if any(part.endswith(".egg-info") for part in rel.parts):
        return True
    if path.is_dir():
        return False
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    if path.name.endswith(".egg-info"):
        return True
    return False


def build_manifest(project_root: Path, names: list[str]) -> dict[str, object]:
    version_path = project_root / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.exists() else "unknown"
    return {
        "tool": "ai-ssh-mcp",
        "version": version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(project_root),
        "files": sorted(names),
    }


def git_commit(project_root: Path) -> str:
    git = find_git()
    if not git:
        return "unknown"
    try:
        completed = subprocess.run(
            [str(git), "rev-parse", "HEAD"],
            cwd=str(project_root),
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def find_git() -> str | None:
    env_git = os.environ.get("UT_COVER_GIT")
    if env_git and Path(env_git).exists():
        return env_git
    path_git = shutil.which("git")
    if path_git:
        return path_git
    candidates: list[Path] = []
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.extend(Path(local_app).glob("GitHubDesktop/app-*/resources/app/git/cmd/git.exe"))
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Git" / "cmd" / "git.exe")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Git" / "cmd" / "git.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
