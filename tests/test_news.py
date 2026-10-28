"""Новости на главном экране."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from launcher import news


class NewsTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = mock.patch.object(news, "CACHE", os.path.join(d, "cache", "news.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_clean_drops_bad_items_and_sorts(self):
        items = news._clean([
            {"id": 1, "at": 100, "title": "Старая", "body": "b", "url": ""},
            {"id": 3, "at": 300, "title": "Новая", "body": "b", "url": "https://zelees.link/x"},
            {"id": 2, "title": "Ссылка не https", "url": "javascript:alert(1)"},
            {"id": "4", "title": "id не число"}, {"id": 5, "title": ""}, "мусор", None,
        ])
        self.assertEqual([n["id"] for n in items], [3, 2, 1])
        self.assertEqual(items[1]["url"], "")
        self.assertEqual(news._clean({"not": "a list"}), [])

    def test_fetch_caches(self):
        with mock.patch.object(news.premium, "call", return_value={"news": [{"id": 7, "at": 1, "title": "T"}]}):
            self.assertEqual([n["id"] for n in news.fetch()], [7])
        self.assertEqual([n["id"] for n in news.cached()], [7])  # без сети — из кэша

    def test_cached_empty_without_file(self):
        self.assertEqual(news.cached(), [])

    def test_unread(self):
        items = news._clean([{"id": i, "title": "N%d" % i} for i in (1, 2, 3)])
        self.assertEqual([n["id"] for n in news.unread(items, None)], [3, 2, 1])
        self.assertEqual([n["id"] for n in news.unread(items, 2)], [3])
        self.assertEqual(news.unread(items, 3), [])


if __name__ == "__main__":
    unittest.main()
