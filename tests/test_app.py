"""The Mac app module loads and shares the command line's options (it isn't launched here)."""

import pytest

AppKit = pytest.importorskip("AppKit")


def test_app_module_and_args():
    from mocap import app
    from mocap.__main__ import Running, parser
    args = parser().parse_args(["--sim", "--dry-run", "--no-hotkeys"])
    assert args.sim and args.dry_run
    assert callable(app.main)


def test_sim_never_touches_real_data(tmp_path, monkeypatch):
    from mocap.__main__ import Running, parser
    monkeypatch.setenv("HOME", str(tmp_path))
    run = Running(parser().parse_args(["--sim", "--dry-run"]))
    assert "mocap-sim-" in str(run.data_dir)
    assert not (tmp_path / "Library").exists()
