"""Сервера в локальной сети: миры, открытые через «Открыть для сети», и выделенные сервера в своей подсети.

Minecraft раз в 1,5 секунды рассылает по UDP на 224.0.2.60:4445 пакет «[MOTD]Мир[/MOTD][AD]порт[/AD]».
Слушаем его на всех сетевых интерфейсах (в том числе Radmin VPN, Hamachi, ZeroTier). Выделенные
сервера себя не объявляют — их находит scan_subnets: TCP-порт 25565 во всей своей /24-подсети.
"""
import ipaddress
import re
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

GROUP = "224.0.2.60"
PORT = 4445
TTL = 6.0  # мир пропадает из списка, если от него столько секунд нет объявлений
VPN_NETS = [ipaddress.ip_network(n) for n in ("25.0.0.0/8", "26.0.0.0/8")]  # Hamachi, Radmin VPN
_AD_RE = re.compile(r"\[MOTD\](.*?)\[/MOTD\]\[AD\](.*?)\[/AD\]", re.S)


def parse_announce(data):
    """Пакет объявления -> (motd, адрес_или_None, порт) или None."""
    m = _AD_RE.search(data.decode("utf-8", "replace"))
    if not m:
        return None
    motd, ad = m.group(1).strip(), m.group(2).strip()
    host = None
    if ":" in ad:  # некоторые моды и прокси пишут host:port
        host, ad = ad.rsplit(":", 1)
    try:
        port = int(ad)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return motd or "LAN-мир", host or None, port


def local_ipv4():
    """IPv4-адреса этого компьютера (кроме 127.x), включая адаптеры VPN."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # адрес основного интерфейса (пакет никуда не уходит — UDP connect только выбирает маршрут)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


class LanListener:
    """Фоновый приём объявлений LAN-миров. on_change() вызывается из потока приёма при изменении списка."""

    def __init__(self, on_change=None):
        self.on_change = on_change
        self.lock = threading.Lock()
        self.found = {}  # (ip, port) -> {"motd", "address", "port", "seen"}
        self.error = None
        self._started = False
        self._joined = set()

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run, name="lan-listen", daemon=True).start()
        threading.Thread(target=self._expire_loop, name="lan-expire", daemon=True).start()

    def servers(self):
        with self.lock:
            return sorted(self.found.values(), key=lambda e: (e["motd"].lower(), e["address"], e["port"]))

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def _join_all(self, s):
        """Подписка на группу на каждом интерфейсе: иначе Windows слушает только основной."""
        for ip in local_ipv4() + ["0.0.0.0"]:
            if ip in self._joined:
                continue
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(ip)))
                self._joined.add(ip)
            except OSError:
                pass

    def _run(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # порт делим с запущенной игрой
            s.bind(("", PORT))
            s.settimeout(1.0)
        except OSError as e:
            self.error = "не удалось слушать порт %d: %s" % (PORT, e)
            self._changed()
            return
        self._join_all(s)
        if not self._joined:
            self.error = "сетевые интерфейсы не поддерживают multicast"
            self._changed()
        rejoin = time.time() + 10
        while True:
            if time.time() > rejoin:  # подключили VPN или сменили сеть — подписываемся на новые интерфейсы
                self._join_all(s)
                rejoin = time.time() + 10
            try:
                data, (src, _p) = s.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(1)
                continue
            ad = parse_announce(data)
            if not ad:
                continue
            motd, host, port = ad
            addr = host or src
            key = (addr, port)
            with self.lock:
                new = key not in self.found or self.found[key]["motd"] != motd
                self.found[key] = {"motd": motd, "address": addr, "port": port, "seen": time.time(), "kind": "lan"}
            if new:
                self._changed()

    def _expire_loop(self):
        while True:
            time.sleep(1.5)
            t = time.time()
            with self.lock:
                old = [k for k, e in self.found.items() if t - e["seen"] > TTL]
                for k in old:
                    del self.found[k]
            if old:
                self._changed()


def _port_open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_subnets(port=25565, timeout=0.6, cancel=None):
    """Ищет открытый порт во всех частных /24-подсетях этого компьютера. Возвращает список IP."""
    hosts = []
    for ip in local_ipv4():
        a = ipaddress.ip_address(ip)
        if not (a.is_private or any(a in n for n in VPN_NETS)):
            continue
        net = ipaddress.ip_network(ip + "/24", strict=False)
        hosts += [str(h) for h in net.hosts()]
    hosts = sorted(set(hosts), key=lambda h: tuple(int(x) for x in h.split(".")))
    found = []
    with ThreadPoolExecutor(max_workers=96) as pool:
        for h, ok in zip(hosts, pool.map(lambda h: not (cancel and cancel.is_set()) and _port_open(h, port, timeout),
                                          hosts)):
            if ok:
                found.append(h)
    return found
