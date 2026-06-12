import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

import fetcher


def test_get_file_list_filters_by_source():
    manifest = {
        "item_meta.xml": {"source": "metadata"},
        "item_files.xml": {"source": "metadata"},
        "book.pdf": {"source": "original"},
    }

    assert fetcher.get_file_list(manifest, source="metadata") == ["item_meta.xml", "item_files.xml"]


def test_resolve_download_path_uses_item_subdirectories_for_collections():
    dest = Path("/downloads/my-collection")

    first = fetcher.resolve_download_path(
        dest,
        "item-one",
        "shared/name.xml",
        root_identifier="my-collection",
        collection_layout=True,
    )
    second = fetcher.resolve_download_path(
        dest,
        "item-two",
        "shared/name.xml",
        root_identifier="my-collection",
        collection_layout=True,
    )

    assert first == Path("/downloads/my-collection/item-one/shared/name.xml")
    assert second == Path("/downloads/my-collection/item-two/shared/name.xml")
    assert first != second


def test_resolve_download_path_flattens_when_no_directories():
    path = fetcher.resolve_download_path(
        Path("/downloads/item"),
        "item",
        "nested/file.txt",
        root_identifier="item",
        no_directories=True,
    )

    assert path == Path("/downloads/item/file.txt")


def test_resolve_download_path_rejects_remote_traversal():
    with pytest.raises(ValueError):
        fetcher.resolve_download_path(Path("/downloads/item"), "item", "../secret.txt")

    with pytest.raises(ValueError):
        fetcher.resolve_download_path(Path("/downloads/item"), "../bad-item", "file.txt", root_identifier="collection", collection_layout=True)


def test_lock_is_stale_keeps_active_same_host_lock(monkeypatch):
    monkeypatch.setattr(fetcher, "_pid_is_running", lambda _pid: True)

    assert fetcher.lock_is_stale({"pid": 123, "host": "container-a"}, now=100, current_host="container-a") is False


def test_lock_is_stale_keeps_recent_other_host_lock(monkeypatch):
    monkeypatch.setenv("IA_LOCK_STALE_SECONDS", "60")

    assert fetcher.lock_is_stale({"pid": 123, "host": "container-b", "started": 75}, now=100, current_host="container-a") is False
    assert fetcher.lock_is_stale({"pid": 123, "host": "container-b", "started": 1}, now=100, current_host="container-a") is True


def test_batch_mode_inherits_env_dry_run_with_cli_args(monkeypatch, tmp_path):
    batch_file = tmp_path / "batch.csv"
    batch_file.write_text("source,destdir\nitem-one,/downloads\n")

    captured = {}

    def fake_run(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setenv("IA_DRY_RUN", "1")
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py",
        "mirror",
        "--use-batch-source",
        "--batch-source-path",
        str(batch_file),
    ])
    fetcher._shutdown_event.clear()

    with patch("fetcher.subprocess.run", side_effect=fake_run):
        exit_code = fetcher.main()

    assert exit_code == 0
    assert "--dry-run" in captured["cmd"]
    assert captured["cmd"][0] == sys.executable
    assert captured["env"]["IA_IS_CHILD"] == "1"
