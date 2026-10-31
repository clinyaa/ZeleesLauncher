"""Собирает launcher/ca_bundle.py — корневые сертификаты Mozilla из пакета certifi.

    py tools\\make_ca_bundle.py

Зачем: Windows держит не все корневые сертификаты сразу и докачивает их, когда браузер впервые
встречает сайт. OpenSSL (Python) так не умеет, поэтому на «свежем» ПК загрузка, например, с
cdn.modrinth.com (корень Certainly Root R1) падает с CERTIFICATE_VERIFY_FAILED. Лаунчер доверяет
и хранилищу Windows (там корни антивирусов и корпоративных прокси), и этому набору.

Набор лежит в .py, потому что пакет обновления (tools/publish_update.py) несёт только .py и assets.zip.
Перегенерируйте время от времени: pip install -U certifi, затем этот скрипт.
"""
import os
import re

import certifi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "launcher", "ca_bundle.py")


def main():
    with open(certifi.where(), encoding="ascii") as f:
        pems = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", f.read(), re.S)
    if len(pems) < 100:
        raise SystemExit("Подозрительно мало сертификатов в certifi: %d" % len(pems))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""Корневые сертификаты Mozilla (certifi %s, %d шт.). Сгенерировано tools/make_ca_bundle.py —\n'
                'не правьте вручную."""\n\nCA_PEM = """\\\n' % (certifi.__version__, len(pems)))
        f.write("\n".join(pems))
        f.write('\n"""\n')
    print("%s: %d сертификатов" % (OUT, len(pems)))


if __name__ == "__main__":
    main()
