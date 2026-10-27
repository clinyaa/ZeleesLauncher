"""Опрос сервера Minecraft (Server List Ping): MOTD, онлайн, версия, иконка и пинг. Без сторонних библиотек."""
import base64
import ipaddress
import json
import random
import re
import socket
import struct
import time

DEFAULT_PORT = 25565
DNS_SERVERS = ("1.1.1.1", "8.8.8.8", "77.88.8.8")
_FMT_RE = re.compile("§[0-9a-fk-orx]", re.I)


class PingError(Exception):
    pass


def parse_address(text):
    """'play.example.com:25566' -> ('play.example.com', 25566); порт 0 = по умолчанию / SRV."""
    text = (text or "").strip().lower().rstrip(".")
    port = 0
    if text.count(":") == 1:
        text, p = text.split(":")
        try:
            port = int(p)
        except ValueError:
            raise ValueError("Порт должен быть числом")
        if not 1 <= port <= 65535:
            raise ValueError("Порт: от 1 до 65535")
    if port == DEFAULT_PORT:
        port = 0
    return text, port


def display_address(host, port):
    return host + (":%d" % port if port else "")


# ---------------------------------------------------------------- SRV-запись _minecraft._tcp
def _dns_name(data, off):
    labels, jumped, end = [], False, off
    for _ in range(64):
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = off + 2
            off = ((n & 0x3F) << 8) | data[off + 1]
            jumped = True
            continue
        labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    return ".".join(labels), (end if jumped else off)


def resolve_srv(host, timeout=1.5):
    """(цель, порт) из SRV-записи или None. Запрос — напрямую к публичным DNS по UDP."""
    try:
        qname = b"".join(bytes([len(p)]) + p for p in ("_minecraft._tcp." + host).encode("idna").split(b"."))
    except UnicodeError:
        return None
    qid = random.randrange(65536)
    query = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0) + qname + b"\0" + struct.pack(">HH", 33, 1)
    for server in DNS_SERVERS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            s.sendto(query, (server, 53))
            data, _ = s.recvfrom(4096)
        except OSError:
            continue
        finally:
            s.close()
        try:
            rid, flags, qd, an = struct.unpack(">HHHH", data[:8])
            if rid != qid:
                continue
            if flags & 0x000F or not an:
                return None  # нет записи (NXDOMAIN / пусто)
            off = 12
            for _ in range(qd):
                _, off = _dns_name(data, off)
                off += 4
            for _ in range(an):
                _, off = _dns_name(data, off)
                rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
                off += 10
                if rtype == 33:
                    _prio, _weight, port = struct.unpack(">HHH", data[off:off + 6])
                    target, _ = _dns_name(data, off + 6)
                    return (target.rstrip("."), port) if target else None
                off += rdlen
        except (struct.error, IndexError):
            continue
        return None
    return None


# ---------------------------------------------------------------- протокол
def _varint(n):
    n &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _string(s):
    b = s.encode("utf-8")
    return _varint(len(b)) + b


def _packet(pid, payload=b""):
    body = _varint(pid) + payload
    return _varint(len(body)) + body


class _Reader:
    def __init__(self, sock):
        self.sock = sock

    def exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise PingError("сервер закрыл соединение")
            buf += chunk
        return bytes(buf)

    def varint(self):
        n = 0
        for i in range(5):
            b = self.exact(1)[0]
            n |= (b & 0x7F) << (7 * i)
            if not b & 0x80:
                return n
        raise PingError("неверный ответ сервера")


def motd_text(desc):
    """Описание сервера (строка или JSON-компонент) -> обычный текст без кодов цвета."""
    def walk(d):
        if isinstance(d, str):
            return d
        if isinstance(d, list):
            return "".join(walk(x) for x in d)
        if isinstance(d, dict):
            return walk(d.get("text", "")) + "".join(walk(x) for x in d.get("extra", []))
        return ""
    text = _FMT_RE.sub("", walk(desc))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def is_public(ip):
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def ping(host, port=0, timeout=4.0, allow_private=False):
    """Возвращает dict: ping (мс), online, max, version, protocol, motd, favicon (PNG-байты или None)."""
    target, tport = host, port or DEFAULT_PORT
    if not port:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            srv = resolve_srv(host)
            if srv:
                target, tport = srv
    try:
        infos = socket.getaddrinfo(target, tport, type=socket.SOCK_STREAM)
    except OSError:
        raise PingError("адрес не найден")
    family, _, _, _, sockaddr = infos[0]
    if not allow_private and not is_public(sockaddr[0]):
        raise PingError("внутренний адрес")
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        try:
            s.connect(sockaddr)
        except OSError:
            raise PingError("нет ответа")
        connect_ms = (time.perf_counter() - t0) * 1000
        hs = _varint(-1) + _string(host) + struct.pack(">H", tport) + _varint(1)
        s.sendall(_packet(0, hs) + _packet(0))
        r = _Reader(s)
        length = r.varint()
        if length > 2 * 1024 * 1024:
            raise PingError("слишком большой ответ")
        data = r.exact(length)
        # пропускаем id пакета и длину строки
        i = 0
        for _ in range(2):
            while data[i] & 0x80:
                i += 1
            i += 1
        st = json.loads(data[i:].decode("utf-8", "replace"))
        ms = connect_ms
        try:
            token = random.getrandbits(63)
            t1 = time.perf_counter()
            s.sendall(_packet(1, struct.pack(">q", token)))
            r.varint()
            r.exact(9)
            ms = (time.perf_counter() - t1) * 1000
        except (OSError, PingError):
            pass
    except socket.timeout:
        raise PingError("нет ответа")
    except (OSError, ValueError, IndexError) as e:
        raise PingError("ошибка ответа: %s" % e)
    finally:
        s.close()
    players = st.get("players") or {}
    ver = st.get("version") or {}
    fav = None
    icon = st.get("favicon") or ""
    if icon.startswith("data:image/png;base64,"):
        try:
            fav = base64.b64decode(icon.split(",", 1)[1])
        except ValueError:
            fav = None
    return {
        "ping": max(1, int(round(ms))),
        "online": int(players.get("online") or 0),
        "max": int(players.get("max") or 0),
        "version": _FMT_RE.sub("", str(ver.get("name") or "")),
        "protocol": ver.get("protocol"),
        "motd": motd_text(st.get("description", "")),
        "favicon": fav,
    }
