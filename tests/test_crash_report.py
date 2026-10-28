"""Отчёты об ошибках: что уходит разработчику и когда."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from launcher import crash_report as cr
from launcher import net

TRACE = '''Traceback (most recent call last):
  File "C:\\Users\\Steff\\AppData\\Roaming\\ZeleesLauncher\\app\\app-1.1.0.zip\\launcher\\gui.py", line 92, in run
    fn(*a)
  File "E:\\Games\\ZeleesLauncher\\launcher\\mods_page.py", line 120, in reload_builds
    vd = resolve_version(root, lv["id"], self.app.manifest)
KeyError: 'id' (profile C:/Users/steff/Desktop, user steff, token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij)
'''


class FakeSettings(dict):
    def get(self, k):
        return dict.get(self, k)

    def set(self, k, v, save=True):
        self[k] = v


def boom(exc):
    try:
        raise exc
    except Exception as e:  # noqa: BLE001
        return e


class ScrubTest(unittest.TestCase):
    def test_removes_personal_data(self):
        out = cr.scrub(TRACE, home=r"C:\Users\Steff", user="steff",
                       dirs=[(r"C:\Users\Steff\AppData\Roaming\ZeleesLauncher", "<data>"),
                             (r"E:\Games\ZeleesLauncher", "<launcher>")])
        self.assertNotIn("steff", out.lower())
        self.assertNotIn("eyJ", out)
        self.assertIn('File "<data>\\app\\app-1.1.0.zip\\launcher\\gui.py"', out)
        self.assertIn('File "<launcher>\\launcher\\mods_page.py"', out)
        self.assertIn("profile ~/Desktop", out)
        self.assertIn("KeyError: 'id'", out)  # сама ошибка остаётся

    def test_short_username_not_replaced_everywhere(self):
        self.assertEqual(cr.scrub("in function run", home="", user="un", dirs=[]), "in function run")

    def test_signature_ignores_line_numbers(self):
        other = TRACE.replace("line 92", "line 99").replace("line 120", "line 131")
        self.assertEqual(cr.signature(other), cr.signature(TRACE))
        self.assertNotEqual(cr.signature(TRACE.replace("KeyError", "TypeError")), cr.signature(TRACE))


class ExpectedTest(unittest.TestCase):
    def test_expected_errors_skipped(self):
        for e in (OSError("net"), TimeoutError(), net.HttpError(500, b"", "u"), net.Cancelled()):
            with self.subTest(e=type(e).__name__):
                self.assertTrue(cr.is_expected(e))
        for e in (KeyError("id"), RuntimeError("main thread is not in main loop"), TypeError()):
            with self.subTest(e=type(e).__name__):
                self.assertFalse(cr.is_expected(e))


class ReporterTest(unittest.TestCase):
    def make(self, consent):
        self.sent, self.asked = [], []
        s = FakeSettings(crash_reports=consent)
        r = cr.Reporter(s, self.asked.append, self.sent.append)
        patch = mock.patch.object(cr.threading, "Thread",
                                  side_effect=lambda target, args, daemon: mock.Mock(start=lambda: target(*args)))
        patch.start()
        self.addCleanup(patch.stop)
        return r, s

    def test_disabled_sends_nothing(self):
        r, _s = self.make(False)
        r.exception(boom(KeyError("x")))
        self.assertEqual((self.sent, self.asked), ([], []))

    def test_enabled_sends_unexpected_only(self):
        r, _s = self.make(True)
        r.exception(boom(OSError("нет сети")))
        r.exception(boom(KeyError("x")))
        self.assertEqual(len(self.sent), 1)
        p = self.sent[0]
        self.assertEqual(set(p), {"version", "kind", "os", "trace"})
        self.assertIn("KeyError", p["trace"])

    def test_same_error_once_and_session_limit(self):
        r, _s = self.make(True)
        for _ in range(3):
            r.exception(boom(KeyError("x")))
        self.assertEqual(len(self.sent), 1)
        for i in range(10):
            r.report('File "a.py", line 1, in f%d\nValueError: %d' % (i, i))
        self.assertEqual(len(self.sent), cr.MAX_PER_SESSION)

    def test_asks_once_then_sends_queued(self):
        r, s = self.make(None)
        r.exception(boom(KeyError("a")))
        r.exception(boom(TypeError("b")))
        self.assertEqual(len(self.asked), 1)  # второй вопрос не задаём, пока ждём ответа
        self.asked[0](True)
        self.assertIs(s["crash_reports"], True)
        self.assertEqual(len(self.sent), 2)

    def test_declined(self):
        r, s = self.make(None)
        r.exception(boom(KeyError("a")))
        self.asked[0](False)
        self.assertIs(s["crash_reports"], False)
        r.exception(boom(TypeError("b")))
        self.assertEqual((self.sent, len(self.asked)), ([], 1))

    def test_send_failure_is_silent(self):
        s = FakeSettings(crash_reports=True)
        r = cr.Reporter(s, lambda cb: None, mock.Mock(side_effect=OSError("offline")))
        r._send({"x": 1})  # не бросает

    def test_startup_crash_reported_once(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        log = os.path.join(d, "crash.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write(TRACE)
        r, s = self.make(True)
        with mock.patch.object(cr, "CRASH_LOG", log):
            r.check_startup_crash()
            r.seen.clear()
            r.check_startup_crash()
        self.assertEqual([p["kind"] for p in self.sent], ["startup"])
        self.assertTrue(s["crash_reported_at"])


if __name__ == "__main__":
    unittest.main()
