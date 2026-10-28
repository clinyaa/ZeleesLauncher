"""Discord Rich Presence: «Играет в ZeleesLauncher · Minecraft 1.21.1 · Fabric» в профиле Discord.

Работает через локальный канал Discord (\\\\.\\pipe\\discord-ipc-N на Windows, сокет в TMPDIR на Linux/macOS),
только стандартная библиотека. Если Discord не запущен, ничего не происходит.
"""
import ipaddress
import json
import os
import struct
import sys
import threading
import time
import uuid

from .lan import VPN_NETS

CLIENT_ID = "1565133801380778015"  # приложение ZeleesLauncher на discord.com/developers
SITE = "https://zelees.link"
LOGO = SITE + "/static/logo_mark@2.png"

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2


class NotRunning(OSError):
    pass


def encode(op, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<II", op, len(data)) + data


def decode_header(head):
    """8 байт заголовка -> (op, длина)."""
    return struct.unpack("<II", head)


def server_text(server):
    """Что показать про сервер: адрес публичного сервера, без адресов из локальной сети."""
    if not server:
        return "Одиночная игра"
    host = server[0]
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global or any(ip in n for n in VPN_NETS):  # Hamachi и Radmin VPN — тоже «свои»
            return "Игра по сети"
    except ValueError:
        if host == "localhost" or "." not in host:
            return "Игра по сети"
    return "Сервер %s" % host


def build_activity(mc, loader_name=None, server=None, started=None):
    details = "Minecraft %s" % mc + (" · %s" % loader_name if loader_name else "")
    return {
        "details": details[:128],
        "state": server_text(server)[:128],
        "timestamps": {"start": int(started or time.time())},
        "assets": {"large_image": LOGO, "large_text": "ZeleesLauncher"},
        "buttons": [{"label": "Скачать ZeleesLauncher", "url": SITE}],
    }


def _pipe_paths():
    if sys.platform.startswith("win"):
        return [r"\\?\pipe\discord-ipc-%d" % i for i in range(10)]
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return [os.path.join(base, "discord-ipc-%d" % i) for i in range(10)]


class _Conn:
    def __init__(self):
        self.f = None
        self.sock = None
        for p in _pipe_paths():
            try:
                if sys.platform.startswith("win"):
                    self.f = open(p, "r+b", buffering=0)
                else:
                    import socket
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.settimeout(5)
                    self.sock.connect(p)
                return
            except OSError:
                self.sock = None
                continue
        raise NotRunning("Discord не запущен")

    def write(self, data):
        if self.f:
            self.f.write(data)
        else:
            self.sock.sendall(data)

    def read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.f.read(n - len(buf)) if self.f else self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("Discord закрыл соединение")
            buf += chunk
        return buf

    def close(self):
        for c in (self.f, self.sock):
            try:
                if c:
                    c.close()
            except OSError:
                pass


class Presence:
    """Статус в Discord. Методы можно вызывать из любого потока — работа идёт в своём потоке."""

    def __init__(self, client_id=CLIENT_ID, log=None):
        self.client_id = client_id
        self.log = log or (lambda s: None)
        self.conn = None
        self.lock = threading.Lock()

    def _connect(self):
        conn = _Conn()
        conn.write(encode(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id}))
        self._read(conn)
        self.conn = conn

    @staticmethod
    def _read(conn):
        op, n = decode_header(conn.read(8))
        msg = json.loads(conn.read(n).decode("utf-8")) if n else {}
        if op == OP_CLOSE or msg.get("evt") == "ERROR":
            raise OSError("Discord: %s" % (msg.get("message") or (msg.get("data") or {}).get("message") or msg))
        return msg

    def _send(self, activity):
        with self.lock:
            for attempt in (1, 2):  # Discord мог перезапуститься — пробуем переподключиться один раз
                try:
                    if not self.conn:
                        self._connect()
                    self.conn.write(encode(OP_FRAME, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                                                      "args": {"pid": os.getpid(), "activity": activity}}))
                    self._read(self.conn)
                    return
                except (OSError, ValueError, struct.error) as e:
                    if self.conn:
                        self.conn.close()
                        self.conn = None
                    if isinstance(e, NotRunning):
                        return  # Discord не запущен — это нормально
                    if attempt == 2:
                        self.log("Discord: статус не обновлён (%s)" % e)

    def set(self, activity):
        threading.Thread(target=self._send, args=(activity,), daemon=True).start()

    def clear(self):
        if self.conn:
            self.set(None)
