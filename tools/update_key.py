"""Ключ подписи обновлений ZeleesLauncher (Ed25519).

    py tools\\update_key.py genkey   — создать ключ (один раз) и вшить открытый ключ в launcher/updater.py
    py tools\\update_key.py show     — где лежит ключ и какой открытый ключ ему соответствует

Секретный ключ хранится ВНЕ папки проекта: %USERPROFILE%\\.zelees\\update_signing.key
(или путь из переменной ZELEES_SIGNING_KEY). Его нельзя класть на сервер и в репозиторий.
Сделайте резервную копию (флешка / менеджер паролей): без ключа выпустить обновление,
которое примут уже установленные лаунчеры, невозможно — пользователям придётся ставить
лаунчер заново вручную.
"""
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from launcher import ed25519  # noqa: E402

UPDATER_PY = os.path.join(ROOT, "launcher", "updater.py")
KEY_PATH = os.environ.get("ZELEES_SIGNING_KEY") or os.path.join(os.path.expanduser("~"), ".zelees",
                                                                   "update_signing.key")


def load_seed():
    try:
        with open(KEY_PATH, "r", encoding="ascii") as f:
            seed = bytes.fromhex(f.read().strip())
    except FileNotFoundError:
        sys.exit("Нет ключа подписи %s.\nСоздайте его: py tools\\update_key.py genkey (или верните из резервной копии)"
                 % KEY_PATH)
    if len(seed) != 32:
        sys.exit("Повреждён ключ подписи %s" % KEY_PATH)
    return seed


def embedded_pubkey():
    with open(UPDATER_PY, "r", encoding="utf-8") as f:
        m = re.search(r'^UPDATE_PUBKEY = "([0-9a-f]*)"', f.read(), re.M)
    return m.group(1) if m else ""


def set_pubkey(pub_hex):
    with open(UPDATER_PY, "r", encoding="utf-8") as f:
        s = f.read()
    s = re.sub(r'^UPDATE_PUBKEY = "[0-9a-f]*"', 'UPDATE_PUBKEY = "%s"' % pub_hex, s, count=1, flags=re.M)
    with open(UPDATER_PY, "w", encoding="utf-8") as f:
        f.write(s)


def sign(data):
    """Подпись data (hex). Проверяет, что ключ совпадает с вшитым в лаунчер."""
    seed = load_seed()
    pub = ed25519.public_key(seed).hex()
    if pub != embedded_pubkey():
        sys.exit("Ключ %s не совпадает с UPDATE_PUBKEY в launcher/updater.py — "
                 "такое обновление лаунчеры отвергнут." % KEY_PATH)
    sig = ed25519.sign(seed, data)
    assert ed25519.verify(bytes.fromhex(pub), data, sig)
    return sig.hex()


def genkey():
    if os.path.exists(KEY_PATH):
        sys.exit("Ключ уже есть: %s — новый не создаю (иначе старые лаунчеры перестанут принимать обновления)."
                 % KEY_PATH)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    seed = secrets.token_bytes(32)
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(seed.hex() + "\n")
    pub = ed25519.public_key(seed).hex()
    set_pubkey(pub)
    print("Ключ создан: %s" % KEY_PATH)
    print("Открытый ключ вшит в launcher/updater.py: %s" % pub)
    print("СДЕЛАЙТЕ РЕЗЕРВНУЮ КОПИЮ этого файла в надёжное место (не на сервер).")


def show():
    print("Файл ключа:      %s" % KEY_PATH)
    print("Вшит в лаунчер:  %s" % (embedded_pubkey() or "—"))
    if os.path.exists(KEY_PATH):
        print("Ключ из файла:   %s" % ed25519.public_key(load_seed()).hex())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    {"genkey": genkey, "show": show}.get(cmd, show)()
