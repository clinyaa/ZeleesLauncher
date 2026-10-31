"""Свой набор корневых сертификатов: на ПК без докачанных корней Windows загрузки не должны падать."""
import ssl
import unittest

from launcher import ca_bundle, net


class CaBundleTest(unittest.TestCase):
    def test_bundle_has_new_roots(self):
        self.assertGreater(ca_bundle.CA_PEM.count("BEGIN CERTIFICATE"), 100)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_verify_locations(cadata=ca_bundle.CA_PEM)  # набор читается без ошибок
        names = {dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()}
        self.assertIn("Certainly Root R1", names)  # корень cdn.modrinth.com
        self.assertIn("ISRG Root X1", names)

    def test_default_context_includes_bundle(self):
        ctx = net._default_context()
        names = {dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()}
        self.assertIn("Certainly Root R1", names)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
