"""Коды сборок, адреса серверов, LAN-объявления, лог игры и командная строка."""
import os
import shutil
import tempfile
import unittest

from launcher import lan, launch, mc_status, packs


class PackCodeTest(unittest.TestCase):
    def test_parse_code(self):
        for text in ("K7QF-M2XA", "k7qf m2xa", "K7QFM2XA", "https://zelees.link/p/K7QF-M2XA", "zelees.link/p/k7qfm2xa "):
            with self.subTest(text=text):
                self.assertEqual(packs.parse_code(text), "K7QF-M2XA")
        for text in ("", None, "K7QF-M2X", "K7QF-M2XA1", "K7QF-M2X0", "IIII-OOOO"):  # 0, 1, I, O не входят в алфавит
            with self.subTest(text=text):
                self.assertIsNone(packs.parse_code(text))

    def test_slug(self):
        self.assertEqual(packs.slug("Моя Сборка 1.20"), "moya-sborka-1.20")
        self.assertEqual(packs.slug("!!!"), "sborka")
        self.assertLessEqual(len(packs.slug("x" * 100)), 32)

    def test_safe_name(self):
        self.assertEqual(packs._safe_name("sodium-0.5.jar"), "sodium-0.5.jar")
        self.assertEqual(packs._safe_name("../../evil.jar"), "evil.jar")
        self.assertEqual(packs._safe_name("..\\..\\evil.jar"), "evil.jar")
        for bad in ("", None, "..", ".hidden", "dir/"):
            with self.subTest(bad=bad):
                self.assertIsNone(packs._safe_name(bad))

    def test_make_pack(self):
        files = [{"kind": "mod", "source": "modrinth", "project": "AANobbMI", "version": "v1", "title": "Sodium"},
                 {"kind": "mod", "source": "modrinth", "project": "P", "version": "v2", "off": True, "title": "X"}]
        p = packs.make_pack("  " + "N" * 50, "1.20.1", "fabric", "0.16.9", files)
        self.assertEqual(len(p["name"]), 40)
        self.assertEqual(p["files"][0], {"kind": "mod", "source": "modrinth", "project": "AANobbMI", "version": "v1"})
        self.assertTrue(p["files"][1]["off"])


class AddressTest(unittest.TestCase):
    def test_parse_address(self):
        self.assertEqual(mc_status.parse_address("Play.Example.com"), ("play.example.com", 0))
        self.assertEqual(mc_status.parse_address("play.example.com:25566"), ("play.example.com", 25566))
        self.assertEqual(mc_status.parse_address("host:25565"), ("host", 0))
        for bad in ("host:abc", "host:0", "host:70000"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                mc_status.parse_address(bad)
        self.assertEqual(mc_status.display_address("h", 0), "h")
        self.assertEqual(mc_status.display_address("h", 1234), "h:1234")

    def test_motd_text(self):
        self.assertEqual(mc_status.motd_text("§aHello  §lworld"), "Hello world")
        desc = {"text": "A", "extra": [{"text": "§cB"}, "\n  C  "]}
        self.assertEqual(mc_status.motd_text(desc), "AB\nC")
        self.assertEqual(mc_status.motd_text(None), "")

    def test_is_public(self):
        self.assertTrue(mc_status.is_public("8.8.8.8"))
        for ip in ("192.168.1.5", "10.0.0.1", "127.0.0.1", "not-an-ip"):
            with self.subTest(ip=ip):
                self.assertFalse(mc_status.is_public(ip))

    def test_lan_announce(self):
        self.assertEqual(lan.parse_announce(b"[MOTD]My World[/MOTD][AD]51234[/AD]"), ("My World", None, 51234))
        self.assertEqual(lan.parse_announce(b"[MOTD][/MOTD][AD]10.0.0.2:25565[/AD]"), ("LAN-мир", "10.0.0.2", 25565))
        for bad in (b"garbage", b"[MOTD]x[/MOTD][AD]abc[/AD]", b"[MOTD]x[/MOTD][AD]99999[/AD]"):
            with self.subTest(bad=bad):
                self.assertIsNone(lan.parse_announce(bad))


class LogParserTest(unittest.TestCase):
    def test_plain_and_xml(self):
        p = launch.LogParser()
        self.assertEqual(p.feed("plain line\n"), ("plain line", "INFO"))
        self.assertIsNone(p.feed('<log4j:Event logger="net.minecraft" timestamp="1" level="WARN" thread="Render thread">'))
        self.assertIsNone(p.feed("  <log4j:Message><![CDATA[Something odd]]></log4j:Message>"))
        self.assertEqual(p.feed("</log4j:Event>"), ("[Render thread/WARN] Something odd", "WARN"))
        one = '<log4j:Event logger="a" level="ERROR" thread="main"><log4j:Message><![CDATA[boom]]></log4j:Message></log4j:Event>'
        self.assertEqual(p.feed(one), ("[main/ERROR] boom", "ERROR"))


ACCOUNT = {"type": "microsoft", "mc_token": "SECRET.TOKEN", "id": "0123-4567", "name": "Steve", "xuid": "42"}
SETTINGS = {"width": 854, "height": 480, "memory_max": 4096, "memory_min": 1024, "jvm_args": "-Dfoo=bar",
            "fullscreen": False}
MODERN = {
    "id": "1.21", "mainClass": "net.minecraft.client.main.Main", "assetIndex": {"id": "17"},
    "arguments": {
        "game": ["--username", "${auth_player_name}", "--accessToken", "${auth_access_token}",
                 {"rules": [{"action": "allow", "features": {"has_custom_resolution": True}}],
                  "value": ["--width", "${resolution_width}", "--height", "${resolution_height}"]},
                 {"rules": [{"action": "allow", "features": {"is_demo_user": True}}], "value": "--demo"},
                 {"rules": [{"action": "allow", "features": {"is_quick_play_multiplayer": True}}],
                  "value": ["--quickPlayMultiplayer", "${quickPlayMultiplayer}"]}],
        "jvm": ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"],
    },
    "libraries": [],
}
LEGACY = {"id": "1.8.9", "mainClass": "net.minecraft.client.main.Main", "assets": "1.8",
          "minecraftArguments": "--username ${auth_player_name} --session ${auth_session}", "libraries": []}


class BuildCommandTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def build(self, vdata, **kw):
        cmd, natives = launch.build_command(vdata, self.root, self.root, "java", ACCOUNT, SETTINGS, **kw)
        self.assertTrue(os.path.isdir(natives))
        return cmd

    def test_modern(self):
        cmd = self.build(MODERN)
        self.assertEqual(cmd[0], "java")
        self.assertIn("-Xmx4096M", cmd)
        self.assertIn("-Dfoo=bar", cmd)
        main = cmd.index("net.minecraft.client.main.Main")
        self.assertLess(cmd.index("-cp"), main)
        self.assertEqual(cmd[main + 1:main + 3], ["--username", "Steve"])
        self.assertIn("--width", cmd)
        self.assertNotIn("--demo", cmd)
        self.assertNotIn("--quickPlayMultiplayer", cmd)
        self.assertNotIn("${", " ".join(cmd))

    def test_quick_play_and_legacy_server(self):
        cmd = self.build(MODERN, server=("play.example.com", 0))
        self.assertEqual(cmd[cmd.index("--quickPlayMultiplayer") + 1], "play.example.com")
        self.assertNotIn("--server", cmd)
        cmd = self.build(LEGACY, server=("play.example.com", 0), demo=True)
        self.assertEqual(cmd[cmd.index("--server"):cmd.index("--server") + 4],
                         ["--server", "play.example.com", "--port", "25565"])
        self.assertIn("token:SECRET.TOKEN:01234567", cmd)
        self.assertIn("--demo", cmd)
        self.assertIn("--width", cmd)

    def test_requires_microsoft_account(self):
        with self.assertRaises(ValueError):
            launch.build_command(MODERN, self.root, self.root, "java", {"type": "offline", "name": "x"}, SETTINGS)

    def test_mask_token(self):
        cmd = self.build(LEGACY)
        masked = " ".join(launch.mask_cmd(cmd, ACCOUNT))
        self.assertNotIn("SECRET.TOKEN", masked)
        self.assertIn("token:***:01234567", masked)


if __name__ == "__main__":
    unittest.main()
