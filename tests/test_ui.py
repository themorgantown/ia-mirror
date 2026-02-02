"""Consolidated Unit Tests for ia-mirror Web UI."""

import sys
import os
import unittest.mock
import pytest
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add docker directory to path so we can import the web package
# Assuming this file is in <repo>/tests/test_ui.py
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, 'docker')
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

# Import web modules
from web.parsing import normalize_identifier, parse_batch_input, validate_destination
from web.storage import JobStorage
from web.jobs import MockJobRunner, create_runner
from web.queue import QueueWorker
from web.metadata import fetch_metadata
from web.app import create_app

# --- Backend Tests ---

class TestParsing:
    """Test URL/identifier parsing."""
    
    def test_normalize_url(self):
        """Test normalizing archive.org URLs."""
        identifier, valid = normalize_identifier('https://archive.org/details/test-item')
        assert valid
        assert identifier == 'test-item'
        
        identifier, valid = normalize_identifier('http://archive.org/details/my-collection_v2')
        assert valid
        assert identifier == 'my-collection_v2'
    
    def test_normalize_identifier(self):
        """Test normalizing raw identifiers."""
        identifier, valid = normalize_identifier('test-item')
        assert valid
        assert identifier == 'test-item'
        
        identifier, valid = normalize_identifier('item_2024.v1')
        assert valid
        assert identifier == 'item_2024.v1'
    
    def test_reject_invalid_identifier(self):
        """Test rejecting invalid identifiers."""
        identifier, valid = normalize_identifier('item with spaces')
        assert not valid
        
        identifier, valid = normalize_identifier('item@#$')
        assert not valid
    
    def test_reject_comments_and_empty(self):
        """Test rejecting comments and empty lines."""
        identifier, valid = normalize_identifier('# This is a comment')
        assert not valid
        
        identifier, valid = normalize_identifier('')
        assert not valid
        
        identifier, valid = normalize_identifier('   ')
        assert not valid
    
    def test_parse_batch_input(self):
        """Test parsing batch input with mixed items."""
    def test_parse_batch_input(self):
        """Test parsing batch input with mixed items."""
        text = """
https://archive.org/details/item1
item2
# This is a comment
item3
item@#$
"""
        valid, invalid = parse_batch_input(text)
        assert 'item1' in valid
        assert 'item2' in valid
        assert 'item3' in valid
        assert len(valid) == 3
        assert 'item@#$' in invalid


class TestDestinationValidation:
    """Test destination path validation."""
    
    def test_valid_data_path(self):
        """Test /data is valid."""
        assert validate_destination('/data')
    
    def test_valid_subdirectory(self):
        """Test /data/subdir is valid."""
        assert validate_destination('/data/subdir')
        assert validate_destination('/data/audio/books')
    
    def test_reject_escape_attempts(self):
        """Test rejecting path escape attempts."""
        assert not validate_destination('/data/../etc')
        assert not validate_destination('/data/../../')
        assert not validate_destination('/etc/passwd')
        assert not validate_destination('/root')


class TestJobStorage:
    """Test SQLite job storage."""
    
    @pytest.fixture
    def storage(self):
        """Create temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            storage = JobStorage(db_path)
            yield storage
    
    def test_add_job(self, storage):
        """Test adding a job."""
        job_id = storage.add_job(
            identifier='test-item',
            input_original='https://archive.org/details/test-item',
            operation='download',
            config={'concurrency': 4}
        )
        assert job_id > 0
        
        job = storage.get_job(job_id)
        assert job['identifier'] == 'test-item'
        assert job['status'] == 'queued'
    
    def test_get_queued_jobs(self, storage):
        """Test retrieving queued jobs."""
        storage.add_job('item1', 'item1', 'download', {})
        storage.add_job('item2', 'item2', 'download', {})
        
        jobs = storage.get_queued_jobs()
        assert len(jobs) == 2
        assert jobs[0]['identifier'] == 'item1'
        assert jobs[1]['identifier'] == 'item2'
    
    def test_update_job_status(self, storage):
        """Test updating job status."""
        job_id = storage.add_job('test', 'test', 'download', {})
        
        storage.update_job_status(job_id, 'running')
        job = storage.get_job(job_id)
        assert job['status'] == 'running'
        
        storage.update_job_status(job_id, 'completed')
        job = storage.get_job(job_id)
        assert job['status'] == 'completed'
    
    def test_worker_state(self, storage):
        """Test worker state management."""
        state = storage.get_worker_state()
        assert state['active_job_id'] is None
        
        storage.update_worker_state(active_job_id=1, is_processing_queue=True)
        state = storage.get_worker_state()
        assert state['active_job_id'] == 1
        assert state['is_processing_queue'] == True
    
    def test_ui_config(self, storage):
        """Test UI config storage."""
        storage.set_config('theme', 'dark')
        assert storage.get_config('theme') == 'dark'
        
        config = storage.get_all_config()
        assert config['theme'] == 'dark'


class TestJobRunner:
    """Test job execution."""
    
    def test_mock_runner(self):
        """Test mock runner execution."""
        runner = create_runner('mock', 1, 'test-item', '/tmp', {'files': 5})
        
        logs = []
        def on_log(msg):
            logs.append(msg)
            
        def on_progress(p):
            pass
            
        # Run
        exit_code = runner.run(on_log, on_progress)
        assert exit_code == 0
        assert len(logs) > 0
        assert any('Download complete' in l for l in logs)

    def test_verify_operation(self):
        """Test that verify operation adds checksum flag."""
        # Use RealJobRunner but mock subprocess to avoid actual execution
        from web.jobs import RealJobRunner
        import subprocess
        
        runner = RealJobRunner(1, 'test-item', '/tmp', {}, operation='verify')
        
        with patch('subprocess.Popen') as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = []
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process
            
            runner.run(lambda x: None, lambda x: None)
            
            # Check args
            args, _ = mock_popen.call_args
            cmd = args[0]
            assert '--checksum' in cmd
            assert '--json-output' in cmd


class TestQueueWorker:
    """Test queue worker."""
    
    @pytest.fixture
    def storage_and_worker(self):
        """Create storage and worker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, 'test.db')
            storage = JobStorage(db_path)
            worker = QueueWorker(storage, runner_type='mock')
            yield storage, worker
            worker.stop()
    
    def test_job_callbacks(self, storage_and_worker):
        """Test job callbacks are fired."""
        storage, worker = storage_and_worker
        
        events = []
        
        worker.add_callback('on_job_start', lambda job_id, identifier: events.append(('start', job_id, identifier)))
        worker.add_callback('on_job_complete', lambda job_id, status, code: events.append(('complete', job_id, status)))
        
        # Add a job
        job_id = storage.add_job('test-item', 'test-item', 'download', {'destination': '/data'})
        
        # Enable processing
        storage.update_worker_state(is_processing_queue=True)
        
        # Start worker
        worker.start()
        
        # Wait for job to complete
        import time
        for _ in range(10):
            time.sleep(0.5)
            if len(events) >= 2:
                break
        
        worker.stop()
        
        # Check events
        assert any(e[0] == 'start' for e in events)
        assert any(e[0] == 'complete' for e in events)


# --- Metadata Tests ---

MOCK_METADATA_RESPONSE = {
    "created": 1766854020,
    "d1": "ia903401.us.archive.org",
    "dir": "/9/items/listofearlyameri00fren",
    "files": [
        {"name": "__ia_thumb.jpg", "source": "original", "format": "Item Tile"},
        {"name": "cover.jpg", "format": "Item Tile"}
    ],
    "metadata": {
        "title": "A list of early American silversmiths and their marks",
        "creator": ["French, Hollis"],
        "identifier": "listofearlyameri00fren"
    }
}

def test_fetch_metadata_success():
    """Test successful metadata fetching."""
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_METADATA_RESPONSE
        mock_get.return_value = mock_response
        
        meta = fetch_metadata('listofearlyameri00fren')
        
        assert meta['title'] == "A list of early American silversmiths and their marks"
        assert meta['creator'] == "French, Hollis"
        assert meta['thumbnail_url'] == "https://ia903401.us.archive.org/9/items/listofearlyameri00fren/__ia_thumb.jpg"


def test_fetch_metadata_default():
    """Test metadata fetching with failure."""
    with patch('requests.get') as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        meta = fetch_metadata('invalid-id')
        
        assert meta['title'] == 'invalid-id'
        assert meta['creator'] == ''
        assert meta['thumbnail_url'] is None


# --- App Integration Tests ---

@pytest.fixture
def app():
    """Create Flask app for testing."""
    import tempfile
    
    # Create temp db file
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)
    
    os.environ['WEB_DB_PATH'] = db_path
    app, _ = create_app({'DB_PATH': db_path, 'RUNNER_TYPE': 'mock'})
    app.config['TESTING'] = True
    
    yield app
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)

@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()

@pytest.fixture
def storage(app):
    """Get storage instance."""
    return app.storage

def test_queue_add_with_metadata(client, storage):
    """Test adding item to queue adds metadata."""
    with patch('web.routes.fetch_metadata') as mock_fetch:
        mock_fetch.return_value = {
            'title': 'Test Title',
            'creator': 'Test Creator',
            'thumbnail_url': 'http://example.com/thumb.jpg'
        }
        
        response = client.post('/api/queue/add', json={
            'text': 'test-identifier'
        })
        
        assert response.status_code == 200
        data = response.json
        assert len(data['job_ids']) == 1
        
        # Verify db
        jobs = storage.get_queued_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job['identifier'] == 'test-identifier'
        assert job['title'] == 'Test Title'
        assert job['creator'] == 'Test Creator'
        assert job['thumbnail_url'] == 'http://example.com/thumb.jpg'
        
        # Verify job list API includes metadata
        response = client.get('/api/jobs')
        data = response.json
        job_api = data['jobs'][0]
        assert job_api['title'] == 'Test Title'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
