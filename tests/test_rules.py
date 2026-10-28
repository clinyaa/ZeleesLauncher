"""Правила из version.json и пути Maven."""
import unittest
from unittest import mock

from launcher import rules


def on(os_name, arch="x86_64", version="10.0.19045"):
    return mock.patch.multiple(rules, os_name=lambda: os_name, os_arch=lambda: arch, os_version=lambda: version)


class RulesTest(unittest.TestCase):
    def test_no_rules_allowed(self):
        self.assertTrue(rules.is_allowed(None))
        self.assertTrue(rules.is_allowed([]))

    def test_allow_only_on_os(self):
        r = [{"action": "allow", "os": {"name": "osx"}}]
        with on("windows"):
            self.assertFalse(rules.is_allowed(r))
        with on("osx"):
            self.assertTrue(rules.is_allowed(r))

    def test_disallow_on_os(self):
        r = [{"action": "allow"}, {"action": "disallow", "os": {"name": "osx"}}]
        with on("windows"):
            self.assertTrue(rules.is_allowed(r))
        with on("osx"):
            self.assertFalse(rules.is_allowed(r))

    def test_arch_and_version(self):
        with on("windows", arch="x86"):
            self.assertTrue(rules.is_allowed([{"action": "allow", "os": {"arch": "x86"}}]))
        with on("windows", arch="x86_64"):
            self.assertFalse(rules.is_allowed([{"action": "allow", "os": {"arch": "x86"}}]))
        with on("osx", version="10.5.8"):
            self.assertTrue(rules.is_allowed([{"action": "allow", "os": {"name": "osx", "version": "^10\\.5\\.\\d$"}}]))
            self.assertFalse(rules.is_allowed([{"action": "allow", "os": {"version": "[bad"}}]))

    def test_features(self):
        r = [{"action": "allow", "features": {"has_custom_resolution": True}}]
        self.assertFalse(rules.is_allowed(r))
        self.assertFalse(rules.is_allowed(r, {"has_custom_resolution": False}))
        self.assertTrue(rules.is_allowed(r, {"has_custom_resolution": True}))


class MavenTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(rules.parse_maven("org.lwjgl:lwjgl:3.3.3"), ("org.lwjgl", "lwjgl", "3.3.3", None, "jar"))
        self.assertEqual(rules.parse_maven("org.lwjgl:lwjgl:3.3.3:natives-windows"),
                         ("org.lwjgl", "lwjgl", "3.3.3", "natives-windows", "jar"))
        self.assertEqual(rules.parse_maven("de.oceanlabs.mcp:mcp_config:1.20.1@zip"),
                         ("de.oceanlabs.mcp", "mcp_config", "1.20.1", None, "zip"))

    def test_path(self):
        self.assertEqual(rules.maven_path("net.fabricmc:fabric-loader:0.16.9"),
                         "net/fabricmc/fabric-loader/0.16.9/fabric-loader-0.16.9.jar")
        self.assertEqual(rules.maven_path("org.lwjgl:lwjgl:3.3.3", "natives-linux"),
                         "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-linux.jar")
        self.assertEqual(rules.maven_path("a.b:c:1@zip"), "a/b/c/1/c-1.zip")

    def test_library_key_ignores_version(self):
        self.assertEqual(rules.library_key("org.ow2.asm:asm:9.6"), rules.library_key("org.ow2.asm:asm:9.7"))
        self.assertNotEqual(rules.library_key("org.lwjgl:lwjgl:3.3.3"),
                            rules.library_key("org.lwjgl:lwjgl:3.3.3:natives-windows"))


if __name__ == "__main__":
    unittest.main()
