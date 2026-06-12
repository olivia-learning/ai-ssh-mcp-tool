from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from .config import DeviceConfig, load_config, save_config
from .credentials import CredentialStore
from .ssh_client import EmbeddedSSHSession
from .store import AuditStore


def core_configure_device(
    host: str,
    username: str,
    ssh_password: str,
    su_password: str,
    port: int = 22,
    connect_timeout: int = 15,
    command_timeout: int = 30,
    banner_timeout: int = 15,
    allow_unknown_host: bool = True,
) -> dict[str, Any]:
    config = DeviceConfig(
        host=host,
        username=username,
        port=port,
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
        banner_timeout=banner_timeout,
        allow_unknown_host=allow_unknown_host,
    )
    path = save_config(config)
    CredentialStore().set_device_secrets(config, ssh_password, su_password)
    public_config = asdict(config)
    public_config["config_path"] = str(path)
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": {"device": public_config},
        "audit_id": str(uuid.uuid4()),
        "message": "Device configuration saved. Passwords were stored in the local keyring.",
    }


def core_test_connection() -> dict[str, Any]:
    config = load_config()
    secrets = CredentialStore().get_device_secrets(config)
    with EmbeddedSSHSession(config, secrets) as session:
        result = session.test_connection()
    return {
        "ok": result.ok,
        "decision": "allow",
        "risk_level": "low",
        "results": asdict(result),
        "audit_id": str(uuid.uuid4()),
    }


def core_list_recent_runs(limit: int = 10) -> dict[str, Any]:
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": {"runs": AuditStore().list_recent_runs(limit=limit)},
        "audit_id": str(uuid.uuid4()),
    }


def core_get_run_detail(run_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "decision": "allow",
        "risk_level": "low",
        "results": {"run": AuditStore().get_run(run_id).to_dict()},
        "audit_id": str(uuid.uuid4()),
    }


CORE_TOOLS = [
    core_configure_device,
    core_test_connection,
    core_list_recent_runs,
    core_get_run_detail,
]
