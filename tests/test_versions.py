"""Сборка version.json по цепочке inheritsFrom, определение загрузчика и Java."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from launcher import java, launch, loaders, versions

VANILLA = {
    "id": "1.20.1", "type": "release", "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "5"}, "javaVersion": {"component": "java-runtime-gamma", "majorVersion": 17},
    "arguments": {"game": ["--username", "${auth_player_name}"], "jvm": ["-cp", "${classpath}"]},
    "libraries": [
        {"name": "org.ow2.asm:asm:9.3"},
        {"name": "org.lwjgl:lwjgl:3.3.1"},
        {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows", "rules": [{"action": "allow", "os": {"name": "windows"}}]},
        {"name": "org.lwjgl:lwjgl:3.3.1:natives-linux", "rules": [{"action": "allow", "os": {"name": "linux"}}]},
    ],
}
FABRIC = {
    "id": "fabric-loader-0.16.9-1.20.1", "inheritsFrom": "1.20.1",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "arguments": {"game": [], "jvm": ["-DFabricMcEmu= net.minecraft.client.main.Main "]},
    "libraries": [{"name": "org.ow2.asm:asm:9.6"}, {"name": "net.fabricmc:fabric-loader:0.16.9"}],
}


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def put(self, data):
        path = versions.version_json_path(self.root, data["id"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_vanilla(self):
        self.put(VANILLA)
        vd = versions.resolve_version(self.root, "1.20.1", None)
        self.assertEqual(vd["_jar"], "1.20.1")
        self.assertEqual(vd["_chain"], ["1.20.1"])

    def test_inherits_merges(self):
        self.put(VANILLA)
        self.put(FABRIC)
        vd = versions.resolve_version(self.root, FABRIC["id"], None)
        self.assertEqual(vd["mainClass"], FABRIC["mainClass"])
        self.assertEqual(vd["_jar"], "1.20.1")
        self.assertEqual(vd["_chain"], [FABRIC["id"], "1.20.1"])
        self.assertNotIn("inheritsFrom", vd)
        names = [lib["name"] for lib in vd["libraries"]]
        # у дочерней версии приоритет, одинаковые библиотеки не дублируются, natives с разными правилами остаются
        self.assertIn("org.ow2.asm:asm:9.6", names)
        self.assertNotIn("org.ow2.asm:asm:9.3", names)
        self.assertIn("org.lwjgl:lwjgl:3.3.1:natives-windows", names)
        self.assertIn("org.lwjgl:lwjgl:3.3.1:natives-linux", names)
        self.assertEqual(vd["arguments"]["jvm"], ["-cp", "${classpath}", "-DFabricMcEmu= net.minecraft.client.main.Main "])
        self.assertEqual(vd["arguments"]["game"], VANILLA["arguments"]["game"])
        self.assertEqual(versions.required_java(vd), ("java-runtime-gamma", 17))

    def test_forge_client_jar_named_after_version(self):
        forge = {"id": "1.20.1-forge-47.4.28", "inheritsFrom": "1.20.1",
                 "arguments": {"jvm": ["-DignoreList=bootstraplauncher,forge-,${version_name}.jar"]}}
        self.put(VANILLA)
        self.put(forge)
        vanilla_jar = versions.version_jar_path(self.root, "1.20.1")
        with open(vanilla_jar, "wb") as f:
            f.write(b"PK-vanilla")
        vd = versions.resolve_version(self.root, forge["id"], None)
        own = launch.own_client_jar(vd, self.root, vanilla_jar)
        self.assertEqual(own, versions.version_jar_path(self.root, forge["id"]))
        with open(own, "rb") as f:
            self.assertEqual(f.read(), b"PK-vanilla")
        # Fabric и ванильные версии берут jar родителя как есть
        self.put(FABRIC)
        vd = versions.resolve_version(self.root, FABRIC["id"], None)
        self.assertEqual(launch.own_client_jar(vd, self.root, vanilla_jar), vanilla_jar)
        vd = versions.resolve_version(self.root, "1.20.1", None)
        self.assertEqual(launch.own_client_jar(vd, self.root, vanilla_jar), vanilla_jar)

    def test_missing_version(self):
        with self.assertRaises(FileNotFoundError):
            versions.resolve_version(self.root, "nope", None)

    def test_inheritance_loop(self):
        self.put({"id": "a", "inheritsFrom": "b"})
        self.put({"id": "b", "inheritsFrom": "a"})
        with self.assertRaises(RuntimeError):
            versions.resolve_version(self.root, "a", None)

    def test_local_versions(self):
        self.put(VANILLA)
        self.put(dict(FABRIC, zeleesName="Моя сборка", zeleesPack="K7QFM2XA"))
        os.makedirs(os.path.join(self.root, "versions", "broken"))
        got = {v["id"]: v for v in versions.local_versions(self.root)}
        self.assertEqual(set(got), {"1.20.1", FABRIC["id"]})
        self.assertEqual(got[FABRIC["id"]]["pack"], "K7QFM2XA")
        self.assertFalse(versions.is_installed(self.root, FABRIC["id"]))  # у родителя нет jar


class DetectLoaderTest(unittest.TestCase):
    def test_fabric_quilt(self):
        vd = {"_chain": ["x", "1.20.1"], "libraries": [{"name": "net.fabricmc:fabric-loader:0.16.9"}]}
        self.assertEqual(loaders.detect(vd), ("fabric", "0.16.9", "1.20.1"))
        vd = {"_chain": ["x", "1.21"], "libraries": [{"name": "org.quiltmc:quilt-loader:0.27.1"}]}
        self.assertEqual(loaders.detect(vd), ("quilt", "0.27.1", "1.21"))

    def test_forge_neoforge_by_args(self):
        vd = {"_chain": ["x", "1.20.1"], "arguments": {"game": ["--fml.forgeVersion", "47.3.0"]}}
        self.assertEqual(loaders.detect(vd), ("forge", "47.3.0", "1.20.1"))
        vd = {"_chain": ["x", "1.21.1"],
              "arguments": {"game": ["--fml.forgeVersion", "x", "--fml.neoForgeVersion", "21.1.77"]}}
        self.assertEqual(loaders.detect(vd), ("neoforge", "21.1.77", "1.21.1"))

    def test_old_forge_by_library(self):
        vd = {"_chain": ["x", "1.12.2"], "libraries": [{"name": "net.minecraftforge:forge:1.12.2-14.23.5.2860"}]}
        self.assertEqual(loaders.detect(vd), ("forge", "14.23.5.2860", "1.12.2"))

    def test_vanilla(self):
        self.assertEqual(loaders.detect({"id": "1.8.9", "minecraftArguments": "--username x"}), (None, "", "1.8.9"))

    def test_neoforge_mc(self):
        self.assertEqual(loaders.neoforge_mc("21.1.77"), "1.21.1")
        self.assertEqual(loaders.neoforge_mc("20.4.1-beta"), "1.20.4")
        self.assertEqual(loaders.neoforge_mc("21.0.1"), "1.21")
        self.assertEqual(loaders.neoforge_mc("26.1.0.5"), "26.1")
        self.assertIsNone(loaders.neoforge_mc("garbage"))

    def test_forge_versions_fallback_url(self):
        promos = {"promos": {"1.20.1-recommended": "47.4.0", "1.20.1-latest": "47.4.28"}}
        calls = []

        def get_json(url):
            calls.append(url)
            if url == loaders.FORGE_PROMOS[0]:
                raise IOError("HTTP 404")
            return promos
        with mock.patch.object(loaders.net, "get_json", get_json):
            self.assertEqual(loaders.loader_versions("forge", "1.20.1"), ["47.4.0", "47.4.28"])
        self.assertEqual(calls, list(loaders.FORGE_PROMOS))
        with mock.patch.object(loaders.net, "get_json", side_effect=IOError("HTTP 404")), \
                self.assertRaises(IOError):
            loaders.loader_versions("forge", "1.20.1")

    def test_modrinth_loaders(self):
        self.assertEqual(loaders.modrinth_loaders("quilt"), ["quilt", "fabric"])
        self.assertEqual(loaders.modrinth_loaders("forge"), ["forge"])
        self.assertEqual(loaders.modrinth_loaders(""), [])


class JavaTest(unittest.TestCase):
    def j(self, major, is64=True, source="PATH"):
        return java.JavaInstall("java%d" % major, str(major), major, is64, source)

    def test_parse_major(self):
        self.assertEqual(java.parse_major('"1.8.0_402"'), 8)
        self.assertEqual(java.parse_major("17.0.10"), 17)
        self.assertEqual(java.parse_major("21"), 21)
        self.assertEqual(java.parse_major(""), 0)

    def test_compatible(self):
        self.assertTrue(java.is_compatible(self.j(8), 8))
        self.assertFalse(java.is_compatible(self.j(17), 8))  # старые версии на 9+ падают
        self.assertTrue(java.is_compatible(self.j(21), 17))
        self.assertFalse(java.is_compatible(self.j(17), 21))

    def test_pick_prefers_exact_64bit_own(self):
        installs = [self.j(21), self.j(17, is64=False), self.j(17), self.j(17, source="ZeleesLauncher")]
        best = java.pick_java(installs, 17)
        self.assertEqual((best.major, best.is64, best.source), (17, True, "ZeleesLauncher"))
        self.assertIsNone(java.pick_java([self.j(17)], 21))


if __name__ == "__main__":
    unittest.main()
