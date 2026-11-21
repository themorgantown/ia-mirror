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


def test_download_single_file_retries_on_failure(monkeypatch, tmp_path):
    """download_single_file retries on ia_file.download() failure and succeeds on 3rd attempt."""
    call_count = {"n": 0}

    def fake_ia_download(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return False
        kwargs["fileobj"].write(b"data")
        return True

    mock_ia_file = MagicMock()
    mock_ia_file.download.side_effect = fake_ia_download

    mock_item = MagicMock()
    mock_item.get_file.return_value = mock_ia_file

    monkeypatch.setenv("IA_DOWNLOAD_RETRIES", "3")
    monkeypatch.setenv("IA_RETRY_BACKOFF_BASE", "0")
    fetcher._shutdown_event.clear()

    with patch("fetcher.internetarchive.get_item", return_value=mock_item):
        fname, ok = fetcher.download_single_file(
            ia="ia",
            identifier="test-item",
            filename="test.txt",
            destdir=tmp_path,
            manifest_entry={"size": 4},
            retries=1,
            progress_timeout=30,
            max_timeout=60,
            verify_mode="none",
            idx=0,
            total=1,
            max_mbps=0,
            bucket=None,
        )

    assert ok is True, "Expected success after retries"
    assert mock_ia_file.download.call_count == 3


def test_download_single_file_subdir_does_not_touch_cwd(monkeypatch, tmp_path):
    """Files inside item subfolders must not make the library create dirs in cwd.

    internetarchive's File.download() runs os.makedirs(dirname(name)) relative to
    its destdir even when given a fileobj. With no destdir that is the process cwd
    (/app in the image, not writable), so "Mozart/CD01.flac" failed with EACCES.
    """
    name = "Mozart/CD01.flac"

    def fake_ia_download(**kwargs):
        # Mimic the library's side effect, then write the payload.
        os.makedirs(os.path.join(kwargs.get("destdir") or "", os.path.dirname(name)), exist_ok=True)
        kwargs["fileobj"].write(b"data")
        return True

    mock_ia_file = MagicMock()
    mock_ia_file.download.side_effect = fake_ia_download
    mock_item = MagicMock()
    mock_item.get_file.return_value = mock_ia_file

    cwd = tmp_path / "cwd"
    dest = tmp_path / "dest"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    fetcher._shutdown_event.clear()

    with patch("fetcher.internetarchive.get_item", return_value=mock_item):
        _, ok = fetcher.download_single_file(
            ia="ia",
            identifier="test-item",
            filename=name,
            destdir=dest,
            manifest_entry={"size": 4},
            retries=1,
            progress_timeout=30,
            max_timeout=60,
            verify_mode="none",
            idx=0,
            total=1,
            max_mbps=0,
            bucket=None,
        )

    assert ok is True
    assert (dest / name).read_bytes() == b"data"
    assert list(cwd.iterdir()) == [], "library side-effect dirs leaked into cwd"


def test_download_single_file_midstream_failure_keeps_full_file(monkeypatch, tmp_path):
    """A dropped connection must never leave a truncated file in place.

    When internetarchive retries internally it closes the caller's fileobj and
    writes the rest to its own file_path, then reports success. The partial
    tmp file would then be renamed over the real destination.
    """
    payload = b"0123456789"
    calls = {"n": 0}

    def fake_ia_download(**kwargs):
        # Mimic internetarchive 5.x: the first stream dies after 3 bytes.
        calls["n"] += 1
        fileobj = kwargs["fileobj"]
        if calls["n"] == 1:
            fileobj.write(payload[:3])
            if kwargs.get("retries", 0) > 0:
                side = os.path.join(kwargs.get("destdir") or ".", "test.bin")
                with open(side, "wb") as fh:  # library's own retry target
                    fh.write(payload)
                return True
            raise OSError("connection reset")
        fileobj.write(payload)
        return True

    mock_ia_file = MagicMock()
    mock_ia_file.download.side_effect = fake_ia_download
    mock_item = MagicMock()
    mock_item.get_file.return_value = mock_ia_file

    monkeypatch.setenv("IA_RETRY_BACKOFF_BASE", "0")
    monkeypatch.delenv("IA_DOWNLOAD_RETRIES", raising=False)
    monkeypatch.chdir(tmp_path)
    fetcher._shutdown_event.clear()

    with patch("fetcher.internetarchive.get_item", return_value=mock_item):
        _, ok = fetcher.download_single_file(
            ia="ia",
            identifier="test-item",
            filename="test.bin",
            destdir=tmp_path / "dest",
            manifest_entry={"size": len(payload)},
            retries=2,
            progress_timeout=30,
            max_timeout=60,
            verify_mode="none",
            idx=0,
            total=1,
            max_mbps=0,
            bucket=None,
        )

    assert ok is True
    assert (tmp_path / "dest" / "test.bin").read_bytes() == payload


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
