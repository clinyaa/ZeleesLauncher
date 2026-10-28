"""Discord Rich Presence: протокол и текст статуса."""
import json
import struct
import unittest
from unittest import mock

from launcher import discord_rpc as d


class FakeConn:
    """Discord на другом конце канала: отвечает на каждый кадр."""

    def __init__(self, fail_after=None):
        self.sent, self.buf, self.closed = [], b"", False
        self.fail_after = fail_after

    def write(self, data):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise OSError("pipe broken")
        op, n = struct.unpack("<II", data[:8])
        msg = json.loads(data[8:8 + n])
        self.sent.append((op, msg))
        reply = {"evt": "READY"} if op == d.OP_HANDSHAKE else {"cmd": "SET_ACTIVITY", "nonce": msg["nonce"]}
        self.buf += d.encode(d.OP_FRAME, reply)

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        self.closed = True


class ActivityTest(unittest.TestCase):
    def test_frame_roundtrip(self):
        data = d.encode(d.OP_FRAME, {"a": "ё"})
        op, n = d.decode_header(data[:8])
        self.assertEqual((op, json.loads(data[8:].decode("utf-8"))), (d.OP_FRAME, {"a": "ё"}))
        self.assertEqual(n, len(data) - 8)

    def test_server_text_hides_private_addresses(self):
        self.assertEqual(d.server_text(None), "Одиночная игра")
        self.assertEqual(d.server_text(("mc.hypixel.net", 0)), "Сервер mc.hypixel.net")
        self.assertEqual(d.server_text(("8.8.8.8", 25565)), "Сервер 8.8.8.8")
        for host in ("192.168.0.5", "26.12.0.1", "25.1.2.3", "127.0.0.1", "localhost", "mypc"):
            with self.subTest(host=host):
                self.assertEqual(d.server_text((host, 0)), "Игра по сети")

    def test_build_activity(self):
        a = d.build_activity("1.21.1", "Fabric", ("play.example.com", 0), started=1000)
        self.assertEqual(a["details"], "Minecraft 1.21.1 · Fabric")
        self.assertEqual(a["state"], "Сервер play.example.com")
        self.assertEqual(a["timestamps"], {"start": 1000})
        self.assertTrue(a["assets"]["large_image"].startswith("https://"))
        self.assertEqual(d.build_activity("1.8.9")["details"], "Minecraft 1.8.9")


class PresenceTest(unittest.TestCase):
    def test_handshake_then_activity(self):
        conn = FakeConn()
        with mock.patch.object(d, "_Conn", return_value=conn):
            p = d.Presence(client_id="123")
            p._send(d.build_activity("1.21"))
            p._send(None)
        ops = [op for op, _m in conn.sent]
        self.assertEqual(ops, [d.OP_HANDSHAKE, d.OP_FRAME, d.OP_FRAME])
        self.assertEqual(conn.sent[0][1], {"v": 1, "client_id": "123"})
        self.assertEqual(conn.sent[1][1]["cmd"], "SET_ACTIVITY")
        self.assertEqual(conn.sent[1][1]["args"]["activity"]["details"], "Minecraft 1.21")
        self.assertIsNone(conn.sent[2][1]["args"]["activity"])

    def test_reconnects_after_discord_restart(self):
        broken, fresh = FakeConn(fail_after=2), FakeConn()
        with mock.patch.object(d, "_Conn", side_effect=[broken, fresh]):
            p = d.Presence()
            p._send(d.build_activity("1.21"))
            p._send(d.build_activity("1.21"))  # старый канал закрыт — переподключение
        self.assertTrue(broken.closed)
        self.assertEqual([op for op, _m in fresh.sent], [d.OP_HANDSHAKE, d.OP_FRAME])

    def test_discord_not_running_is_silent(self):
        logs = []
        with mock.patch.object(d, "_Conn", side_effect=d.NotRunning("нет")):
            d.Presence(log=logs.append)._send(d.build_activity("1.21"))
        self.assertEqual(logs, [])

    def test_error_reply_is_logged(self):
        conn = FakeConn()
        orig = conn.write

        def write(data):
            orig(data)
            if struct.unpack("<II", data[:8])[0] == d.OP_FRAME:
                conn.buf = d.encode(d.OP_FRAME, {"evt": "ERROR", "data": {"message": "bad"}})
        conn.write = write
        logs = []
        with mock.patch.object(d, "_Conn", return_value=conn):
            d.Presence(log=logs.append)._send(d.build_activity("1.21"))
        self.assertEqual(len(logs), 1)
        self.assertIn("bad", logs[0])


if __name__ == "__main__":
    unittest.main()
