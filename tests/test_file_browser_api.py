"""File browser API tests for the Web UI."""

import os
import shutil
import sys
import tempfile
from unittest.mock import patch

import pytest


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

from web.app import create_app


@pytest.fixture
def file_browser_root():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "subdir"))
    with open(os.path.join(root, "file1.txt"), "w", encoding="utf-8") as handle:
        handle.write("content1")
    with open(os.path.join(root, "subdir", "file2.txt"), "w", encoding="utf-8") as handle:
        handle.write("content2")
    with open(os.path.join(root, "image.jpg"), "wb") as handle:
        handle.write(b"fakeimagecontent")

    try:
        yield root
    finally:
        shutil.rmtree(root)


@pytest.fixture
def client(file_browser_root):
    real_abspath = os.path.abspath

    with patch("web.routes.os.path.abspath") as mock_abspath:
        def side_effect(path):
            if path == "/downloads":
                return file_browser_root
            return real_abspath(path)

        mock_abspath.side_effect = side_effect

        db_path = os.path.join(file_browser_root, "ui.db")
        app, _socketio = create_app({"TESTING": True, "DB_PATH": db_path, "RUNNER_TYPE": "mock"})
        app.config["TESTING"] = True

        try:
            with app.test_client() as test_client:
                yield test_client
        finally:
            app.worker.stop()
            app.watcher.stop()


def test_list_files_root(client):
    response = client.get("/api/files/list")
    assert response.status_code == 200
    assert response.json["path"] == "/"

    items = response.json["items"]
    names = [item["name"] for item in items]
    assert "file1.txt" in names
    assert "subdir" in names
    assert "image.jpg" in names

    subdir = next(item for item in items if item["name"] == "subdir")
    assert subdir["type"] == "directory"


def test_list_files_subdir(client):
    response = client.get("/api/files/list?path=subdir")
    assert response.status_code == 200
    assert len(response.json["items"]) == 1
    item = response.json["items"][0]
    assert item["name"] == "file2.txt"
    assert item["path"] == "subdir/file2.txt"
    assert item["type"] == "file"


def test_download_file(client):
    response = client.get("/api/files/download?path=file1.txt")
    assert response.status_code == 200
    assert response.data == b"content1"


def test_get_file_content(client):
    response = client.get("/api/files/content?path=file1.txt")
    assert response.status_code == 200
    assert response.json["content"] == "content1"


def test_delete_file(client):
    before = client.get("/api/files/list")
    assert "file1.txt" in [item["name"] for item in before.json["items"]]

    response = client.post("/api/files/delete", json={"path": "file1.txt"})
    assert response.status_code == 200

    after = client.get("/api/files/list")
    assert "file1.txt" not in [item["name"] for item in after.json["items"]]


def test_security_traversal(client):
    response = client.get("/api/files/list?path=../")
    assert response.status_code == 403

    response = client.get("/api/files/download?path=../etc/passwd")
    assert response.status_code == 403
