"""Импорт модпаков Modrinth (.mrpack)."""
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from launcher import mrpack

SHA = "a" * 40


def entry(path, url="https://cdn.modrinth.com/data/AANobbMI/versions/x/sodium.jar", **kw):
    e = {"path": path, "hashes": {"sha1": SHA, "sha512": "b" * 128}, "downloads": [url], "fileSize": 10}
    e.update(kw)
    return e


INDEX = {
    "formatVersion": 1, "game": "minecraft", "versionId": "1.2.0", "name": "Cool Pack", "summary": "s",
    "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.16.9"},
    "files": [
        entry("mods/sodium.jar"),
        entry("mods/server-only.jar", env={"client": "unsupported", "server": "required"}),
        entry("resourcepacks/pack.zip", url="https://evil.example/pack.zip"),
        entry("shaderpacks/s.zip", url="http://github.com/x/s.zip"),  # не https
        entry("mods/gh.jar", url="https://github.com/owner/repo/releases/download/v1/gh.jar",
              env={"client": "optional", "server": "optional"}),
    ],
}


class MrpackTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def pack(self, index=INDEX, extra=None):
        path = os.path.join(self.dir, "p%d.mrpack" % len(os.listdir(self.dir)))
        with zipfile.ZipFile(path, "w") as z:
            if index is not None:
                z.writestr("modrinth.index.json", index if isinstance(index, str) else json.dumps(index))
            for name, data in (extra or {}).items():
                z.writestr(name, data)
        return path

    def test_read(self):
        p = mrpack.read(self.pack(extra={"overrides/config/a.toml": "x", "overrides/": "",
                                         "client-overrides/options.txt": "y"}))
        self.assertEqual((p["name"], p["version"], p["mc"], p["loader"], p["loader_version"]),
                         ("Cool Pack", "1.2.0", "1.20.1", "fabric", "0.16.9"))
        self.assertEqual([f["path"] for f in p["files"]], ["mods/sodium.jar", "mods/gh.jar"])
        self.assertEqual(p["skipped"], ["resourcepacks/pack.zip", "shaderpacks/s.zip"])
        self.assertEqual(p["overrides"], 2)

    def test_loaders(self):
        for dep, loader in mrpack.LOADER_DEPS.items():
            idx = dict(INDEX, dependencies={"minecraft": "1.20.1", dep: "1"})
            with self.subTest(dep=dep):
                self.assertEqual(mrpack.read(self.pack(idx))["loader"], loader)

    def test_bad_packs(self):
        bad = [
            None,  # нет индекса
            "{not json",
            dict(INDEX, formatVersion=2),
            dict(INDEX, game="terraria"),
            dict(INDEX, dependencies={"fabric-loader": "0.16.9"}),
            dict(INDEX, dependencies={"minecraft": "1.20.1"}),
            dict(INDEX, dependencies={"minecraft": "1.20.1", "forge": "47", "fabric-loader": "0.16"}),
        ]
        for idx in bad:
            with self.subTest(idx=str(idx)[:60]), self.assertRaises(mrpack.MrpackError):
                mrpack.read(self.pack(idx))
        not_zip = os.path.join(self.dir, "x.mrpack")
        with open(not_zip, "wb") as f:
            f.write(b"nope")
        with self.assertRaises(mrpack.MrpackError):
            mrpack.read(not_zip)

    def test_unsafe_file_path_rejected(self):
        for path in ("../evil.jar", "mods/../../evil.jar", "/etc/x", "C:/Windows/x.dll", "mods\\..\\..\\x", "", "mods//a"):
            idx = dict(INDEX, files=[entry(path)])
            with self.subTest(path=path), self.assertRaises(mrpack.MrpackError):
                mrpack.read(self.pack(idx))

    def test_safe_path(self):
        self.assertEqual(mrpack.safe_path("mods/a.jar"), "mods/a.jar")
        self.assertEqual(mrpack.safe_path("config\\a.toml"), "config/a.toml")
        self.assertIsNone(mrpack.safe_path("./a"))

    def test_extract_overrides(self):
        path = self.pack(extra={
            "overrides/config/a.toml": "base", "overrides/options.txt": "base",
            "client-overrides/options.txt": "client", "overrides/../escape.txt": "x",
            "overrides/mods/../../escape2.txt": "x", "other/ignored.txt": "x",
        })
        game = os.path.join(self.dir, "game")
        n = mrpack.extract_overrides(path, game)
        self.assertEqual(n, 3)
        with open(os.path.join(game, "options.txt")) as f:
            self.assertEqual(f.read(), "client")  # client-overrides поверх overrides
        self.assertTrue(os.path.isfile(os.path.join(game, "config", "a.toml")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escape.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escape2.txt")))
        self.assertFalse(os.path.exists(os.path.join(game, "other")))

    def test_install_files_tasks(self):
        p = mrpack.read(self.pack())
        game = os.path.join(self.dir, "game")
        with mock.patch.object(mrpack.net, "download_many") as dm:
            mrpack.install_files(p, game, threads=4)
        tasks = dm.call_args[0][0]
        self.assertEqual([t.path for t in tasks], [os.path.join(game, "mods", "sodium.jar"),
                                                   os.path.join(game, "mods", "gh.jar")])
        self.assertTrue(all(t.sha1 == SHA for t in tasks))


if __name__ == "__main__":
    unittest.main()
