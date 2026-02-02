import pytest
import os
import sys
import shutil
import tempfile
import unittest.mock

# Add docker directory to path so we can import the web package
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, 'docker')
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

from web.app import create_app

@pytest.fixture
def app_with_files():
    """Create Flask app with a temporary directory for file operations."""
    test_dir = tempfile.mkdtemp()
    
    # Create some test files/dirs
    os.makedirs(os.path.join(test_dir, 'subdir'))
    with open(os.path.join(test_dir, 'file1.txt'), 'w') as f:
        f.write('content1')
    with open(os.path.join(test_dir, 'subdir', 'file2.txt'), 'w') as f:
        f.write('content2')
    with open(os.path.join(test_dir, 'image.jpg'), 'wb') as f:
        f.write(b'fakeimagecontent')
        
    # Mock /downloads to point to test_dir
    # We do this by patching or by ensuring the app uses a configured path.
    # The routes.py currently hardcodes /downloads or uses standard logic.
    # However, in the provided routes.py, it uses os.path.abspath('/downloads').
    # We need to mock os.path.abspath or change how the app gets the base dir.
    # For now, let's assuming we can move /downloads. 
    # BUT, since we can't easily change the hardcoded '/downloads' in `routes.py`,
    # we might need to mock os.path operations inside the route.
    # Actually, a better way is to see if we can control the base dir.
    # The current implementation hardcodes base_dir = os.path.abspath('/downloads').
    # We should refactor that to be configurable, but for this test let's mock the internal logic if possible
    # or just use a mock for the whole os module? No that's too much.
    
    # Wait, the code I wrote literally does `base_dir = os.path.abspath('/downloads')`.
    # I should have made that configurable! 
    # But for now, let's use `unittest.mock.patch` to patch `os.path.abspath` to return our test dir when it sees '/downloads'?
    # Or better, patch `os.path.join`? No.
    
    yield test_dir
    
    shutil.rmtree(test_dir)

@pytest.fixture
def client(app_with_files):
    # We need to patch the server to use app_with_files as the root
    real_abspath = os.path.abspath
    with unittest.mock.patch('web.routes.os.path.abspath') as mock_abspath:
        def side_effect(path):
            if path == '/downloads':
                return app_with_files
            return real_abspath(path)
            
        mock_abspath.side_effect = side_effect
        
        # Also need to patch os.path.normpath slightly if it resolves symlinks or similar?
        # Actually proper mocking of abspath should be enough if the code uses it.
        # But wait, `os.path.join(base_dir, path)` -> if base_dir is real path, it works.
        
        db_path = os.path.join(app_with_files, 'ui.db')
        app, _ = create_app({'TESTING': True, 'DB_PATH': db_path})
        with app.test_client() as client:
            yield client

import unittest.mock

def test_list_files_root(client):
    response = client.get('/api/files/list')
    assert response.status_code == 200
    data = response.json
    assert data['path'] == '/'
    items = data['items']
    assert len(items) >= 3 # file1, image, subdir
    
    names = [i['name'] for i in items]
    assert 'file1.txt' in names
    assert 'subdir' in names
    assert 'image.jpg' in names
    
    # Check subdir type
    subdir = next(i for i in items if i['name'] == 'subdir')
    assert subdir['type'] == 'directory'

def test_list_files_subdir(client):
    response = client.get('/api/files/list?path=subdir')
    assert response.status_code == 200
    data = response.json
    items = data['items']
    assert len(items) == 1
    assert items[0]['name'] == 'file2.txt'

def test_download_file(client):
    response = client.get('/api/files/download?path=file1.txt')
    assert response.status_code == 200
    assert response.data == b'content1'

def test_get_file_content(client):
    response = client.get('/api/files/content?path=file1.txt')
    assert response.status_code == 200
    data = response.json
    assert data['content'] == 'content1'

def test_delete_file(client):
    # Verify file exists
    response = client.get('/api/files/list')
    names = [i['name'] for i in response.json['items']]
    assert 'file1.txt' in names
    
    # Delete
    response = client.post('/api/files/delete', json={'path': 'file1.txt'})
    assert response.status_code == 200
    
    # Verify gone
    response = client.get('/api/files/list')
    names = [i['name'] for i in response.json['items']]
    assert 'file1.txt' not in names

def test_security_traversal(client):
    response = client.get('/api/files/list?path=../')
    assert response.status_code == 403
    
    response = client.get('/api/files/download?path=../etc/passwd')
    assert response.status_code == 403
