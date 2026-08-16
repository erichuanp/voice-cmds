"""Differential self-update for voice-cmds.

Flow:
1. Fetch the latest GitHub release's `manifest.json` asset (small) and the
   portable zip asset URL.
2. Diff against the shipped local manifest: only changed / deleted files
   are handled — unchanged files are never downloaded.
3. Changed files are pulled from the zip via HTTP Range requests (the zip's
   central directory + the compressed byte range of each changed entry);
   any failure falls back to downloading the whole zip.
4. Changed files are staged into `<app>/_update/` with a plan file
   `_update.json`; `launch_update_bat()` writes and spawns the platform
   apply script (update.bat on Windows, update.sh on macOS), and the app
   exits. The script copies the staged files over the app, deletes removed
   files, then restarts the app.

User data (models/ logs/ config/ scripts/) is never touched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Callable, Optional

import requests

logger = logging.getLogger("voice_cmds.updater")

GITHUB_API = "https://api.github.com/repos/erichuanp/voice-cmds/releases/latest"

StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def fetch_latest_release() -> dict:
    r = requests.get(GITHUB_API, timeout=10)
    r.raise_for_status()
    data = r.json()
    manifest_url = zip_url = None
    # On macOS pick the zip built for this machine's architecture
    # (voice-cmds-vX-macos-arm64-portable.zip / -macos-x86_64-...); on other
    # platforms the plain voice-cmds-vX-portable.zip.
    if sys.platform == "darwin":
        import platform as _platform

        want = "arm64" if _platform.machine() == "arm64" else "x86_64"
        marker = f"-macos-{want}-"
        for a in data.get("assets", []):
            if a.get("name") == "manifest.json":
                manifest_url = a["browser_download_url"]
            elif a.get("name", "").endswith("-portable.zip") and marker in a["name"]:
                zip_url = a["browser_download_url"]
    else:
        for a in data.get("assets", []):
            if a.get("name") == "manifest.json":
                manifest_url = a["browser_download_url"]
            elif (
                a.get("name", "").endswith("-portable.zip")
                and "-macos-" not in a["name"]
            ):
                zip_url = a["browser_download_url"]
    return {"tag": str(data["tag_name"]), "manifest_url": manifest_url, "zip_url": zip_url}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_local_manifest(root: Path) -> dict:
    p = root / "manifest.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": "0.0.0", "files": []}


def compute_diff(root: Path, remote: dict) -> tuple[list[str], list[str]]:
    """Return (changed paths, deleted paths) vs the shipped local manifest.

    manifest.json is special: it never participates in the diff — the
    updater always stages the freshly downloaded remote manifest instead,
    so the local copy tracks the installed version.
    """
    local_files = {
        f["path"]: f
        for f in load_local_manifest(root).get("files", [])
        if f["path"] != "manifest.json"
    }
    remote_files = {
        f["path"]: f
        for f in remote.get("files", [])
        if f["path"] != "manifest.json"
    }
    changed = []
    for path, meta in remote_files.items():
        p = root / path
        if not p.exists() or p.stat().st_size != meta.get("size", -1) or sha256_file(p) != meta["sha256"]:
            changed.append(path)
    deleted = [p for p in local_files if p not in remote_files]
    return changed, deleted


# --- zip byte-range access ------------------------------------------------
def _zip_index(url: str) -> dict:
    """name -> (local_header_offset, method, compressed_size)."""
    head = requests.get(url, stream=True, timeout=(15, 60))
    total = int(head.headers.get("Content-Length") or 0)
    head.close()
    if total <= 0:
        raise RuntimeError("无法获取更新包大小")
    # NOTE: release-assets.githubusercontent.com does NOT support suffix byte
    # ranges ("bytes=-66000" → 501); an explicit start-end range works.
    tail_start = max(0, total - 66000)
    tail = requests.get(
        url, headers={"Range": f"bytes={tail_start}-{total - 1}"},
        timeout=(15, 60),
    ).content
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise RuntimeError("更新包格式异常（找不到 EOCD）")
    eocd_abs = total - (len(tail) - eocd)
    (_sig, _disk, _cd_disk, _disk_entries, cd_count, cd_size, cd_offset, _clen) = struct.unpack_from(
        "<4s4H2LH", tail, eocd
    )
    cd = requests.get(
        url, headers={"Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}"},
        timeout=(15, 60),
    ).content
    entries = {}
    off = 0
    for _ in range(cd_count):
        if cd[off:off + 4] != b"PK\x01\x02":
            break
        (sig, _vmaj, _vmin, _flags, method, _mtime, _mdate, _crc, comp,
         _uncomp, nlen, elen, clen, _disk, _iattr, _eattr, lho) = struct.unpack_from(
            "<4s6H3I5H2I", cd, off
        )
        name = cd[off + 46:off + 46 + nlen].decode("utf-8", "replace")
        name = re.sub(r"^voice-cmds/", "", name)
        entries[name] = (lho, method, comp)
        off += 46 + nlen + elen + clen
    return entries


def _fetch_entry(url: str, lho: int, method: int, comp: int) -> bytes:
    r = requests.get(
        url, headers={"Range": f"bytes={lho}-{lho + 30 + 256 + comp - 1}"},
        timeout=(15, 120),
    )
    buf = r.content
    r.close()
    if buf[:4] != b"PK\x03\x04":
        raise RuntimeError("条目头异常")
    nlen, elen = struct.unpack_from("<HH", buf, 26)
    data = buf[30 + nlen + elen:30 + nlen + elen + comp]
    if method == 0:
        return data
    if method == 8:
        return zlib.decompress(data, -15)
    raise RuntimeError(f"不支持的压缩方式 {method}")


def _stage(root: Path, remote: dict, changed: list[str], deleted: list[str]) -> None:
    stage = root / "_update"
    if stage.exists():
        shutil.rmtree(stage)
    (root / "_update.json").write_text(
        json.dumps({"files": changed, "deleted": deleted}, ensure_ascii=False),
        encoding="utf-8",
    )


def _full_download(
    root: Path, zip_url: str, files_map: dict, changed: list[str], deleted: list[str],
    status_cb: StatusCB, progress_cb: ProgressCB, manifest_text: str = "",
) -> None:
    """Fallback: download the whole zip and extract the changed entries."""
    if status_cb:
        status_cb("差分下载失败，正在下载完整更新包…")
    import io
    import zipfile

    with requests.get(zip_url, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        buf = io.BytesIO()
        for chunk in r.iter_content(1 << 16):
            buf.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(done, total)
    buf.seek(0)
    stage = root / "_update"
    if stage.exists():
        shutil.rmtree(stage)
    with zipfile.ZipFile(buf) as zf:
        names = set(zf.namelist())
        for p in changed:
            zname = p if p in names else f"voice-cmds/{p}"
            data = zf.read(zname)
            if sha256_bytes(data) != files_map[p]["sha256"]:
                raise RuntimeError(f"校验失败: {p}")
            dest = stage / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    if manifest_text:
        (stage / "manifest.json").write_text(manifest_text, encoding="utf-8")
    (root / "_update.json").write_text(
        json.dumps({"files": changed + (["manifest.json"] if manifest_text else []),
                    "deleted": deleted}, ensure_ascii=False),
        encoding="utf-8",
    )


def prepare_update(
    root: Path,
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> tuple[int, int]:
    """Stage an update into <root>/_update; return (changed, deleted) counts."""
    info = fetch_latest_release()
    if not info["manifest_url"] or not info["zip_url"]:
        raise RuntimeError("发布页缺少 manifest 或便携包资产，请手动下载")
    r = requests.get(info["manifest_url"], timeout=(15, 60))
    r.raise_for_status()
    manifest_text = r.text
    remote = json.loads(manifest_text)
    files_map = {f["path"]: f for f in remote.get("files", [])}
    changed, deleted = compute_diff(root, remote)
    if status_cb:
        status_cb("正在比对差异文件…")
    if not changed and not deleted:
        _stage(root, remote, changed, deleted)
        return 0, 0
    try:
        index = _zip_index(info["zip_url"])
        stage = root / "_update"
        if stage.exists():
            shutil.rmtree(stage)
        total = sum(index[p][2] for p in changed if p in index) or 1
        done = 0
        for p in changed:
            if p not in index:
                raise KeyError(p)
            lho, method, comp = index[p]
            data = _fetch_entry(info["zip_url"], lho, method, comp)
            if sha256_bytes(data) != files_map[p]["sha256"]:
                raise RuntimeError(f"校验失败: {p}")
            dest = stage / p
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            done += comp
            if progress_cb:
                progress_cb(done, total)
        # Always ship the freshly downloaded manifest with the update.
        (stage / "manifest.json").write_text(manifest_text, encoding="utf-8")
        (root / "_update.json").write_text(
            json.dumps({"files": changed + ["manifest.json"], "deleted": deleted},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("Differential update failed; falling back to full download")
        _full_download(root, info["zip_url"], files_map, changed, deleted,
                       status_cb, progress_cb, manifest_text)
    return len(changed), len(deleted)


def build_update_sh_lines(root: Path, plan: dict) -> list[str]:
    """The macOS apply script (update.sh). Exposed for tests.

    macOS has no mandatory file locks, so replacing a running executable
    works without the Windows retry dance; everything else mirrors the
    Windows bat: copy staged files over the app dir, remove deleted files,
    clean up, relaunch.
    """
    lines = [
        "#!/bin/sh",
        'cd "$(dirname "$0")"',
        "sleep 1",
        'cp -R _update/. .',
        "rm -rf _update",
    ]
    for p in plan.get("deleted", []):
        lines.append(f'rm -f "./{p}"')
    lines += [
        'rm -f "./_update.json"',
        'rm -f "./update.sh"',
        'nohup ./voice-cmds >/dev/null 2>&1 &',
    ]
    return lines


def launch_update_sh(root: Path) -> Path:
    """Write and spawn update.sh (macOS); return the script path."""
    plan = json.loads((root / "_update.json").read_text(encoding="utf-8"))
    sh_path = root / "update.sh"
    sh_path.write_text("\n".join(build_update_sh_lines(root, plan)) + "\n",
                       encoding="utf-8")
    subprocess.Popen(
        ["/bin/sh", str(sh_path)],
        start_new_session=True,
        close_fds=True,
        cwd=str(root),
    )
    logger.info("Update sh spawned: %s", sh_path)
    return sh_path


def launch_update_bat(root: Path) -> Path:
    """Write and spawn the platform apply script (update.bat / update.sh),
    then the app exits and the script stages files + restarts.

    The Windows bat waits for the old process to release file locks and
    RETRIES copying the exe until it succeeds (restarting too early left
    the old exe in place in 0.8.0/0.8.1), then copies everything else,
    removes deleted files and starts the new exe.
    """
    if sys.platform == "darwin":
        return launch_update_sh(root)
    plan = json.loads((root / "_update.json").read_text(encoding="utf-8"))
    lines = [
        "@echo off",
        "timeout /t 3 /nobreak >nul",
        ":retry_exe",
        'copy /y "%~dp0_update\\voice-cmds.exe" "%~dp0voice-cmds.exe" >nul 2>&1',
        "if errorlevel 1 (",
        "  timeout /t 1 /nobreak >nul",
        "  goto retry_exe",
        ")",
        'xcopy /e /y /q "%~dp0_update\\*" "%~dp0" >nul 2>&1',
        'rmdir /s /q "%~dp0_update"',
    ]
    for p in plan.get("deleted", []):
        rel = p.replace("/", "\\")
        lines.append(f'del /f /q "%~dp0{rel}"')
    lines.append('del /f /q "%~dp0_update.json"')
    lines.append('start "" "%~dp0voice-cmds.exe"')
    bat = root / "update.bat"
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    subprocess.Popen(
        f'cmd /c ""{bat}""',
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        cwd=str(root),
    )
    logger.info("Update bat spawned: %s", bat)
    return bat
