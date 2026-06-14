from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path


TFTP_DATA_SIZE = 512
OP_WRQ = 2
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5


@dataclass(frozen=True)
class TftpReceiveResult:
    filename: str
    path: str
    size_bytes: int
    ok: bool
    message: str = ""


class TftpReceiveServer:
    def __init__(
        self,
        bind_host: str,
        port: int,
        expected_filename: str,
        destination_path: Path,
        timeout_seconds: int = 60,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        if port < 0 or port > 65535:
            raise ValueError("TFTP port must be between 0 and 65535")
        self.bind_host = bind_host
        self.port = port
        self.expected_filename = expected_filename
        self.destination_path = destination_path
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.bound_port: int | None = None
        self.result: TftpReceiveResult | None = None
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise TimeoutError("Timed out starting local TFTP receive server")
        if self.error:
            raise self.error

    def wait(self) -> TftpReceiveResult:
        self._thread.join(timeout=self.timeout_seconds + 5)
        if self._thread.is_alive():
            raise TimeoutError("Timed out waiting for TFTP transfer to finish")
        if self.error:
            raise self.error
        if self.result is None:
            raise RuntimeError("TFTP transfer did not produce a result")
        return self.result

    def _run(self) -> None:
        try:
            self.destination_path.parent.mkdir(parents=True, exist_ok=True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listen_socket:
                listen_socket.bind((self.bind_host, self.port))
                listen_socket.settimeout(self.timeout_seconds)
                self.bound_port = int(listen_socket.getsockname()[1])
                self._ready.set()
                packet, client = listen_socket.recvfrom(65535)
                opcode = unpack_opcode(packet)
                if opcode != OP_WRQ:
                    send_error(listen_socket, client, 4, "Only TFTP WRQ upload is supported")
                    raise ValueError("Expected TFTP WRQ packet")
                filename, _mode = parse_wrq(packet)
                if filename != self.expected_filename:
                    send_error(listen_socket, client, 2, "Unexpected TFTP filename")
                    raise ValueError(f"Unexpected TFTP filename: {filename!r}")
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as transfer_socket:
                    transfer_socket.bind((self.bind_host, 0))
                    transfer_socket.settimeout(self.timeout_seconds)
                    transfer_socket.sendto(make_ack(0), client)
                    self.result = receive_data_blocks(
                        transfer_socket=transfer_socket,
                        client=client,
                        destination_path=self.destination_path,
                        filename=filename,
                        max_bytes=self.max_bytes,
                    )
        except BaseException as exc:
            self.error = exc
            self._ready.set()


def receive_data_blocks(
    transfer_socket: socket.socket,
    client: tuple[str, int],
    destination_path: Path,
    filename: str,
    max_bytes: int,
) -> TftpReceiveResult:
    total = 0
    expected_block = 1
    with destination_path.open("wb") as handle:
        while True:
            packet, sender = transfer_socket.recvfrom(4 + TFTP_DATA_SIZE)
            if sender != client:
                send_error(transfer_socket, sender, 5, "Unknown transfer id")
                continue
            opcode = unpack_opcode(packet)
            if opcode == OP_ERROR:
                raise RuntimeError(f"TFTP client returned error: {packet[4:].decode('ascii', errors='replace')}")
            if opcode != OP_DATA or len(packet) < 4:
                send_error(transfer_socket, client, 4, "Expected TFTP DATA packet")
                raise ValueError("Expected TFTP DATA packet")
            block = struct.unpack("!H", packet[2:4])[0]
            data = packet[4:]
            if block == expected_block:
                total += len(data)
                if total > max_bytes:
                    send_error(transfer_socket, client, 3, "File is too large")
                    raise ValueError(f"TFTP transfer exceeded max_bytes={max_bytes}")
                handle.write(data)
                expected_block = (expected_block + 1) % 65536
            transfer_socket.sendto(make_ack(block), client)
            if len(data) < TFTP_DATA_SIZE:
                break
    return TftpReceiveResult(
        filename=filename,
        path=str(destination_path),
        size_bytes=destination_path.stat().st_size,
        ok=True,
    )


def parse_wrq(packet: bytes) -> tuple[str, str]:
    parts = packet[2:].split(b"\x00")
    if len(parts) < 2:
        raise ValueError("Invalid TFTP WRQ packet")
    filename = parts[0].decode("utf-8", errors="replace")
    mode = parts[1].decode("ascii", errors="replace").lower()
    return filename, mode


def unpack_opcode(packet: bytes) -> int:
    if len(packet) < 2:
        raise ValueError("Invalid TFTP packet")
    return struct.unpack("!H", packet[:2])[0]


def make_ack(block: int) -> bytes:
    return struct.pack("!HH", OP_ACK, block)


def send_error(sock: socket.socket, target: tuple[str, int], code: int, message: str) -> None:
    payload = struct.pack("!HH", OP_ERROR, code) + message.encode("ascii", errors="replace") + b"\x00"
    sock.sendto(payload, target)


def infer_local_host_for_device(device_host: str, device_port: int = 22) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((device_host, device_port))
        return str(probe.getsockname()[0])


def wait_briefly_for_server() -> None:
    time.sleep(0.05)
