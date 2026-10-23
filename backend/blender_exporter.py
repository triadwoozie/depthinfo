"""
blender_exporter.py — headless Blender subprocess runner for static scene export
(AGENTS.md § 3.4). Triggered by ``POST /api/export/blend``.

The stitched cloud is dumped to ``temp_<ts>.ply`` and then::

    <blender> --background blender/template_exhibition.blend \
              --python blender/export_static_scene.py -- \
              --input temp.ply --output scan_<ts>.blend --color_mode <mode> ...

which writes ``scan_<ts>.blend`` and ``scan_<ts>.glb``; the PLY is kept as ``scan_<ts>.ply``.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import signal
import time
from pathlib import Path

from config import REPO_DIR, SystemConfig, settings
from stitcher import Stitcher

BLENDER_DIR = REPO_DIR / "blender"
EXPORT_TIMEOUT_S = 180


class ExportError(RuntimeError):
    """An export that could not be produced; the message is shown to the operator."""


def find_blender() -> str | None:
    """Locate a Blender executable: $BLENDER_BIN, PATH, then ~/blender*/blender."""
    candidates: list[str] = []
    if settings.blender_bin:
        candidates.append(settings.blender_bin)
    on_path = shutil.which("blender")
    if on_path:
        candidates.append(on_path)
    home = str(Path.home())
    for pattern in ("blender*/blender", "Blender*/blender", "blender*/Blender*/blender", ".local/blender*/blender"):
        candidates += sorted(glob.glob(os.path.join(home, pattern)), reverse=True)
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    """Kill the subprocess's whole process group and reap it, so nothing runs on or lingers as a
    zombie."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    await proc.wait()


def _discard(*paths: Path) -> None:
    """Remove the temp PLY and any partial outputs of a failed export."""
    for path in paths:
        path.unlink(missing_ok=True)


async def export_scene(stitcher: Stitcher, cfg: SystemConfig) -> dict:
    """Freeze the stitched geometry and render it to .blend / .glb / .ply."""
    blender = find_blender()
    if blender is None:
        raise ExportError("Blender executable not found (set BLENDER_BIN or add it to PATH)")

    out_dir = settings.exports_path
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    temp_ply = out_dir / f"temp_{ts}.ply"
    blend, glb, ply = (out_dir / f"scan_{ts}.{ext}" for ext in ("blend", "glb", "ply"))

    try:
        n_vertices = await asyncio.get_running_loop().run_in_executor(None, stitcher.export_ply, temp_ply, cfg)
    except RuntimeError as exc:
        temp_ply.unlink(missing_ok=True)
        raise ExportError(str(exc)) from exc

    template = BLENDER_DIR / "template_exhibition.blend"
    cmd = [blender, "--background"]
    if template.exists():
        cmd.append(str(template))
    style, vf = cfg.blender_style, cfg.vision_and_fog
    cmd += [
        "--python", str(BLENDER_DIR / "export_static_scene.py"), "--",
        "--input", str(temp_ply),
        "--output", str(blend),
        "--color_mode", style.color_mode,
        "--near_hex", style.custom_near_hex,
        "--far_hex", style.custom_far_hex,
        "--point_radius_mm", str(style.point_radius_mm),
        "--min_scan", str(vf.min_scan_distance_m),
        "--max_scan", str(vf.max_scan_distance_m),
        "--fog_density", str(vf.fog_density),
        "--falloff", vf.fog_falloff_curve,
    ]
    # Own process group, so a timeout or cancellation can stop Blender *and* anything it spawned
    # (a surviving child would keep the output pipe open and hang the wait below).
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=EXPORT_TIMEOUT_S)
    except asyncio.TimeoutError:
        await _terminate(proc)
        _discard(temp_ply, blend, glb)
        raise ExportError(f"Blender export timed out after {EXPORT_TIMEOUT_S}s") from None
    except asyncio.CancelledError:  # the request went away: do not leave Blender running
        await _terminate(proc)
        _discard(temp_ply, blend, glb)
        raise
    log = stdout.decode(errors="replace")

    if proc.returncode != 0 or not blend.exists():
        _discard(temp_ply, blend, glb)
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise ExportError(f"Blender exited with code {proc.returncode}:\n{tail}")

    shutil.move(str(temp_ply), ply)
    return {
        "status": "ok",
        "vertex_count": n_vertices,
        "blend_url": f"/exports/{blend.name}",
        "glb_url": f"/exports/{glb.name}" if glb.exists() else None,
        "ply_url": f"/exports/{ply.name}",
    }
