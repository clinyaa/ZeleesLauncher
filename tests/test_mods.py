"""Метаданные модов из jar и проверка совместимости."""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from launcher import mods

MODS_TOML = """
modLoader="javafml"
loaderVersion="[47,)"
[[mods]] # главный мод
modId="examplemod"
version="1.2.3"
displayName="Example Mod"
[[dependencies.examplemod]]
modId="forge"
mandatory=true
versionRange="[47,)"
[[dependencies.examplemod]]
modId="minecraft"
mandatory=true
versionRange="[1.20.1,1.21)"
[[dependencies.examplemod]]
modId="geckolib"
type="required"
[[dependencies.examplemod]]
modId="jei"
type="optional"
"""


class JarMetaTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def jar(self, files):
        path = os.path.join(self.dir, "mod%d.jar" % len(os.listdir(self.dir)))
        with zipfile.ZipFile(path, "w") as z:
            for name, data in files.items():
                z.writestr(name, data if isinstance(data, str) else json.dumps(data))
        return path

    def test_fabric(self):
        m = mods.read_jar_meta(self.jar({"fabric.mod.json": {
            "id": "sodium", "name": "Sodium", "version": "0.5.11", "provides": ["indium-api"],
            "depends": {"minecraft": ">=1.20.1", "fabricloader": ">=0.15", "fabric-api": "*"}}}))
        self.assertEqual(m["loader"], "fabric")
        self.assertEqual(m["ids"], ["sodium", "indium-api"])
        self.assertEqual(m["mc"], ("fabric", ">=1.20.1"))
        self.assertEqual(m["deps"], ["fabric-api"])

    def test_quilt(self):
        m = mods.read_jar_meta(self.jar({"quilt.mod.json": {"quilt_loader": {
            "id": "qmod", "version": "1.0", "metadata": {"name": "Q Mod"},
            "depends": ["quilt_loader", {"id": "minecraft", "versions": "1.21"},
                        {"id": "qsl"}, {"id": "maybe", "optional": True}]}}}))
        self.assertEqual((m["loader"], m["name"], m["mc"]), ("quilt", "Q Mod", ("fabric", "1.21")))
        self.assertEqual(m["deps"], ["qsl"])

    def test_forge_toml(self):
        m = mods.read_jar_meta(self.jar({"META-INF/mods.toml": MODS_TOML}))
        self.assertEqual((m["loader"], m["ids"], m["name"], m["version"]),
                         ("forge", ["examplemod"], "Example Mod", "1.2.3"))
        self.assertEqual(m["mc"], ("maven", "[1.20.1,1.21)"))
        self.assertEqual(m["deps"], ["geckolib"])

    def test_neoforge_toml(self):
        m = mods.read_jar_meta(self.jar({"META-INF/neoforge.mods.toml": MODS_TOML}))
        self.assertEqual(m["loader"], "neoforge")

    def test_multiloader_jar_picks_pack_loader(self):
        # «универсальный» jar с описаниями и для Fabric, и для Forge (Collective, Terralith, ...)
        path = self.jar({"fabric.mod.json": {"id": "examplemod", "depends": {"minecraft": "1.20.1"}},
                         "META-INF/mods.toml": MODS_TOML})
        self.assertEqual(mods.read_jar_meta(path)["loader"], "fabric")  # без загрузчика — первое описание
        self.assertEqual(mods.read_jar_meta(path, "fabric")["loader"], "fabric")
        forge = mods.read_jar_meta(path, "forge")
        self.assertEqual((forge["loader"], forge["deps"]), ("forge", ["geckolib"]))
        self.assertIs(mods.jar_compat(forge, "1.20.1", "forge")[0], True)
        self.assertEqual(mods.read_jar_meta(path, "neoforge")["loader"], "forge")  # NeoForge 1.20.1 берёт Forge-мод
        self.assertEqual(mods.read_jar_meta(path, "quilt")["loader"], "fabric")

    def test_optional_neoforge_dep_keeps_forge(self):
        opt = MODS_TOML + '\n[[dependencies.examplemod]]\nmodId="neoforge"\nmandatory=false\nversionRange="[20,)"\n'
        self.assertEqual(mods.read_jar_meta(self.jar({"META-INF/mods.toml": opt}))["loader"], "forge")
        req = MODS_TOML + '\n[[dependencies.examplemod]]\nmodId="neoforge"\nmandatory=true\nversionRange="[20,)"\n'
        self.assertEqual(mods.read_jar_meta(self.jar({"META-INF/mods.toml": req}))["loader"], "neoforge")

    def test_mini_toml_matches_tomllib(self):
        t = mods._mini_toml(MODS_TOML)
        self.assertEqual(t["mods"][0]["modId"], "examplemod")
        self.assertEqual([d["modId"] for d in t["dependencies"]["examplemod"]], ["forge", "minecraft", "geckolib", "jei"])
        self.assertIs(t["dependencies"]["examplemod"][0]["mandatory"], True)

    def test_mcmod_info(self):
        m = mods.read_jar_meta(self.jar({"mcmod.info": [{"modid": "old", "name": "Old", "mcversion": "1.7.10"}]}))
        self.assertEqual((m["loader"], m["mc"]), ("forge", ("exact", "1.7.10")))

    def test_not_a_mod(self):
        self.assertIsNone(mods.read_jar_meta(self.jar({"readme.txt": "hi"})))
        bad = os.path.join(self.dir, "bad.jar")
        with open(bad, "wb") as f:
            f.write(b"not a zip")
        self.assertIsNone(mods.read_jar_meta(bad))


class ModFolderUpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.folder = mods.ModFolder(self.dir)

    def put(self, fn, project="P1", indexed=True):
        with open(os.path.join(self.dir, fn), "wb") as f:
            f.write(b"old")
        if indexed:
            self.folder.index[mods.ModFolder.base_name(fn)] = {"source": "modrinth", "project_id": project,
                                                               "version_id": "v1", "title": "Mod"}
            self.folder.save_index()

    def update(self, old_file, new_name):
        mf = {"source": "modrinth", "project_id": "P1", "version_id": "v2", "version_number": "2.0",
              "filename": new_name, "url": "https://cdn.modrinth.com/x/" + new_name, "sha1": None}

        def fake_download(url, dest, sha1=None, cancel=None):
            with open(dest, "wb") as f:
                f.write(b"new")
        with mock.patch.object(mods.net, "download", side_effect=fake_download):
            self.folder.update(old_file, mf, "Mod")

    def files(self):
        return sorted(f for f in os.listdir(self.dir) if not f.startswith("."))

    def test_enabled_mod(self):
        self.put("mod-1.0.jar")
        self.update("mod-1.0.jar", "mod-2.0.jar")
        self.assertEqual(self.files(), ["mod-2.0.jar"])
        self.assertEqual(self.folder.index["mod-2.0.jar"]["version_id"], "v2")
        self.assertNotIn("mod-1.0.jar", self.folder.index)

    def test_disabled_mod_stays_disabled(self):
        self.put("mod-1.0.jar.disabled")
        self.update("mod-1.0.jar.disabled", "mod-2.0.jar")
        self.assertEqual(self.files(), ["mod-2.0.jar.disabled"])
        self.assertIn("mod-2.0.jar", self.folder.index)

    def test_same_file_name(self):
        self.put("mod.jar.disabled")
        self.update("mod.jar.disabled", "mod.jar")
        self.assertEqual(self.files(), ["mod.jar.disabled"])
        with open(os.path.join(self.dir, "mod.jar.disabled"), "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_mod_downloaded_by_hand(self):
        self.put("handmade-1.0.jar", indexed=False)  # найден на Modrinth по хэшу, в индексе его нет
        self.update("handmade-1.0.jar", "handmade-2.0.jar")
        self.assertEqual(self.files(), ["handmade-2.0.jar"])


class CompatTest(unittest.TestCase):
    def fabric(self, spec):
        return {"loader": "fabric", "mc": ("fabric", spec)}

    def test_fabric_predicates(self):
        cases = [
            ("*", "1.20.1", True), (">=1.20", "1.20.1", True), (">=1.20.2", "1.20.1", False),
            ("1.20.x", "1.20.4", True), ("1.20.x", "1.21", False), ("~1.20", "1.20.6", True),
            ("~1.20", "1.21", False), ("^1.20", "1.99", True), (">=1.20 <1.21", "1.20.6", True),
            (">=1.20 <1.21", "1.21", False), (["1.19.4", "1.20.1"], "1.20.1", True),
            ("1.21-beta.1", "1.21", True), ("<1.20-alpha", "1.21", False),
        ]
        for spec, mc, want in cases:
            with self.subTest(spec=spec, mc=mc):
                self.assertIs(mods.jar_compat(self.fabric(spec), mc, "fabric")[0], want)

    def test_maven_ranges(self):
        cases = [
            ("[1.20.1,1.21)", "1.20.1", True), ("[1.20.1,1.21)", "1.21", False),
            ("(1.20,1.20.4]", "1.20", False), ("(1.20,1.20.4]", "1.20.4", True),
            ("[1.20,)", "1.21.1", True), ("[1.19.2]", "1.19.2", True), ("[1.19.2]", "1.20", False),
            ("[1.18,1.19),[1.20,1.21)", "1.20.1", True),
        ]
        for spec, mc, want in cases:
            with self.subTest(spec=spec, mc=mc):
                meta = {"loader": "forge", "mc": ("maven", spec)}
                self.assertIs(mods.jar_compat(meta, mc, "forge")[0], want)

    def test_unknown_is_not_blocking(self):
        self.assertIsNone(mods.jar_compat(self.fabric("weird"), "1.20.1", "fabric")[0])
        self.assertIsNone(mods.jar_compat({"loader": "fabric", "mc": None}, "1.20.1", "fabric")[0])
        self.assertIsNone(mods.jar_compat(self.fabric("*"), "24w14a", "fabric")[0])  # снапшот
        self.assertIsNone(mods.jar_compat(None, "1.20.1", "fabric")[0])

    def test_loader_mismatch(self):
        self.assertIs(mods.jar_compat(self.fabric("*"), "1.20.1", "forge")[0], False)
        self.assertIs(mods.jar_compat(self.fabric("*"), "1.20.1", "quilt")[0], True)  # Quilt запускает Fabric-моды
        forge = {"loader": "forge", "mc": ("maven", "[1.20.1]")}
        self.assertIs(mods.jar_compat(forge, "1.20.1", "neoforge")[0], True)  # на 1.20.1 NeoForge совместим с Forge
        self.assertIs(mods.jar_compat(dict(forge, mc=("maven", "[1.21]")), "1.21", "neoforge")[0], False)

    def test_exact(self):
        meta = {"loader": "forge", "mc": ("exact", "1.7.10")}
        self.assertIs(mods.jar_compat(meta, "1.7.10", "forge")[0], True)
        self.assertIs(mods.jar_compat(meta, "1.12.2", "forge")[0], False)


if __name__ == "__main__":
    unittest.main()


class PreflightTest(unittest.TestCase):
    """Проверка папки mods перед запуском игры (без интернета)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def fabric(self, name, mod_id, version="1.0", depends=None, breaks=None, mc="1.21.4"):
        path = os.path.join(self.dir, name)
        d = {"id": mod_id, "name": mod_id.title(), "version": version, "depends": dict({"minecraft": mc}, **(depends or {}))}
        if breaks:
            d["breaks"] = breaks
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("fabric.mod.json", json.dumps(d))
        return path

    def test_clean_folder(self):
        self.fabric("api.jar", "fabric-api")
        self.fabric("sodium.jar", "sodium", depends={"fabric-api": "*"})
        self.assertEqual(mods.preflight(self.dir, "1.21.4", "fabric"), [])

    def test_problems_found(self):
        self.fabric("sodium.jar", "sodium", "0.6.0", depends={"fabric-api": "*"})
        self.fabric("sodium-old.jar", "sodium", "0.5.0")
        self.fabric("old.jar", "oldmod", mc="1.20.1")
        self.fabric("of.jar", "optifabric", "1.14.3")
        self.fabric("iris.jar", "iris", breaks={"optifabric": "*"})
        p = mods.preflight(self.dir, "1.21.4", "fabric")
        text = "\n".join(p)
        self.assertIn("не хватает fabric-api", text)
        self.assertIn("«Sodium» установлен 2 раза: sodium-old.jar, sodium.jar", text)
        self.assertIn("«Oldmod» — мод рассчитан на Minecraft 1.20.1", text)
        self.assertIn("«Iris» несовместим с «Optifabric» 1.14.3", text)

    def test_breaks_version_range(self):
        self.fabric("modmenu.jar", "modmenu", "9.0.0+mc1.21")
        self.fabric("a.jar", "amod", breaks={"modmenu": "<8.0"})
        self.fabric("b.jar", "bmod", breaks={"modmenu": ">=9"})
        p = mods.preflight(self.dir, "1.21.4", "fabric")
        self.assertEqual(p, ["«Bmod» несовместим с «Modmenu» 9.0.0+mc1.21"])

    def test_dependency_version_range(self):
        # Bosses of Mass Destruction требует Cerbons API ниже 1.1.0 — Forge с 1.1.0 не запустится
        self.fabric("cerbons.jar", "cerbons_api", "1.1.0")
        self.fabric("bomd.jar", "bomd", depends={"cerbons_api": "<1.1.0"})
        self.fabric("ok.jar", "okmod", depends={"cerbons_api": ">=1.0"})
        p = mods.preflight(self.dir, "1.21.4", "fabric")
        self.assertEqual(p, ["«Bomd» требует «Cerbons_Api» <1.1.0, а установлена 1.1.0"])

    def test_update_held_by_dependency_range(self):
        self.fabric("cerbons.jar", "cerbons_api", "1.0.0")
        self.fabric("bomd.jar", "bomd", depends={"cerbons_api": "<1.1.0"})
        self.fabric("other.jar", "other", "2.0")
        ident = {"cerbons": {"version_id": "c1", "project_id": "pc", "version_number": "1.0.0", "deps": [],
                             "game_versions": ["1.21.4"], "loaders": ["fabric"]},
                 "other": {"version_id": "o1", "project_id": "po", "version_number": "2.0", "deps": [],
                           "game_versions": ["1.21.4"], "loaders": ["fabric"]}}
        upd = {"cerbons": {"version_id": "c2", "version_number": "1.21.4-1.1.0"},
               "other": {"version_id": "o2", "version_number": "2.1"}}
        sha = {"cerbons.jar": "cerbons", "bomd.jar": "bomd", "other.jar": "other"}
        with mock.patch.object(mods.net, "sha1_file", side_effect=lambda p: sha[os.path.basename(p)]),                 mock.patch.object(mods.Modrinth, "identify", return_value=ident),                 mock.patch.object(mods.Modrinth, "updates", return_value=upd):
            res = {r["file"]: r for r in mods.check_folder(mods.ModFolder(self.dir), "1.21.4", "fabric")}
        self.assertIsNone(res["cerbons.jar"]["update"])
        self.assertEqual(res["cerbons.jar"]["update_held"], "«Bomd» требует cerbons_api <1.1.0")
        self.assertEqual(res["other.jar"]["update"]["version_id"], "o2")
        self.assertIsNone(res["other.jar"]["update_held"])

    def test_update_version_parsing(self):
        for num, want in (("1.1.0", (1, 1, 0)), ("1.20.1-1.1.0", (1, 1, 0)), ("1.3.6+1.20.1-patch.1", (1, 3, 6)),
                          ("1.20-4.0.6", (4, 0, 6)), ("1.20.4-1.5.5", (1, 5, 5)), ("1.20.1-1.16.0", (1, 16, 0)),
                          ("v8.0.8-1.20.1-Forge", (8, 0, 8)), ("beta", None)):
            with self.subTest(num=num):
                self.assertEqual(mods._update_version(num, "1.20.1"), want)

    def test_nested_jar_in_jar_counts_as_installed(self):
        def jar(files):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for name, data in files.items():
                    z.writestr(name, data if isinstance(data, (str, bytes)) else json.dumps(data))
            return buf.getvalue()
        lib = jar({"fabric.mod.json": {"id": "puzzlesaccessapi", "version": "20.1.1"}})
        with open(os.path.join(self.dir, "puzzles.jar"), "wb") as f:
            f.write(jar({"fabric.mod.json": {"id": "puzzleslib", "depends": {"puzzlesaccessapi": "*"}},
                         "META-INF/jars/puzzlesaccessapi.jar": lib}))
        # обёртка без своего описания (как kotlinforforge-all.jar) — описание из вложенного мода
        with open(os.path.join(self.dir, "kff-all.jar"), "wb") as f:
            f.write(jar({"META-INF/jars/kffmod.jar": jar({"fabric.mod.json": {"id": "kotlinforforge"}})}))
        self.fabric("user.jar", "usermod", depends={"puzzleslib": "*", "kotlinforforge": "*"})
        self.assertEqual(mods.preflight(self.dir, "1.21.4", "fabric"), [])
        self.assertEqual(mods.read_jar_meta(os.path.join(self.dir, "kff-all.jar"))["ids"], ["kotlinforforge"])

    def test_disabled_mods_ignored(self):
        self.fabric("old.jar.disabled", "oldmod", mc="1.20.1")
        self.assertEqual(mods.preflight(self.dir, "1.21.4", "fabric"), [])

    def test_forge_incompatible(self):
        toml = MODS_TOML.replace('modId="jei"\ntype="optional"', 'modId="rubidium"\ntype="incompatible"\n'
                                                                    'versionRange="[0.6,)"')
        path = os.path.join(self.dir, "ex.jar")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("META-INF/mods.toml", toml)
        m = mods.read_jar_meta(path)
        self.assertEqual(m["breaks"], [("rubidium", "maven", "[0.6,)")])
        self.assertEqual(m["deps"], ["geckolib"])
