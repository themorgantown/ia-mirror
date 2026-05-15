"""Watcher service tests."""

from datetime import UTC, datetime, timedelta
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

sys.modules["internetarchive"] = MagicMock()
from web.storage import JobStorage
from web.watcher import WatcherService


class TestWatcherService(unittest.TestCase):
    def setUp(self):
        self.mock_storage = MagicMock(spec=JobStorage)
        self.watcher = WatcherService(self.mock_storage)

    @patch("web.watcher.internetarchive")
    def test_check_future_only_first_run(self, mock_ia):
        col = {
            "identifier": "test_col",
            "watch_type": "future",
            "last_checked": None,
            "interval_seconds": 86400,
        }

        self.watcher._check_collection(col)

        self.mock_storage.update_watched_collection_last_checked.assert_called_with("test_col")
        mock_ia.search_items.assert_not_called()
        self.mock_storage.add_job.assert_not_called()

    @patch("web.watcher.internetarchive")
    def test_check_new_items(self, mock_ia):
        last_checked = datetime.now(UTC) - timedelta(days=2)
        col = {
            "identifier": "test_col",
            "watch_type": "future",
            "last_checked": last_checked.isoformat(),
            "interval_seconds": 86400,
        }

        mock_ia.search_items.return_value = [
            {"identifier": "item1", "title": "Item 1", "addeddate": "2023-01-01"},
            {"identifier": "item2", "title": "Item 2", "addeddate": "2023-01-02"},
        ]

        self.watcher._check_collection(col)

        mock_ia.search_items.assert_called()
        self.assertEqual(self.mock_storage.add_job.call_count, 2)
        self.mock_storage.update_watched_collection_last_checked.assert_called_with("test_col")

    @patch("web.watcher.internetarchive")
    def test_not_due(self, mock_ia):
        last_checked = datetime.now(UTC) - timedelta(hours=1)
        col = {
            "identifier": "test_col",
            "watch_type": "future",
            "last_checked": last_checked.isoformat(),
            "interval_seconds": 86400,
        }

        self.watcher._check_collection(col)

        mock_ia.search_items.assert_not_called()


if __name__ == "__main__":
    unittest.main()
