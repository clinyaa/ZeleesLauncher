"""HTTP-запросы и параллельная загрузка файлов с проверкой SHA1."""
import hashlib
import http.client
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import APP_NAME, APP_VERSION

USER_AGENT = "%s/%s" % (APP_NAME, APP_VERSION)

# Один SSL-контекст на весь процесс. Без него urllib на КАЖДЫЙ запрос заново грузит
# все корневые сертификаты Windows — на 5000 ассетов это вешает интерфейс и роняет лаунчер.
_SSL_CTX = None
_SSL_LOCK = threading.Lock()


# Хосты со своим корневым сертификатом (сайт ZeleesLauncher): host -> контекст, доверяющий только ему.
_PINNED = {}


def pin_host(host, ca_pem):
    _PINNED[host] = ssl.create_default_context(cadata=ca_pem)


def ssl_context(host=None):
    global _SSL_CTX
    if host in _PINNED:
        return _PINNED[host]
    if _SSL_CTX is None:
        with _SSL_LOCK:
            if _SSL_CTX is None:
                _SSL_CTX = _default_context()
    return _SSL_CTX


def _default_context():
    """Хранилище Windows (корни антивирусов, корпоративных прокси) + набор Mozilla: Windows докачивает
    корневые сертификаты по требованию, OpenSSL так не умеет, и на «свежем» ПК без набора падают
    загрузки с сайтов на новых корнях (cdn.modrinth.com — Certainly Root R1)."""
    ctx = ssl.create_default_context()
    try:
        from .ca_bundle import CA_PEM
        ctx.load_verify_locations(cadata=CA_PEM)
    except Exception:  # noqa: BLE001 - без набора остаётся хранилище Windows, как раньше
        pass
    return ctx


def _urlopen(req, timeout):
    if req.full_url.lower().startswith("https:"):
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context(req.host))
    return urllib.request.urlopen(req, timeout=timeout)


# ---- keep-alive соединения для массовой загрузки (своё на каждый поток) ----
_local = threading.local()


def _conn(scheme, host, timeout):
    pool = getattr(_local, "pool", None)
    if pool is None:
        pool = _local.pool = {}
    key = (scheme, host)
    c = pool.get(key)
    if c is None:
        if scheme == "https":
            c = http.client.HTTPSConnection(host, timeout=timeout, context=ssl_context(host))
        else:
            c = http.client.HTTPConnection(host, timeout=timeout)
        pool[key] = c
    return c


def _drop_conn(scheme, host):
    pool = getattr(_local, "pool", None) or {}
    c = pool.pop((scheme, host), None)
    if c is not None:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def close_thread_connections():
    pool = getattr(_local, "pool", None) or {}
    for c in list(pool.values()):
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    pool.clear()


_PROXIES = None


def _has_proxy(scheme):
    global _PROXIES
    if _PROXIES is None:
        try:
            _PROXIES = urllib.request.getproxies() or {}
        except Exception:  # noqa: BLE001
            _PROXIES = {}
    return scheme in _PROXIES


def _fetch_to(url, f, h, cancel, timeout, redirects=5):
    """GET url в открытый файл f через keep-alive соединение потока. Следует редиректам."""
    if _has_proxy(urllib.parse.urlsplit(url).scheme.lower()):
        # в системе настроен прокси — идём через urllib (он его учитывает), но с общим SSL-контекстом
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with _urlopen(req, timeout) as r:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        return
                    h.update(chunk)
                    f.write(chunk)
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, b"", url)
    for _ in range(redirects + 1):
        u = urllib.parse.urlsplit(url)
        scheme = (u.scheme or "https").lower()
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        c = _conn(scheme, u.netloc, timeout)
        try:
            c.request("GET", path, headers={"User-Agent": USER_AGENT, "Connection": "keep-alive"})
            r = c.getresponse()
        except Exception:
            _drop_conn(scheme, u.netloc)
            raise
        if r.status in (301, 302, 303, 307, 308):
            loc = r.getheader("Location")
            r.read()
            if not loc:
                raise HttpError(r.status, b"", url)
            url = urllib.parse.urljoin(url, loc)
            continue
        if r.status != 200:
            body = r.read()
            if r.will_close:
                _drop_conn(scheme, u.netloc)
            raise HttpError(r.status, body, url)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
        except BaseException:
            _drop_conn(scheme, u.netloc)  # ответ не дочитан — соединение больше не годится
            raise
        if r.will_close:
            _drop_conn(scheme, u.netloc)
        return
    raise IOError("Слишком много перенаправлений: %s" % url)


class HttpError(Exception):
    def __init__(self, status, body, url):
        self.status = status
        self.body = body or b""
        self.url = url
        super().__init__("HTTP %s: %s" % (status, url))

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8", "replace"))
        except ValueError:
            return {}

    def text(self):
        return self.body.decode("utf-8", "replace")


class Cancelled(Exception):
    pass


def request(url, data=None, headers=None, method=None, timeout=30):
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with _urlopen(req, timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        raise HttpError(e.code, body, url)


def get_json(url, headers=None, timeout=30):
    _, body = request(url, headers=headers, timeout=timeout)
    return json.loads(body.decode("utf-8"))


def post_json(url, obj, headers=None, timeout=30):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        h.update(headers)
    _, body = request(url, data=json.dumps(obj).encode("utf-8"), headers=h, method="POST", timeout=timeout)
    return json.loads(body.decode("utf-8")) if body else {}


def post_form(url, fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    h = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    _, body = request(url, data=data, headers=h, method="POST", timeout=timeout)
    return json.loads(body.decode("utf-8")) if body else {}


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_valid(path, sha1=None, size=None, check_hash=True):
    if not os.path.isfile(path):
        return False
    if size is not None and os.path.getsize(path) != size:
        return False
    if sha1 and check_hash:
        try:
            return sha1_file(path).lower() == sha1.lower()
        except OSError:
            return False
    return True


def download(url, dest, sha1=None, size=None, retries=4, cancel=None, timeout=60):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = "%s.%d.part" % (dest, threading.get_ident())
    last_err = None
    for attempt in range(retries):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            h = hashlib.sha1()
            with open(tmp, "wb") as f:
                _fetch_to(url, f, h, cancel, timeout)
            if sha1 and h.hexdigest().lower() != sha1.lower():
                raise IOError("SHA1 не совпадает для %s" % url)
            if size is not None and os.path.getsize(tmp) != size:
                raise IOError("Размер не совпадает для %s" % url)
            _replace(tmp, dest)
            return dest
        except Cancelled:
            _silent_remove(tmp)
            raise
        except Exception as e:  # noqa: BLE001 - сеть может падать как угодно
            last_err = e
            _silent_remove(tmp)
            if isinstance(e, HttpError) and e.status in (403, 404, 410):
                break
            # пауза с проверкой отмены, чтобы кнопка «Отмена» срабатывала сразу
            if cancel is not None:
                if cancel.wait(1 + attempt * 2):
                    raise Cancelled()
            else:
                time.sleep(1 + attempt * 2)
    raise IOError("Не удалось скачать %s: %s" % (url, last_err))


def _replace(tmp, dest):
    """os.replace с повтором: антивирус на Windows может ненадолго держать файл."""
    for i in range(5):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if i == 4:
                raise
            time.sleep(0.2 * (i + 1))


def _silent_remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


class Task:
    __slots__ = ("url", "path", "sha1", "size", "check_hash")

    def __init__(self, url, path, sha1=None, size=None, check_hash=True):
        self.url, self.path, self.sha1, self.size, self.check_hash = url, path, sha1, size, check_hash


def download_many(tasks, threads=16, progress=None, cancel=None, label="Загрузка"):
    """Качает список Task. progress(done, total, text). Пропускает уже валидные файлы."""
    # убираем дубликаты путей
    uniq = {}
    for t in tasks:
        if t.url and t.path not in uniq:
            uniq[t.path] = t
    tasks = list(uniq.values())
    total = len(tasks)
    if progress:
        progress(0, total, "%s: проверка файлов..." % label)

    todo = []
    for i, t in enumerate(tasks):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if not is_valid(t.path, t.sha1, t.size, t.check_hash):
            todo.append(t)
        if progress and i % 200 == 0:
            progress(0, total, "%s: проверка файлов (%d/%d)..." % (label, i, total))

    already = total - len(todo)
    if not todo:
        if progress:
            progress(total, total, "%s: всё на месте" % label)
        return
    if progress:
        progress(already, total, "%s: нужно скачать %d из %d..." % (label, len(todo), total))
    done = [already]
    lock = threading.Lock()
    errors = []
    # внутренний флаг остановки: отмена пользователем ИЛИ слишком много ошибок подряд
    stop = threading.Event()
    max_errors = max(20, len(todo) // 20)

    if cancel is not None:
        def _watch():
            while not stop.is_set():
                if cancel.wait(0.2):
                    stop.set()
                    return
        threading.Thread(target=_watch, daemon=True).start()

    def work(t):
        if stop.is_set():
            raise Cancelled()
        download(t.url, t.path, t.sha1, t.size, cancel=stop)
        with lock:
            done[0] += 1
            if progress:
                progress(done[0], total, "%s: %d/%d" % (label, done[0], total))

    workers = max(1, min(int(threads or 8), 32))
    ex = ThreadPoolExecutor(max_workers=workers)
    cancelled = False
    try:
        futs = [ex.submit(work, t) for t in todo]
        for f in as_completed(futs):
            try:
                f.result()
            except Cancelled:
                if cancel is not None and cancel.is_set():
                    cancelled = True
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                if len(errors) >= max_errors:
                    stop.set()  # сеть явно лежит — не ждём ретраев по тысячам файлов
    finally:
        stop.set()
        # не ждём оставшиеся тысячи задач — отменяем их
        ex.shutdown(wait=True, cancel_futures=True)
    if cancelled or (cancel is not None and cancel.is_set()):
        raise Cancelled()
    if errors:
        raise IOError("Ошибок загрузки: %d. Первая: %s" % (len(errors), errors[0]))
