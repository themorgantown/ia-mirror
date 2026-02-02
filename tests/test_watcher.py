import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import sys
import os

# Add docker/web to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../docker/web'))

# Mock internetarchive before import
sys.modules['internetarchive'] = MagicMock()
from watcher import WatcherService
from storage import JobStorage

class TestWatcherService(unittest.TestCase):
    def setUp(self):
        self.mock_storage = MagicMock(spec=JobStorage)
        self.watcher = WatcherService(self.mock_storage)

    @patch('watcher.internetarchive')
    def test_check_future_only_first_run(self, mock_ia):
        # future only, first run (last_checked is None)
        col = {
            'identifier': 'test_col',
            'watch_type': 'future',
            'last_checked': None,
            'interval_seconds': 86400
        }
        
        self.watcher._check_collection(col)
        
        # Should update last_checked but NOT queue anything
        self.mock_storage.update_watched_collection_last_checked.assert_called_with('test_col')
        mock_ia.search_items.assert_not_called()
        self.mock_storage.add_job.assert_not_called()

    @patch('watcher.internetarchive')
    def test_check_new_items(self, mock_ia):
        # Standard check with items
        last_checked = datetime.utcnow() - timedelta(days=2)
        col = {
            'identifier': 'test_col',
            'watch_type': 'future',
            'last_checked': last_checked.isoformat(),
            'interval_seconds': 86400
        }
        
        # Mock search results
        mock_ia.search_items.return_value = [
            {'identifier': 'item1', 'title': 'Item 1', 'addeddate': '2023-01-01'},
            {'identifier': 'item2', 'title': 'Item 2', 'addeddate': '2023-01-02'}
        ]
        
        self.watcher._check_collection(col)
        
        # Should search and queue
        mock_ia.search_items.assert_called()
        self.assertEqual(self.mock_storage.add_job.call_count, 2)
        self.mock_storage.update_watched_collection_last_checked.assert_called_with('test_col')

    @patch('watcher.internetarchive')
    def test_not_due(self, mock_ia):
        # Not due yet
        last_checked = datetime.utcnow() - timedelta(hours=1)
        col = {
            'identifier': 'test_col',
            'watch_type': 'future',
            'last_checked': last_checked.isoformat(),
            'interval_seconds': 86400
        }
        
        self.watcher._check_collection(col)
        
        mock_ia.search_items.assert_not_called()

if __name__ == '__main__':
    unittest.main()
