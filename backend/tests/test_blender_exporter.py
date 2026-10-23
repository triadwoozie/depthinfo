"""Export failure paths must not leak processes, zombies or files (fake Blender executables)."""
import asyncio
import os
import stat
import time

import numpy as np
import pytest

import blender_exporter
from config import SystemConfig, settings
from stitcher import Stitcher


class TinyStitcher(Stitcher):
    """A stitcher holding a few real points, so export_ply writes a valid PLY."""
    def __init__(self):
        super().__init__()
        self._V = np.random.default_rng(0).normal(0, 0.1, (50, 3)).astype(np.float32) + [0, 0, 1.5]
        self._C = np.full((50, 3), 0.5, np.float32)


def fake_blender(tmp_path, body):
    path = tmp_path / "fake-blender"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    out = tmp_path / "exports"
    monkeypatch.setattr(settings, "exports_dir", str(out))
    return out


def children_of_me():
    """Live or zombie child processes of this test process."""
    me = str(os.getpid())
    kids = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if fields[1] == me:
            kids.append((pid, fields[0]))  # (pid, state)
    return kids


def test_failed_blender_leaves_no_files_and_no_process(tmp_path, monkeypatch, export_env):
    monkeypatch.setattr(settings, "blender_bin", fake_blender(tmp_path, "echo boom >&2; exit 3"))
    with pytest.raises(blender_exporter.ExportError, match="code 3"):
        asyncio.run(blender_exporter.export_scene(TinyStitcher(), SystemConfig()))
    assert list(export_env.iterdir()) == []          # temp PLY removed
    assert children_of_me() == []                     # reaped, no zombie


def test_hung_blender_is_killed_and_reaped_on_timeout(tmp_path, monkeypatch, export_env):
    monkeypatch.setattr(settings, "blender_bin", fake_blender(tmp_path, "sleep 30"))
    monkeypatch.setattr(blender_exporter, "EXPORT_TIMEOUT_S", 0.5)
    t0 = time.time()
    with pytest.raises(blender_exporter.ExportError, match="timed out"):
        asyncio.run(blender_exporter.export_scene(TinyStitcher(), SystemConfig()))
    assert time.time() - t0 < 10
    assert list(export_env.iterdir()) == []
    assert children_of_me() == []


def test_cancelled_export_stops_blender(tmp_path, monkeypatch, export_env):
    monkeypatch.setattr(settings, "blender_bin", fake_blender(tmp_path, "sleep 30"))

    t0 = time.time()

    async def cancel_midway():
        task = asyncio.create_task(blender_exporter.export_scene(TinyStitcher(), SystemConfig()))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_midway())
    assert time.time() - t0 < 10                      # not waiting for the 30 s child
    assert list(export_env.iterdir()) == []
    assert children_of_me() == []


def test_no_points_is_a_clean_error(tmp_path, monkeypatch, export_env):
    monkeypatch.setattr(settings, "blender_bin", fake_blender(tmp_path, "exit 0"))
    with pytest.raises(blender_exporter.ExportError, match="no points"):
        asyncio.run(blender_exporter.export_scene(Stitcher(), SystemConfig()))
    assert list(export_env.iterdir()) == []
