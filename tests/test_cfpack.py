"""Импорт модпаков CurseForge (.zip с manifest.json)."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import zipfile

from launcher import cfpack, mrpack

MANIFEST = {
    "manifestType": "minecraftModpack", "manifestVersion": 1, "name": "Otherworld", "version": "8.2",
    "author": "someone", "overrides": "overrides",
    "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.3.0", "primary": True}]},
    "files": [
        {"projectID": 1, "fileID": 11, "required": True},
        {"projectID": 2, "fileID": 22, "required": True},
        {"projectID": 3, "fileID": 33, "required": True},
        {"projectID": 4, "fileID": 44, "required": False},
        {"projectID": 5, "fileID": 55},
        {"projectID": 6, "fileID": 66},
    ],
}


def cf_file(fid, pid, name, url="https://edge.forgecdn.net/files/x/%s"):
    return {"source": "curseforge", "project_id": str(pid), "version_id": str(fid), "filename": name,
            "url": (url % name) if url else None, "sha1": "a" * 40, "size": 1}


class CfPackTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def pack(self, manifest=MANIFEST, extra=None):
        path = os.path.join(self.dir, "p%d.zip" % len(os.listdir(self.dir)))
        with zipfile.ZipFile(path, "w") as z:
            if manifest is not None:
                z.writestr("manifest.json", manifest if isinstance(manifest, str) else json.dumps(manifest))
            for name, data in (extra or {}).items():
                z.writestr(name, data)
        return path

    def test_read(self):
        path = self.pack(extra={"overrides/config/a.toml": "x", "overrides/": "", "profileImage.png": "i"})
        self.assertTrue(cfpack.is_cfpack(path))
        p = cfpack.read(path)
        self.assertEqual((p["name"], p["version"], p["mc"], p["loader"], p["loader_version"]),
                         ("Otherworld", "8.2", "1.20.1", "forge", "47.3.0"))
        self.assertEqual([f["file"] for f in p["files"]], ["11", "22", "33", "55", "66"])  # 44 необязательный
        self.assertEqual(p["overrides"], 1)

    def test_loaders(self):
        for lid, loader in (("neoforge-47.1.106", "neoforge"), ("fabric-0.16.9", "fabric"), ("quilt-0.26", "quilt")):
            mf = dict(MANIFEST, minecraft={"version": "1.20.1", "modLoaders": [{"id": lid, "primary": True}]})
            with self.subTest(lid=lid):
                self.assertEqual(cfpack.read(self.pack(mf))["loader"], loader)

    def test_bad_packs(self):
        bad = [None, "{not json", dict(MANIFEST, manifestType="other"),
               dict(MANIFEST, minecraft={"modLoaders": [{"id": "forge-47"}]}),
               dict(MANIFEST, minecraft={"version": "1.20.1", "modLoaders": []}),
               dict(MANIFEST, minecraft={"version": "1.20.1", "modLoaders": [{"id": "rift-1"}]}),
               dict(MANIFEST, files=[{"projectID": "x"}])]
        for mf in bad:
            with self.subTest(mf=str(mf)[:60]), self.assertRaises(cfpack.CfPackError):
                cfpack.read(self.pack(mf))
        mr = self.pack(None, {"modrinth.index.json": "{}"})
        self.assertFalse(cfpack.is_cfpack(mr))

    def test_extract_overrides_custom_dir(self):
        mf = dict(MANIFEST, overrides="ov")
        path = self.pack(mf, {"ov/config/a.toml": "x", "ov/../escape.txt": "x", "overrides/no.txt": "x"})
        p = cfpack.read(path)
        game = os.path.join(self.dir, "game")
        self.assertEqual(mrpack.extract_overrides(path, game, (p["overrides_dir"] + "/",)), 1)
        self.assertTrue(os.path.isfile(os.path.join(game, "config", "a.toml")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "escape.txt")))

    def test_resolve(self):
        p = cfpack.read(self.pack())
        files = {"11": cf_file(11, 1, "jei.jar"), "22": cf_file(22, 2, "faithful.zip"),
                 "33": cf_file(33, 3, "locked.jar", url=None), "55": cf_file(55, 999, "wrong.jar"),
                 "66": cf_file(66, 6, "world.zip")}
        projects = {"1": {"title": "JEI", "class_id": 6, "icon_url": "", "page_url": ""},
                    "2": {"title": "Faithful", "class_id": 12, "icon_url": "", "page_url": ""},
                    "3": {"title": "Locked", "class_id": 6, "icon_url": "", "page_url": "https://cf/locked"},
                    "6": {"title": "World", "class_id": 17, "icon_url": "", "page_url": ""}}
        with mock.patch.object(cfpack.mods.CurseForge, "files", return_value=files), \
                mock.patch.object(cfpack.mods.CurseForge, "projects", return_value=projects):
            items, failed = cfpack.resolve(p, "key")
        self.assertEqual([(i["kind"], i["mf"]["filename"]) for i in items],
                         [("mod", "jei.jar"), ("resourcepack", "faithful.zip")])
        self.assertEqual([t for t, _ in failed], ["Locked", "CurseForge 5", "World"])
        self.assertIn("https://cf/locked", failed[0][1])

    def test_resolve_needs_key(self):
        with self.assertRaises(cfpack.CfPackError):
            cfpack.resolve(cfpack.read(self.pack()), "")


if __name__ == "__main__":
    unittest.main()
