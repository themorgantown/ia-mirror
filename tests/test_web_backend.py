"""Backend and API tests for the Web UI."""

import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

from web.app import create_app
from web.jobs import RealJobRunner, create_runner
from web.metadata import fetch_metadata
from web.parsing import normalize_identifier, parse_batch_input, validate_destination
from web.queue import QueueWorker
from web.storage import JobStorage


class TestParsing:
    def test_normalize_archive_urls(self):
        identifier, valid = normalize_identifier("https://archive.org/details/test-item")
        assert valid is True
        assert identifier == "test-item"

        identifier, valid = normalize_identifier("http://archive.org/details/my-collection_v2")
        assert valid is True
        assert identifier == "my-collection_v2"

    def test_normalize_raw_identifiers(self):
        identifier, valid = normalize_identifier("item_2024.v1")
        assert valid is True
        assert identifier == "item_2024.v1"

    def test_reject_invalid_identifiers(self):
        identifier, valid = normalize_identifier("item with spaces")
        assert valid is False
        assert identifier == "item with spaces"

        identifier, valid = normalize_identifier("item@#$")
        assert valid is False

    def test_parse_batch_input_filters_comments_and_invalid_rows(self):
        valid, invalid = parse_batch_input(
            """
https://archive.org/details/item1
item2
# ignored
item3
item@#$
""".strip()
        )

        assert valid == ["item1", "item2", "item3"]
        assert invalid == ["item@#$"]


class TestDestinationValidation:
    def test_accepts_download_paths(self):
        assert validate_destination("/data")
        assert validate_destination("/data/subdir")
        assert validate_destination("/data/audio/books")

    def test_rejects_escape_attempts(self):
        assert not validate_destination("/data/../etc")
        assert not validate_destination("/data/../../")
        assert not validate_destination("/etc/passwd")
        assert not validate_destination("/root")
        assert not validate_destination("/downloads_evil")
        assert not validate_destination("/data-backup")
        assert not validate_destination("downloads/item")

    def test_accepts_normalized_download_paths(self):
        assert validate_destination("/downloads")
        assert validate_destination("/downloads/item")


class TestJobStorage:
    @pytest.fixture
    def storage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield JobStorage(os.path.join(tmpdir, "test.db"))

    def test_add_job(self, storage):
        job_id = storage.add_job(
            identifier="test-item",
            input_original="https://archive.org/details/test-item",
            operation="download",
            config={"concurrency": 4},
        )

        job = storage.get_job(job_id)
        assert job_id > 0
        assert job["identifier"] == "test-item"
        assert job["status"] == "queued"

    def test_get_queued_jobs(self, storage):
        storage.add_job("item1", "item1", "download", {})
        storage.add_job("item2", "item2", "download", {})

        jobs = storage.get_queued_jobs()
        assert [job["identifier"] for job in jobs] == ["item1", "item2"]

    def test_reorder_queue_changes_execution_order(self, storage):
        first = storage.add_job("item1", "item1", "download", {})
        second = storage.add_job("item2", "item2", "download", {})
        third = storage.add_job("item3", "item3", "download", {})

        storage.reorder_queue([third, first, second])

        jobs = storage.get_queued_jobs()
        assert [job["identifier"] for job in jobs] == ["item3", "item1", "item2"]

    def test_update_job_status(self, storage):
        job_id = storage.add_job("test", "test", "download", {})
        storage.update_job_status(job_id, "running")
        assert storage.get_job(job_id)["status"] == "running"

        storage.update_job_status(job_id, "completed")
        assert storage.get_job(job_id)["status"] == "completed"

    def test_worker_state(self, storage):
        state = storage.get_worker_state()
        assert state["active_job_id"] is None

        storage.update_worker_state(active_job_id=1, is_processing_queue=True)
        state = storage.get_worker_state()
        assert state["active_job_id"] == 1
        assert state["is_processing_queue"] == 1

        storage.update_worker_state(active_job_id=None, active_pid=None)
        state = storage.get_worker_state()
        assert state["active_job_id"] is None
        assert state["active_pid"] is None
        assert state["is_processing_queue"] == 1

    def test_ui_config(self, storage):
        storage.set_config("theme", "dark")
        assert storage.get_config("theme") == "dark"
        assert storage.get_all_config()["theme"] == "dark"

    def test_append_and_retrieve_job_logs(self, storage):
        job_id = storage.add_job("log-item", "log-item", "download", {})
        storage.append_job_log(job_id, "line one")
        storage.append_job_log(job_id, "line two")
        storage.append_job_log(job_id, "line three")

        logs = storage.get_job_logs(job_id)
        assert len(logs) == 3
        assert [entry["line"] for entry in logs] == ["line one", "line two", "line three"]
        # ts should be monotonically non-decreasing
        assert logs[0]["ts"] <= logs[1]["ts"] <= logs[2]["ts"]

    def test_job_logs_capped_at_500(self, storage):
        job_id = storage.add_job("cap-item", "cap-item", "download", {})
        for i in range(510):
            storage.append_job_log(job_id, f"line {i}")

        logs = storage.get_job_logs(job_id)
        assert len(logs) == 500
        # The retained lines should be the last 500 (lines 10–509)
        assert logs[0]["line"] == "line 10"
        assert logs[-1]["line"] == "line 509"

    def test_recent_downloads_only_returns_completed_jobs(self, storage):
        completed_id = storage.add_job("done-item", "done-item", "download", {"destdir": "/downloads"})
        failed_id = storage.add_job("failed-item", "failed-item", "download", {"destdir": "/downloads"})
        old_id = storage.add_job("old-item", "old-item", "download", {"destdir": "/downloads"})

        storage.update_job_status(completed_id, "completed")
        storage.update_job_status(failed_id, "failed")
        storage.update_job_status(old_id, "completed")

        with storage._get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET completed_at = datetime('now', '-31 days') WHERE id = ?",
                (old_id,),
            )

        rows = storage.get_recent_downloads(days=30, limit=10)
        assert [row["identifier"] for row in rows] == ["done-item"]


class TestJobRunner:
    def test_mock_runner(self):
        runner = create_runner("mock", 1, "test-item", "/tmp", {"files": 5})

        logs = []
        exit_code = runner.run(logs.append, lambda _progress: None)

        assert exit_code == 0
        assert logs
        assert any("Download complete" in line for line in logs)

    def test_verify_operation_adds_checksum_flag(self):
        runner = RealJobRunner(1, "test-item", "/tmp", {}, operation="verify")

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.stdout = []
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            runner.run(lambda _line: None, lambda _progress: None)

        command = mock_popen.call_args.args[0]
        assert "--checksum" in command
        assert "--json-output" in command


class TestQueueWorker:
    @pytest.fixture
    def storage_and_worker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = JobStorage(os.path.join(tmpdir, "test.db"))
            worker = QueueWorker(storage, runner_type="mock")
            try:
                yield storage, worker
            finally:
                worker.stop()

    def test_orphaned_running_jobs_reset_to_failed_on_startup(self):
        """Jobs left in 'running' state (e.g. after a crash) must be marked failed when a new worker starts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = JobStorage(os.path.join(tmpdir, "orphan.db"))
            job_id = storage.add_job("orphan-item", "orphan-item", "download", {})
            # Simulate the job being mid-run when the previous worker died
            storage.update_job_status(job_id, "running")
            assert storage.get_job(job_id)["status"] == "running"

            # Creating a new QueueWorker must reset that orphaned job to failed
            worker = QueueWorker(storage, runner_type="mock")
            try:
                job = storage.get_job(job_id)
                assert job["status"] == "failed"
                assert job["error_message"] == "Interrupted: worker restarted"
            finally:
                worker.stop()

    def test_job_callbacks(self, storage_and_worker):
        storage, worker = storage_and_worker
        events = []

        worker.add_callback(
            "on_job_start",
            lambda job_id, identifier: events.append(("start", job_id, identifier)),
        )
        worker.add_callback(
            "on_job_complete",
            lambda job_id, status, code: events.append(("complete", job_id, status, code)),
        )

        storage.add_job("test-item", "test-item", "download", {"destdir": "/tmp"})
        storage.update_worker_state(is_processing_queue=True)
        worker.start()

        for _ in range(60):
            time.sleep(0.25)
            if len(events) >= 2:
                break

        assert any(event[0] == "start" for event in events)
        assert any(event[0] == "complete" for event in events)

    def test_job_is_marked_running_before_runner_executes(self, storage_and_worker):
        storage, worker = storage_and_worker
        job_id = storage.add_job("test-item", "test-item", "download", {"destdir": "/tmp"})
        observed = {}

        class InspectRunner:
            def run(self, _on_log, _on_progress):
                observed["status_during_run"] = storage.get_job(job_id)["status"]
                observed["queued_during_run"] = [job["identifier"] for job in storage.get_queued_jobs()]
                return 0

            def stop(self):
                pass

        with patch("web.queue.create_runner", return_value=InspectRunner()):
            worker._run_job(storage.get_job(job_id))

        assert observed["status_during_run"] == "running"
        assert observed["queued_during_run"] == []
        assert storage.get_job(job_id)["status"] == "completed"


def test_create_app_generates_secret_when_missing(monkeypatch):
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        app, _socketio = create_app({"DB_PATH": os.path.join(tmpdir, "test.db"), "RUNNER_TYPE": "mock"})
        assert app.config["SECRET_KEY"]
        assert app.config["SECRET_KEY"] != "dev-secret-key"
        app.worker.stop()
        app.watcher.stop()


def test_create_app_does_not_enable_wildcard_cors_by_default(monkeypatch):
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with tempfile.TemporaryDirectory() as tmpdir:
        app, _socketio = create_app({"DB_PATH": os.path.join(tmpdir, "test.db"), "RUNNER_TYPE": "mock"})
        try:
            response = app.test_client().get("/api/config", headers={"Origin": "https://evil.example"})
            assert response.headers.get("Access-Control-Allow-Origin") is None
        finally:
            app.worker.stop()
            app.watcher.stop()


MOCK_METADATA_RESPONSE = {
    "created": 1766854020,
    "d1": "ia903401.us.archive.org",
    "dir": "/9/items/listofearlyameri00fren",
    "files": [
        {"name": "__ia_thumb.jpg", "source": "original", "format": "Item Tile"},
        {"name": "cover.jpg", "format": "Item Tile"},
    ],
    "metadata": {
        "title": "A list of early American silversmiths and their marks",
        "creator": ["French, Hollis"],
        "identifier": "listofearlyameri00fren",
    },
}


def test_fetch_metadata_success():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = MOCK_METADATA_RESPONSE
        mock_get.return_value = mock_response

        metadata = fetch_metadata("listofearlyameri00fren")

    assert metadata["title"] == "A list of early American silversmiths and their marks"
    assert metadata["creator"] == "French, Hollis"
    assert (
        metadata["thumbnail_url"]
        == "https://ia903401.us.archive.org/9/items/listofearlyameri00fren/__ia_thumb.jpg"
    )


def test_fetch_metadata_default():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        metadata = fetch_metadata("invalid-id")

    assert metadata["title"] == "invalid-id"
    assert metadata["creator"] == ""
    assert metadata["thumbnail_url"] is None


@pytest.fixture
def app():
    fd, db_path = tempfile.mkstemp()
    os.close(fd)
    os.environ["WEB_DB_PATH"] = db_path

    app, _socketio = create_app({"DB_PATH": db_path, "RUNNER_TYPE": "mock"})
    app.config["TESTING"] = True

    try:
        yield app
    finally:
        app.worker.stop()
        app.watcher.stop()
        if os.path.exists(db_path):
            os.unlink(db_path)
        os.environ.pop("WEB_DB_PATH", None)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def storage(app):
    return app.storage


def test_queue_add_with_metadata(client, storage):
    with patch("web.routes.fetch_metadata") as mock_fetch:
        mock_fetch.return_value = {
            "title": "Test Title",
            "creator": "Test Creator",
            "thumbnail_url": "http://example.com/thumb.jpg",
        }

        response = client.post("/api/queue/add", json={"text": "test-identifier"})

    assert response.status_code == 200
    assert len(response.json["job_ids"]) == 1

    jobs = storage.get_queued_jobs()
    assert len(jobs) == 1
    assert jobs[0]["identifier"] == "test-identifier"
    assert jobs[0]["title"] == "Test Title"
    assert jobs[0]["creator"] == "Test Creator"
    assert jobs[0]["thumbnail_url"] == "http://example.com/thumb.jpg"

    jobs_response = client.get("/api/jobs")
    assert jobs_response.json["jobs"][0]["title"] == "Test Title"


def test_get_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    assert "destination" in response.json
    assert "operation" in response.json


def test_post_config(client, storage):
    response = client.post("/api/config", json={"theme": "dark", "concurrency": 8})
    assert response.status_code == 200
    assert storage.get_config("theme") == "dark"
    assert storage.get_config("concurrency") == "8"


def test_get_config_includes_host_download_dir_from_env(client, monkeypatch):
    """DOWNLOAD_DIR env var is returned as host_download_dir in GET /api/config."""
    monkeypatch.setenv("DOWNLOAD_DIR", "/some/host/path")
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json["host_download_dir"] == "/some/host/path"


def test_get_config_host_download_dir_defaults_to_empty(client, monkeypatch):
    """Missing DOWNLOAD_DIR returns empty string, not a KeyError."""
    monkeypatch.delenv("DOWNLOAD_DIR", raising=False)
    response = client.get("/api/config")
    assert response.status_code == 200
    assert "host_download_dir" in response.json
    assert response.json["host_download_dir"] == ""


def test_post_host_download_dir_stores_in_sqlite(client, storage):
    """POSTing host_download_dir saves to config storage."""
    response = client.post("/api/config", json={"host_download_dir": "/desired/path"})
    assert response.status_code == 200
    assert storage.get_config("host_download_dir") == "/desired/path"


def test_get_config_host_download_dir_env_takes_precedence(client, monkeypatch):
    """GET /api/config always returns env var value for host_download_dir, not SQLite."""
    # Save a different desired path to SQLite
    client.post("/api/config", json={"host_download_dir": "/sqlite/path"})
    # Set a different value in the env
    monkeypatch.setenv("DOWNLOAD_DIR", "/env/path")
    response = client.get("/api/config")
    assert response.status_code == 200
    # Env var must win — SQLite stores desired-next-path, env var is live mount
    assert response.json["host_download_dir"] == "/env/path"


def test_get_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert "queue_length" in response.json
    assert "system" in response.json
    assert "has_credentials" in response.json["system"]


def test_get_jobs(client, storage):
    storage.add_job("test-item", "test-item", "download", {})
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert len(response.json["jobs"]) >= 1


def test_get_recent_jobs_uses_30_day_completed_window(client, storage):
    completed_id = storage.add_job("recent-item", "recent-item", "download", {"destdir": "/downloads"})
    failed_id = storage.add_job("failed-item", "failed-item", "download", {"destdir": "/downloads"})

    storage.update_job_status(completed_id, "completed")
    storage.update_job_progress(completed_id, {"bytes_total": 2048})
    storage.update_job_status(failed_id, "failed")

    response = client.get("/api/jobs/recent")

    assert response.status_code == 200
    assert response.json["days"] == 30
    assert [job["identifier"] for job in response.json["jobs"]] == ["recent-item"]
    assert response.json["jobs"][0]["resolved_path"] == "/downloads/recent-item"
    assert response.json["jobs"][0]["bytes_total"] == 2048


def test_get_job_by_id(client, storage):
    job_id = storage.add_job("test-item", "test-item", "download", {})
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json["identifier"] == "test-item"


def test_get_job_log(client, storage):
    job_id = storage.add_job("test-item", "test-item", "download", {})
    response = client.get(f"/api/jobs/{job_id}/log")
    assert response.status_code in (200, 404)


def test_get_job_logs_endpoint(client, storage):
    job_id = storage.add_job("log-item", "log-item", "download", {})
    storage.append_job_log(job_id, "hello world")
    storage.append_job_log(job_id, "second line")

    response = client.get(f"/api/jobs/{job_id}/logs")
    assert response.status_code == 200
    logs = response.json["logs"]
    assert len(logs) == 2
    assert logs[0]["line"] == "hello world"
    assert logs[1]["line"] == "second line"


def test_get_job_logs_endpoint_404(client):
    response = client.get("/api/jobs/99999/logs")
    assert response.status_code == 404


def test_queue_add_invalid(client):
    response = client.post("/api/queue/add", json={"text": ""})
    assert response.status_code in (200, 400)
    if response.status_code == 400:
        assert "error" in response.json
    else:
        assert response.json["valid_count"] == 0


def test_queue_delete_nonexistent(client):
    response = client.delete("/api/queue/99999")
    assert response.status_code in (200, 404)


def test_file_list_path_traversal(client):
    response = client.get("/api/files/list?path=/downloads/../../etc")
    assert response.status_code == 403


def test_file_download_path_traversal(client):
    response = client.get("/api/files/download?path=/downloads/../../etc/passwd")
    assert response.status_code == 403


def test_file_delete_path_traversal(client):
    response = client.post("/api/files/delete", json={"path": "/downloads/../../etc/passwd"})
    assert response.status_code == 403


def test_file_content_path_traversal(client):
    response = client.get("/api/files/content?path=/downloads/../../etc/passwd")
    assert response.status_code == 403


def test_destinations_validate_valid(client):
    response = client.post("/api/destinations/validate", json={"path": "/downloads"})
    assert response.status_code == 200
    assert response.json["valid"] is True


def test_destinations_validate_invalid(client):
    response = client.post("/api/destinations/validate", json={"path": "/etc"})
    if response.status_code == 200:
        assert response.json["valid"] is False
    else:
        assert response.status_code == 400
