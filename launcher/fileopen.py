"""Открытие файлов .mrpack двойным кликом.

* register() — добавляет лаунчер в «Открыть с помощью» для .mrpack (HKCU, без прав администратора) и делает
  его программой по умолчанию, только если у .mrpack её ещё нет.
* Если лаунчер уже открыт, второй экземпляр отдаёт ему путь к файлу через локальный сокет (Server/send) и
  сразу закрывается, чтобы не было двух окон, качающих в одну папку.
"""
import json
import os
import secrets
import socket
import sys
import threading

from .config import DATA_DIR

EXT = ".mrpack"
PROG_ID = "ZeleesLauncher.mrpack"
IPC_FILE = os.path.join(DATA_DIR, "ipc.json")


def file_arg(argv=None):
    """Путь к .mrpack из командной строки или None."""
    for a in (sys.argv[1:] if argv is None else argv):
        if a.lower().endswith(EXT) and os.path.isfile(a):
            return os.path.abspath(a)
    return None


# ----------------------------------------------------------------- реестр
def open_command(exe):
    return '"%s" "%%1"' % exe


def register():
    """Связывает .mrpack с собранным exe. Из исходников (python main.py) ничего не делает."""
    if not (sys.platform.startswith("win") and getattr(sys, "frozen", False)):
        return
    import winreg
    exe = os.path.abspath(sys.executable)
    base = "Software\\Classes\\"
    changed = False

    def ensure(path, value, name=""):
        nonlocal changed
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + path) as k:
            try:
                if winreg.QueryValueEx(k, name)[0] == value:
                    return
            except OSError:
                pass
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
            changed = True

    ensure(PROG_ID, "Модпак Modrinth")
    ensure(PROG_ID + "\\DefaultIcon", '"%s",0' % exe)
    ensure(PROG_ID + "\\shell\\open\\command", open_command(exe))
    ensure(EXT + "\\OpenWithProgids", "", PROG_ID)
    try:  # программа по умолчанию — только если её ещё нет (не отбираем .mrpack у других лаунчеров)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, EXT) as k:
            current = winreg.QueryValueEx(k, "")[0]
    except OSError:
        current = ""
    if not current:
        ensure(EXT, PROG_ID)
    if changed:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


# ----------------------------------------------------------------- один экземпляр
class Server:
    """Принимает пути к файлам от новых запусков лаунчера. on_open(path) вызывается в фоновом потоке."""

    def __init__(self, on_open, ipc_file=None):
        self.on_open = on_open
        self.ipc_file = ipc_file or IPC_FILE
        self.token = secrets.token_hex(16)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        os.makedirs(os.path.dirname(self.ipc_file), exist_ok=True)
        with open(self.ipc_file, "w", encoding="utf-8") as f:
            json.dump({"port": self.sock.getsockname()[1], "token": self.token, "pid": os.getpid()}, f)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _addr = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(3)
                    msg = json.loads(_recv_line(conn))
                    if msg.get("token") != self.token or not isinstance(msg.get("open"), str):
                        continue
                    conn.sendall(b"ok\n")
                except (OSError, ValueError, AttributeError):
                    continue
            self.on_open(msg["open"])

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            with open(self.ipc_file, "r", encoding="utf-8") as f:
                mine = json.load(f).get("token") == self.token
            if mine:
                os.remove(self.ipc_file)
        except (OSError, ValueError):
            pass


def _recv_line(conn, limit=65536):
    data = b""
    while b"\n" not in data and len(data) < limit:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.split(b"\n", 1)[0].decode("utf-8")


def send(path, ipc_file=None):
    """Отдаёт путь уже открытому лаунчеру. True — получилось (этот запуск можно закрыть)."""
    try:
        with open(ipc_file or IPC_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1.5) as s:
            s.settimeout(3)
            s.sendall((json.dumps({"token": info["token"], "open": path}) + "\n").encode("utf-8"))
            return _recv_line(s) == "ok"
    except (OSError, ValueError, KeyError, TypeError):
        return False
