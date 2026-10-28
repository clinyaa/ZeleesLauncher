"""Подпись обновлений: Ed25519 и проверка update.json."""
import unittest
from unittest import mock

from launcher import ed25519, updater

# RFC 8032, раздел 7.1, тесты 1 и 2
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]

SEED = bytes(range(32))
PUB = ed25519.public_key(SEED)


def signed(manifest):
    m = dict(manifest)
    m["signature"] = ed25519.sign(SEED, updater.signed_bytes(m)).hex()
    return m


MANIFEST = {
    "version": "1.0.14",
    "min_bootstrap": 1,
    "package": {"url": "https://zelees.link/update/zelees-app-1.0.14.zip", "sha256": "ab" * 32},
    "installer": {"url": "https://zelees.link/update/ZeleesLauncher-Setup-1.0.14.exe", "sha256": "cd" * 32},
}


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for seed, pub, msg, sig in RFC_VECTORS:
            seed, pub, msg, sig = (bytes.fromhex(x) for x in (seed, pub, msg, sig))
            self.assertEqual(ed25519.public_key(seed), pub)
            self.assertEqual(ed25519.sign(seed, msg), sig)
            self.assertTrue(ed25519.verify(pub, msg, sig))

    def test_rejects_changed_message_or_signature(self):
        sig = ed25519.sign(SEED, b"hello")
        self.assertFalse(ed25519.verify(PUB, b"hellO", sig))
        bad = bytearray(sig)
        bad[0] ^= 1
        self.assertFalse(ed25519.verify(PUB, b"hello", bytes(bad)))
        self.assertFalse(ed25519.verify(ed25519.public_key(b"\x01" * 32), b"hello", sig))

    def test_rejects_wrong_lengths_and_large_s(self):
        sig = ed25519.sign(SEED, b"x")
        self.assertFalse(ed25519.verify(PUB[:31], b"x", sig))
        self.assertFalse(ed25519.verify(PUB, b"x", sig[:63]))
        big_s = sig[:32] + (2 ** 256 - 1).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(PUB, b"x", big_s))

    def test_seed_must_be_32_bytes(self):
        with self.assertRaises(ValueError):
            ed25519.public_key(b"short")


class ManifestTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(updater, "UPDATE_PUBKEY", PUB.hex())
        p.start()
        self.addCleanup(p.stop)

    def test_pubkey_is_valid_point(self):
        self.assertIsNotNone(ed25519._decompress(bytes.fromhex(updater.UPDATE_PUBKEY)))

    def test_signed_bytes_ignore_signature_and_key_order(self):
        a = updater.signed_bytes(dict(MANIFEST, signature="00"))
        b = updater.signed_bytes(dict(reversed(list(MANIFEST.items()))))
        self.assertEqual(a, b)
        self.assertNotIn(b"signature", a)

    def test_valid_manifest_accepted(self):
        m = signed(MANIFEST)
        self.assertIs(updater.verify_manifest(m), m)

    def test_tampered_fields_rejected(self):
        for change in ({"version": "9.9.9"}, {"min_bootstrap": 0},
                       {"package": dict(MANIFEST["package"], sha256="00" * 32)},
                       {"installer": dict(MANIFEST["installer"], url="https://evil.example/x.exe")},
                       {"extra": 1}):
            m = signed(MANIFEST)
            m.update(change)
            with self.subTest(change=change), self.assertRaises(IOError):
                updater.verify_manifest(m)

    def test_missing_or_garbage_signature_rejected(self):
        for sig in (None, "", "zz", "00" * 64):
            m = dict(MANIFEST)
            if sig is not None:
                m["signature"] = sig
            with self.subTest(sig=sig), self.assertRaises(IOError):
                updater.verify_manifest(m)
        with self.assertRaises(IOError):
            updater.verify_manifest(["not", "a", "dict"])

    def test_other_key_rejected(self):
        m = dict(MANIFEST)
        m["signature"] = ed25519.sign(b"\x07" * 32, updater.signed_bytes(m)).hex()
        with self.assertRaises(IOError):
            updater.verify_manifest(m)


class BetaChannelTest(unittest.TestCase):
    URL = "https://zelees.link/update/update.json"

    def setUp(self):
        for p in (mock.patch.object(updater, "UPDATE_PUBKEY", PUB.hex()),
                  mock.patch.object(updater, "APP_VERSION", "1.1.0"),
                  mock.patch.object(updater, "_mirrors", lambda u: [u])):
            p.start()
            self.addCleanup(p.stop)

    def serve(self, stable, beta=None):
        files = {self.URL: stable}
        if beta is not None:
            files[updater.beta_url(self.URL)] = beta

        def get_json(url, timeout=0):
            if url not in files:
                raise IOError("404")
            return files[url]
        return mock.patch.object(updater.net, "get_json", side_effect=get_json)

    def test_beta_url(self):
        self.assertEqual(updater.beta_url(self.URL), "https://zelees.link/update/update-beta.json")
        self.assertEqual(updater.beta_url("https://x/other.json"), "")

    def test_stable_users_ignore_beta(self):
        with self.serve(signed(dict(MANIFEST, version="1.1.1")), signed(dict(MANIFEST, version="1.2.0", channel="beta"))):
            self.assertEqual(updater.check(self.URL)["version"], "1.1.1")

    def test_beta_users_get_newest(self):
        with self.serve(signed(dict(MANIFEST, version="1.1.1")), signed(dict(MANIFEST, version="1.2.0", channel="beta"))):
            m = updater.check(self.URL, beta=True)
        self.assertEqual((m["version"], m["channel"]), ("1.2.0", "beta"))
        # стабильная версия вышла новее беты — бета-тестеры получают её
        with self.serve(signed(dict(MANIFEST, version="1.3.0")), signed(dict(MANIFEST, version="1.2.0", channel="beta"))):
            self.assertEqual(updater.check(self.URL, beta=True)["version"], "1.3.0")

    def test_missing_beta_manifest_is_fine(self):
        with self.serve(signed(dict(MANIFEST, version="1.0.13"))):
            self.assertIsNone(updater.check(self.URL, beta=True))

    def test_beta_manifest_cannot_replace_stable(self):
        beta = signed(dict(MANIFEST, version="1.2.0", channel="beta"))
        with self.serve(beta), self.assertRaises(IOError):
            updater.check(self.URL)
        stable = signed(dict(MANIFEST, version="1.2.0"))  # обычный манифест под видом беты тоже не принимается
        with self.serve(signed(dict(MANIFEST, version="1.0.0")), stable):
            self.assertIsNone(updater.check(self.URL, beta=True))

    def test_channel_is_signed(self):
        m = signed(dict(MANIFEST, version="1.2.0", channel="beta"))
        m["channel"] = "stable"
        with self.assertRaises(IOError):
            updater.verify_manifest(m)


class VersionTest(unittest.TestCase):
    def test_vtuple(self):
        self.assertEqual(updater.vtuple("1.0.13"), (1, 0, 13))
        self.assertEqual(updater.vtuple("v2.1"), (2, 1))
        self.assertEqual(updater.vtuple("1.0.0-beta"), (1, 0, 0))

    def test_newer(self):
        self.assertTrue(updater.newer("1.0.10", "1.0.9"))
        self.assertTrue(updater.newer("1.1", "1.0.99"))
        self.assertFalse(updater.newer("1.0.13", "1.0.13"))
        self.assertFalse(updater.newer("1.0.9", "1.0.10"))

    def test_mirrors(self):
        hosts = updater.premium.API_HOSTS
        url = hosts[0] + "/update/update.json"
        self.assertEqual(updater._mirrors(url), [h + "/update/update.json" for h in hosts])
        self.assertEqual(updater._mirrors("https://other.example/u.json"), ["https://other.example/u.json"])


if __name__ == "__main__":
    unittest.main()
