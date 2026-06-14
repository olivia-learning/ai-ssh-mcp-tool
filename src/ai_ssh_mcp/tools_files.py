from __future__ import annotations

from typing import Any

from .config import load_config
from .credentials import CredentialStore
from .policy import DECISION_ALLOW, evaluate_operation
from .responses import execution_result, policy_block
from .ssh_client import EmbeddedSSHSession


def files_download(
    remote_paths: list[str],
    local_dir: str,
    user_confirmed: bool = False,
    tftp_bind_host: str = "0.0.0.0",
    tftp_server_host: str | None = None,
    tftp_port: int = 6969,
    max_bytes_per_file: int = 50 * 1024 * 1024,
    transfer_timeout: int = 60,
) -> dict[str, Any]:
    decision = evaluate_operation("files_download", user_confirmed=user_confirmed)
    if decision.decision != DECISION_ALLOW:
        return policy_block(decision)

    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    with EmbeddedSSHSession(config, secrets) as session:
        downloaded = session.download_files_tftp(
            remote_paths=remote_paths,
            local_dir=local_dir,
            tftp_bind_host=tftp_bind_host,
            tftp_server_host=tftp_server_host,
            tftp_port=tftp_port,
            max_bytes_per_file=max_bytes_per_file,
            transfer_timeout=transfer_timeout,
        )
    return execution_result(
        decision,
        [
            {
                "remote_path": item.remote_path,
                "local_path": item.local_path,
                "size_bytes": item.size_bytes,
                "ok": item.ok,
                "transfer_method": "tftp",
                "message": item.message,
            }
            for item in downloaded
        ],
    )


FILES_TOOLS = [files_download]
