"""Открытие .mrpack двойным кликом: аргументы и передача файла уже открытому лаунчеру."""
import json
import os
import shutil
import tempfile
import threading
import unittest

from launcher import fileopen


class FileOpenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ipc = os.path.join(self.dir, "ipc.json")

    def test_file_arg(self):
        pack = os.path.join(self.dir, "Pack.MRPACK")
        open(pack, "wb").close()
        self.assertEqual(fileopen.file_arg(["--flag", pack]), os.path.abspath(pack))
        self.assertIsNone(fileopen.file_arg([os.path.join(self.dir, "missing.mrpack")]))
        self.assertIsNone(fileopen.file_arg(["x.zip"]))
        self.assertIsNone(fileopen.file_arg([]))

    def test_open_command(self):
        self.assertEqual(fileopen.open_command(r"C:\Program Files\Z\ZeleesLauncher.exe"),
                         '"C:\\Program Files\\Z\\ZeleesLauncher.exe" "%1"')

    def test_send_to_running(self):
        got, done = [], threading.Event()
        srv = fileopen.Server(lambda p: (got.append(p), done.set()), ipc_file=self.ipc)
        self.assertTrue(fileopen.send(r"C:\пак.mrpack", ipc_file=self.ipc))
        self.assertTrue(done.wait(3))
        self.assertEqual(got, [r"C:\пак.mrpack"])
        srv.close()
        self.assertFalse(os.path.exists(self.ipc))
        self.assertFalse(fileopen.send("x.mrpack", ipc_file=self.ipc))  # лаунчер закрыт

    def test_wrong_token_ignored(self):
        got = []
        srv = fileopen.Server(got.append, ipc_file=self.ipc)
        self.addCleanup(srv.close)
        with open(self.ipc, encoding="utf-8") as f:
            info = json.load(f)
        info["token"] = "bad"
        fake = os.path.join(self.dir, "fake.json")
        with open(fake, "w", encoding="utf-8") as f:
            json.dump(info, f)
        self.assertFalse(fileopen.send("x.mrpack", ipc_file=fake))
        self.assertEqual(got, [])

    def test_stale_ipc_file(self):
        with open(self.ipc, "w", encoding="utf-8") as f:
            json.dump({"port": 1, "token": "t"}, f)
        self.assertFalse(fileopen.send("x.mrpack", ipc_file=self.ipc))
        with open(self.ipc, "w", encoding="utf-8") as f:
            f.write("garbage")
        self.assertFalse(fileopen.send("x.mrpack", ipc_file=self.ipc))

    def test_close_keeps_other_instance_file(self):
        a = fileopen.Server(lambda p: None, ipc_file=self.ipc)
        b = fileopen.Server(lambda p: None, ipc_file=self.ipc)  # второй запуск перезаписал файл
        a.close()
        self.assertTrue(os.path.exists(self.ipc))
        b.close()
        self.assertFalse(os.path.exists(self.ipc))


class FakeWinreg:
    """winreg в памяти: {(hive, путь в нижнем регистре): {имя: значение}}."""
    HKEY_CURRENT_USER, HKEY_CLASSES_ROOT, REG_SZ = "HKCU", "HKCR", 1

    def __init__(self):
        self.keys = {}
        self.writes = 0

    class _Key:
        def __init__(self, store):
            self.store = store

        def __enter__(self):
            return self.store

        def __exit__(self, *a):
            return False

    def _path(self, hive, path):
        if hive == "HKCR":  # HKCR = HKCU\Software\Classes поверх HKLM (здесь только HKCU)
            hive, path = "HKCU", "Software\\Classes\\" + path
        return hive, path.lower()

    def CreateKey(self, hive, path):
        return self._Key(self.keys.setdefault(self._path(hive, path), {}))

    def OpenKey(self, hive, path):
        key = self._path(hive, path)
        if key not in self.keys:
            raise OSError("no key")
        return self._Key(self.keys[key])

    def QueryValueEx(self, store, name):
        if name not in store:
            raise OSError("no value")
        return store[name], self.REG_SZ

    def SetValueEx(self, store, name, _reserved, _type, value):
        store[name] = value
        self.writes += 1


class RegisterTest(unittest.TestCase):
    EXE = r"C:\Users\u\AppData\Local\Programs\ZeleesLauncher\ZeleesLauncher.exe"

    def run_register(self, reg):
        from unittest import mock
        notify = mock.MagicMock()
        fake_ctypes = mock.MagicMock()
        fake_ctypes.windll.shell32.SHChangeNotify = notify
        with mock.patch.dict("sys.modules", {"winreg": reg, "ctypes": fake_ctypes}), \
                mock.patch.object(fileopen.sys, "platform", "win32"), \
                mock.patch.object(fileopen.sys, "frozen", True, create=True), \
                mock.patch.object(fileopen.sys, "executable", self.EXE):
            fileopen.register()
        return notify.called

    def value(self, reg, path, name=""):
        return reg.keys[("HKCU", ("Software\\Classes\\" + path).lower())][name]

    def test_registers_and_becomes_default(self):
        reg = FakeWinreg()
        self.assertTrue(self.run_register(reg))
        self.assertEqual(self.value(reg, "ZeleesLauncher.mrpack\\shell\\open\\command"), '"%s" "%%1"' % self.EXE)
        self.assertEqual(self.value(reg, "ZeleesLauncher.mrpack\\DefaultIcon"), '"%s",0' % self.EXE)
        self.assertEqual(self.value(reg, ".mrpack\\OpenWithProgids", "ZeleesLauncher.mrpack"), "")
        self.assertEqual(self.value(reg, ".mrpack"), "ZeleesLauncher.mrpack")
        writes = reg.writes
        self.assertFalse(self.run_register(reg))  # повторный запуск ничего не меняет
        self.assertEqual(reg.writes, writes)

    def test_does_not_take_over_other_app(self):
        reg = FakeWinreg()
        reg.keys[("HKCU", "software\\classes\\.mrpack")] = {"": "ModrinthApp.mrpack"}
        self.run_register(reg)
        self.assertEqual(self.value(reg, ".mrpack"), "ModrinthApp.mrpack")
        self.assertEqual(self.value(reg, ".mrpack\\OpenWithProgids", "ZeleesLauncher.mrpack"), "")

    def test_not_frozen_does_nothing(self):
        reg = FakeWinreg()
        from unittest import mock
        with mock.patch.dict("sys.modules", {"winreg": reg}), mock.patch.object(fileopen.sys, "frozen", False,
                                                                                 create=True):
            fileopen.register()
        self.assertEqual(reg.keys, {})


if __name__ == "__main__":
    unittest.main()
