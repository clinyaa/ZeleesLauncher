"""Метаданные модов из jar и проверка совместимости."""
import json
import os
import shutil
import tempfile
import unittest
import zipfile

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
