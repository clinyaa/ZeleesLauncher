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


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)
        for rel, data in (("mods/a.jar", b"A"), ("mods/b.jar", b"B"), ("mods/off.jar.disabled", b"x"),
                          ("mods/readme.txt", b"x"), ("resourcepacks/rp.zip", b"R"), ("shaderpacks/s.zip", b"S"),
                          ("config/a.toml", b"c"), ("config/sub/b.json", b"c"), ("saves/w/level.dat", b"w")):
            p = os.path.join(self.game, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)

    def test_scan(self):
        files, disabled = mrpack.scan(self.game)
        self.assertEqual([f["path"] for f in files],
                         ["mods/a.jar", "mods/b.jar", "resourcepacks/rp.zip", "shaderpacks/s.zip"])
        self.assertEqual(disabled, ["off.jar.disabled"])
        self.assertEqual(files[0]["sha1"], "6dcd4ce23d88e2ee9568ba546c007c63d9131c1b")  # sha1(b"A")
        self.assertEqual(files[0]["size"], 1)
        self.assertEqual([r for r, _p in mrpack.config_files(self.game)], ["config/a.toml", "config/sub/b.json"])

    def test_roundtrip(self):
        files, _ = mrpack.scan(self.game)
        a = files[0]
        urls = {a["sha1"]: "https://cdn.modrinth.com/data/x/versions/y/a.jar"}
        idx = mrpack.build_index("My Pack", "2.0", "desc", "1.21.1", "neoforge", "21.1.77", files, urls)
        self.assertEqual(idx["dependencies"], {"minecraft": "1.21.1", "neoforge": "21.1.77"})
        embed = [(f["path"], f["abs"]) for f in files if f["sha1"] not in urls] + mrpack.config_files(self.game)
        out = os.path.join(self.game, "out.mrpack")
        mrpack.write(out, idx, embed)
        self.assertFalse(os.path.exists(out + ".tmp"))
        p = mrpack.read(out)
        self.assertEqual((p["name"], p["version"], p["summary"], p["loader"]), ("My Pack", "2.0", "desc", "neoforge"))
        self.assertEqual([(f["path"], f["sha1"]) for f in p["files"]], [("mods/a.jar", a["sha1"])])
        self.assertEqual(p["overrides"], 5)
        target = os.path.join(self.game, "target")
        mrpack.extract_overrides(out, target)
        with open(os.path.join(target, "mods", "b.jar"), "rb") as f:
            self.assertEqual(f.read(), b"B")
        self.assertTrue(os.path.isfile(os.path.join(target, "config", "sub", "b.json")))

    def test_identify_uses_matching_file(self):
        files, _ = mrpack.scan(self.game)
        h = files[0]["sha1"]
        resp = {h: {"files": [{"hashes": {"sha1": "other"}, "url": "https://cdn.modrinth.com/other.jar"},
                              {"hashes": {"sha1": h}, "url": "https://cdn.modrinth.com/a.jar"}]},
                files[1]["sha1"]: {"files": [{"hashes": {"sha1": files[1]["sha1"]}, "url": "https://evil.example/b.jar"}]}}
        with mock.patch.object(mrpack.net, "post_json", return_value=resp):
            self.assertEqual(mrpack.identify(files), {h: "https://cdn.modrinth.com/a.jar"})


if __name__ == "__main__":
    unittest.main()
