import unittest
import socket
import struct
import tempfile
from pathlib import Path

from ai_ssh_mcp.ssh_client import (
    build_tftp_put_command,
    safe_local_filename,
    unique_filename,
    validate_remote_file_path,
)
from ai_ssh_mcp.tftp import TftpReceiveServer


class DownloadPathTests(unittest.TestCase):
    def test_valid_remote_path(self):
        validate_remote_file_path("/var/log/messages")

    def test_rejects_relative_remote_path(self):
        with self.assertRaises(ValueError):
            validate_remote_file_path("var/log/messages")

    def test_rejects_wildcard_remote_path(self):
        with self.assertRaises(ValueError):
            validate_remote_file_path("/var/log/*.log")

    def test_rejects_sensitive_remote_path(self):
        with self.assertRaises(ValueError):
            validate_remote_file_path("/etc/shadow")

    def test_safe_local_filename(self):
        self.assertEqual(safe_local_filename("/var/log/messages"), "messages")
        self.assertEqual(safe_local_filename("/tmp/a b.txt"), "a_b.txt")

    def test_unique_filename(self):
        used = {"messages"}
        self.assertEqual(unique_filename("messages", used), "messages_2")

    def test_build_tftp_put_command(self):
        command = build_tftp_put_command(
            remote_path="/var/log/messages",
            remote_filename="messages",
            server_host="192.0.2.20",
            server_port=6969,
        )
        self.assertEqual(
            command,
            "tftp -p -l '/var/log/messages' -r 'messages' '192.0.2.20' 6969",
        )

    def test_tftp_receive_server_accepts_wrq_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "messages"
            server = TftpReceiveServer(
                bind_host="127.0.0.1",
                port=0,
                expected_filename="messages",
                destination_path=destination,
                timeout_seconds=3,
            )
            server.start()
            assert server.bound_port is not None
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.settimeout(3)
                wrq = struct.pack("!H", 2) + b"messages\x00octet\x00"
                client.sendto(wrq, ("127.0.0.1", server.bound_port))
                ack0, server_tid = client.recvfrom(516)
                self.assertEqual(ack0, struct.pack("!HH", 4, 0))
                client.sendto(struct.pack("!HH", 3, 1) + b"hello\n", server_tid)
                ack1, _ = client.recvfrom(516)
                self.assertEqual(ack1, struct.pack("!HH", 4, 1))
            result = server.wait()
            self.assertTrue(result.ok)
            self.assertEqual(destination.read_text(encoding="utf-8"), "hello\n")


if __name__ == "__main__":
    unittest.main()
