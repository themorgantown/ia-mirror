import os
import sys
from unittest.mock import patch


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TEST_DIR)
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, DOCKER_DIR)

import fetcher


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