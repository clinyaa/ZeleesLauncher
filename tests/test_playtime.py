"""Время в игре: итоги по версиям и серверам и очередь на сайт."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from launcher import playtime


class PlaytimeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "playtime.json")

    def play(self, pt, seconds, version="1.21.4", server=None, account="u1"):
        with mock.patch.object(playtime.time, "time", return_value=1000.0):
            pt.start(version, server, account)
        with mock.patch.object(playtime.time, "time", return_value=1000.0 + seconds):
            return pt.stop()

    def test_totals_and_persistence(self):
        pt = playtime.Playtime(self.path)
        s = self.play(pt, 3600, server=("play.example.com", 25565))
        self.assertEqual((s["seconds"], s["server"]), (3600, "play.example.com"))
        self.play(pt, 600, server=("mc.example.com", 25570))
        pt2 = playtime.Playtime(self.path)
        self.assertEqual(pt2.seconds("versions", "1.21.4"), 4200)
        self.assertEqual(pt2.seconds("servers", "mc.example.com:25570"), 600)
        self.assertEqual(len(pt2.pending("u1")), 2)
        self.assertEqual(pt2.pending("other"), [])

    def test_short_and_long_sessions(self):
        pt = playtime.Playtime(self.path)
        self.assertIsNone(self.play(pt, 5))
        self.assertEqual(self.play(pt, 3 * 86400)["seconds"], playtime.MAX_SESSION)
        self.assertIsNone(pt.stop())  # без start

    def test_sent_removes_only_sent(self):
        pt = playtime.Playtime(self.path)
        self.play(pt, 100)
        first = pt.pending("u1")
        self.play(pt, 200, account="u1")
        pt.data["pending"][-1]["end"] += 1  # другая сессия
        pt.sent(first)
        self.assertEqual([s["seconds"] for s in pt.pending("u1")], [200])

    def test_no_account_not_queued(self):
        pt = playtime.Playtime(self.path)
        self.play(pt, 100, account=None)
        self.assertEqual(pt.data["pending"], [])
        self.assertEqual(pt.seconds("versions", "1.21.4"), 100)

    def test_broken_file(self):
        with open(self.path, "w") as f:
            f.write("[1, 2")
        self.assertEqual(playtime.Playtime(self.path).seconds("versions", "x"), 0)

    def test_fmt_hours(self):
        self.assertEqual([playtime.fmt_hours(s) for s in (5, 600, 3600, 5400, 37800)],
                         ["1 мин", "10 мин", "1 ч", "1,5 ч", "10 ч"])
