import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_lock_is_stale_uses_heartbeat_for_other_host_lock(monkeypatch):
    """A killed container's lock frees up once its heartbeat stops, not after 24h."""
    monkeypatch.setenv("IA_LOCK_HEARTBEAT_STALE_SECONDS", "120")
    live = {"pid": 7, "host": "container-b", "started": 0, "heartbeat": 950}
    dead = {"pid": 7, "host": "container-b", "started": 900, "heartbeat": 900}

    assert fetcher.lock_is_stale(live, now=1000, current_host="container-a") is False
    assert fetcher.lock_is_stale(dead, now=1100, current_host="container-a") is True


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


class FakeResponse:
    """Minimal stand-in for the streamed requests.Response the library returns."""

    def __init__(self, body, status_code=200, fail_after=None):
        self.body = body
        self.status_code = status_code
        self.fail_after = fail_after

    def iter_content(self, chunk_size=1):
        if self.fail_after is None:
            yield self.body
            return
        yield self.body[:self.fail_after]
        raise ConnectionError("Connection broken: IncompleteRead")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _download(dest, filename, size, fake_download, retries=2, verify_mode="size"):
    mock_ia_file = MagicMock()
    mock_ia_file.download.side_effect = fake_download
    mock_item = MagicMock()
    mock_item.get_file.return_value = mock_ia_file
    fetcher._shutdown_event.clear()
    with patch("fetcher.internetarchive.get_item", return_value=mock_item):
        _, ok = fetcher.download_single_file(
            ia="ia",
            identifier="test-item",
            filename=filename,
            destdir=dest,
            manifest_entry={"size": size},
            retries=retries,
            progress_timeout=30,
            max_timeout=60,
            verify_mode=verify_mode,
            idx=0,
            total=1,
            max_mbps=0,
            bucket=None,
        )
    return ok, mock_ia_file.download


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setenv("IA_RETRY_BACKOFF_BASE", "0")
    monkeypatch.delenv("IA_DOWNLOAD_RETRIES", raising=False)


def test_download_single_file_retries_on_failure(fast_retries, tmp_path):
    """download_single_file retries a failed request and succeeds on the 3rd attempt."""
    calls = {"n": 0}

    def fake_download(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("boom")
        return FakeResponse(b"data")

    ok, dl = _download(tmp_path, "test.txt", 4, fake_download)

    assert ok is True, "Expected success after retries"
    assert dl.call_count == 3
    assert (tmp_path / "test.txt").read_bytes() == b"data"


def test_download_single_file_subdir_does_not_touch_cwd(fast_retries, monkeypatch, tmp_path):
    """Files inside item subfolders must not make the library create dirs in cwd.

    Given a fileobj or file_path, internetarchive's File.download() runs
    os.makedirs(dirname(name)) relative to cwd (/app in the image, not
    writable), so "Mozart/CD01.flac" failed with EACCES.
    """
    name = "Mozart/CD01.flac"

    def fake_download(**kwargs):
        if not kwargs.get("return_responses"):
            os.makedirs(os.path.join(kwargs.get("destdir") or "", os.path.dirname(name)), exist_ok=True)
        return FakeResponse(b"data")

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    ok, _ = _download(tmp_path / "dest", name, 4, fake_download)

    assert ok is True
    assert (tmp_path / "dest" / name).read_bytes() == b"data"
    assert list(cwd.iterdir()) == [], "library side-effect dirs leaked into cwd"


def test_download_single_file_resumes_after_dropped_connection(fast_retries, tmp_path):
    """A dropped connection resumes from the bytes already on disk, not from zero."""
    payload = b"0123456789"
    ranges = []

    def fake_download(**kwargs):
        rng = (kwargs.get("headers") or {}).get("Range")
        ranges.append(rng)
        if rng is None:
            return FakeResponse(payload, fail_after=3)
        start = int(rng.split("=")[1].rstrip("-"))
        return FakeResponse(payload[start:], status_code=206)

    ok, _ = _download(tmp_path, "big.zip", len(payload), fake_download)

    assert ok is True
    assert ranges == [None, "bytes=3-"]
    assert (tmp_path / "big.zip").read_bytes() == payload
    assert not (tmp_path / "big.zip.part").exists()


def test_download_single_file_restarts_when_range_ignored(fast_retries, tmp_path):
    """A 200 reply to a Range request is the whole file: overwrite, never append."""
    payload = b"0123456789"
    calls = {"n": 0}

    def fake_download(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse(payload, fail_after=4)
        return FakeResponse(payload, status_code=200)

    ok, _ = _download(tmp_path, "big.zip", len(payload), fake_download)

    assert ok is True
    assert (tmp_path / "big.zip").read_bytes() == payload


def test_download_single_file_resumes_part_from_previous_run(fast_retries, tmp_path):
    """A .part left by an interrupted run is continued on the next run."""
    payload = b"0123456789"
    (tmp_path / "big.zip.part").write_bytes(payload[:6])

    def fake_download(**kwargs):
        assert kwargs["headers"] == {"Range": "bytes=6-"}
        return FakeResponse(payload[6:], status_code=206)

    ok, _ = _download(tmp_path, "big.zip", len(payload), fake_download)

    assert ok is True
    assert (tmp_path / "big.zip").read_bytes() == payload


def test_signal_handler_interrupts_active_downloads():
    """SIGTERM must unblock a stalled stream instead of waiting for the read timeout."""
    resp = MagicMock()
    fetcher._active_responses.add(resp)
    try:
        fetcher.signal_handler(15, None)
        resp.raw.shutdown.assert_called_once()
    finally:
        fetcher._active_responses.discard(resp)
        fetcher._shutdown_event.clear()


def test_download_single_file_rejects_wrong_size(fast_retries, tmp_path):
    """A body shorter than the item metadata says is a failure, never the final file."""

    def fake_download(**kwargs):
        return FakeResponse(b"short")

    ok, _ = _download(tmp_path, "big.zip", 10, fake_download, retries=1)

    assert ok is False
    assert not (tmp_path / "big.zip").exists()


def _parse_mirror(argv):
    """Parse argv with the real parser and return (args, dests supplied on the CLI)."""
    parser, mirror_parser = fetcher.build_parser()
    return parser.parse_args(argv), fetcher.cli_supplied_dests(mirror_parser, argv)


def test_env_flag_does_not_override_explicit_cli_choice(monkeypatch):
    """An env switch must not overrule an option the caller stated outright.

    Env vars are only defaults. IA_GLOB previously fought with an explicit --glob,
    and the boolean switches were applied unconditionally, so a caller had no way to
    opt out of a mode a stale env var had turned on.
    """
    monkeypatch.setenv("IA_GLOB", "*.zip")
    monkeypatch.setenv("IA_CONCURRENCY", "8")

    args, supplied = _parse_mirror(["mirror", "an-item", "--glob", "*", "--concurrency", "2"])
    fetcher.apply_env_defaults(args, supplied)

    assert args.glob == ["*"], "explicit --glob must win over IA_GLOB"
    assert args.concurrency == 2, "explicit --concurrency must win over IA_CONCURRENCY"


def test_env_flag_still_seeds_options_the_caller_omitted(monkeypatch):
    """Env defaults stay useful for CLI mode: they fill in what was not passed."""
    monkeypatch.setenv("IA_COLLECTION", "1")
    monkeypatch.setenv("IA_CONCURRENCY", "8")
    monkeypatch.setenv("IA_GLOB", "*.zip")

    args, supplied = _parse_mirror(["mirror", "an-item"])
    fetcher.apply_env_defaults(args, supplied)

    assert args.collection is True
    assert args.concurrency == 8
    assert args.glob == ["*.zip"]


def test_explicit_bool_flag_is_not_reapplied_from_env(monkeypatch):
    """--resumefolders passed explicitly stays on; the env var is not what decided it."""
    monkeypatch.setenv("IA_RESUMEFOLDERS", "1")

    args, supplied = _parse_mirror(["mirror", "an-item", "--resumefolders"])
    fetcher.apply_env_defaults(args, supplied)

    assert "resumefolders" in supplied
    assert args.resumefolders is True


def test_cli_supplied_dests_maps_real_option_strings():
    """Guards the option->dest mapping the precedence rules depend on."""
    _, mirror_parser = fetcher.build_parser()

    supplied = fetcher.cli_supplied_dests(
        mirror_parser,
        ["mirror", "an-item", "--glob", "*", "-j", "2", "--dry-run", "--format=mp3"],
    )

    assert {"glob", "concurrency", "dry_run", "formats"} <= supplied
    assert "collection" not in supplied


def test_get_collection_items_narrows_search_with_query(monkeypatch):
    """--query is ANDed onto collection:"<id>", parenthesized so its ORs stay inside it."""
    captured = {}

    def fake_run_cmd(cmd):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="item-a\nitem-b\n")

    monkeypatch.setattr(fetcher, "run_cmd", fake_run_cmd)

    items = fetcher.get_collection_items("ia", "hvconnectdrone", '(subject:"A" OR subject:"B")')

    assert items == ["item-a", "item-b"]
    assert captured["cmd"] == [
        "ia", "search",
        'collection:"hvconnectdrone" AND ((subject:"A" OR subject:"B"))',
        "--itemlist",
    ]


def test_query_requires_collection_mode(monkeypatch, tmp_path):
    """A search filter only means something for a collection; fail instead of ignoring it."""
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.delenv("IA_COLLECTION", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "some-item", "--query", 'subject:"x"', "--destdir", str(tmp_path),
    ])

    with pytest.raises(SystemExit) as exc:
        fetcher.main()
    assert exc.value.code == 2


def test_filtered_collection_with_no_matches_fails_instead_of_falling_back(monkeypatch, tmp_path):
    """An empty unfiltered collection falls back to downloading the item itself, but a
    filter that matches nothing must not: that would silently mirror the wrong thing."""
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "hvconnectdrone", "--collection", "--query", 'subject:"Nowhere"',
        "--destdir", str(tmp_path), "--no-lock",
    ])
    monkeypatch.setattr(fetcher, "find_ia_executable", lambda custom=None: "ia")
    monkeypatch.setattr(fetcher, "get_collection_items", lambda ia, cid, query="": [])
    get_manifest = MagicMock()
    monkeypatch.setattr(fetcher, "get_manifest", get_manifest)
    fetcher._shutdown_event.clear()

    assert fetcher.main() == 1
    get_manifest.assert_not_called()


def test_collection_metadata_phase_emits_progress_events(monkeypatch, tmp_path, capsys):
    """The Web UI's progress console relies on these events while manifests download."""
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "coll", "--collection", "--json-output", "--dry-run",
        "--destdir", str(tmp_path), "--no-lock",
    ])
    monkeypatch.setattr(fetcher, "find_ia_executable", lambda custom=None: "ia")
    monkeypatch.setattr(fetcher, "get_collection_items", lambda ia, cid, query="": ["item-a", "item-b"])
    monkeypatch.setattr(fetcher, "get_manifest", lambda item_id, dest, force_update=False, primary=True: {})
    fetcher._shutdown_event.clear()

    fetcher.main()

    import json
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    meta = [e for e in events if e["type"] == "metadata_progress"]
    assert {"type": "phase", "phase": "searching"}.items() <= next(e for e in events if e["type"] == "phase").items()
    # One event per fetched item; manifests arrive in completion order, so only the
    # running count is ordered.
    assert [(e["items_done"], e["items_total"]) for e in meta] == [(1, 2), (2, 2)]
    assert sorted(e["item"] for e in meta) == ["item-a", "item-b"]


def test_init_logging_drops_handlers_installed_before_it(tmp_path):
    """A logging call before init_logging auto-installs a bare stderr handler;
    keeping it printed every line twice ("INFO:root:..."), doubling the UI log."""
    import logging

    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        early = logging.StreamHandler()
        root.addHandler(early)

        fetcher.init_logging(tmp_path)

        assert early not in root.handlers
        assert len(root.handlers) == 2  # ia_download.log + stdout
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved:
            root.addHandler(handler)


def test_collection_manifests_are_fetched_concurrently(monkeypatch, tmp_path):
    """Manifests load --concurrency at a time, and collection items leave metadata.json alone."""
    import threading

    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "coll", "--collection", "--dry-run", "-j", "3",
        "--destdir", str(tmp_path), "--no-lock",
    ])
    monkeypatch.setattr(fetcher, "find_ia_executable", lambda custom=None: "ia")
    monkeypatch.setattr(fetcher, "get_collection_items", lambda ia, cid, query="": ["a", "b", "c"])
    all_started = threading.Barrier(3, timeout=5)
    primaries = {}
    serialized = []

    def fake_get_manifest(item_id, dest, force_update=False, primary=True):
        primaries[item_id] = primary
        try:
            all_started.wait()  # only passes if all three fetches are in flight at once
        except threading.BrokenBarrierError:
            serialized.append(item_id)
        return {}

    monkeypatch.setattr(fetcher, "get_manifest", fake_get_manifest)
    fetcher._shutdown_event.clear()

    fetcher.main()

    assert sorted(primaries) == ["a", "b", "c"]
    assert serialized == [], "manifests were fetched one at a time"
    assert not any(primaries.values()), "collection items must not overwrite metadata.json"


def test_search_mirrors_every_hit_into_the_label_folder(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "search-drone", "--search", "(drone)", "--json-output", "--dry-run",
        "--destdir", str(tmp_path), "--no-lock",
    ])
    monkeypatch.setattr(fetcher, "find_ia_executable", lambda custom=None: "ia")
    searched = []
    monkeypatch.setattr(fetcher, "search_items", lambda ia, query: searched.append(query) or ["hit-1", "hit-2"])
    fetched = []
    monkeypatch.setattr(fetcher, "get_manifest",
                        lambda item_id, dest, force_update=False, primary=True: fetched.append(item_id) or {})
    fetcher._shutdown_event.clear()

    fetcher.main()

    assert searched == ["(drone)"]
    assert sorted(fetched) == ["hit-1", "hit-2"]


def test_search_with_no_hits_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "search-x", "--search", "(nothing)", "--destdir", str(tmp_path), "--no-lock",
    ])
    monkeypatch.setattr(fetcher, "find_ia_executable", lambda custom=None: "ia")
    monkeypatch.setattr(fetcher, "search_items", lambda ia, query: [])
    fetcher._shutdown_event.clear()

    assert fetcher.main() == 1


def test_search_and_collection_are_exclusive(monkeypatch, tmp_path):
    monkeypatch.setenv("IA_HEALTH_PORT", "0")
    monkeypatch.setattr(sys, "argv", [
        "fetcher.py", "mirror", "coll", "--collection", "--search", "(x)", "--destdir", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as exc:
        fetcher.main()
    assert exc.value.code == 2
