"""CI-only end-to-end update smoke test (runs on macOS and Windows):

1. Copy the built dist to a temp "installed" dir with a fake old manifest
   (v0.0.0, one extra file marked for deletion).
2. Build a "new version" of the tree (a new file, the voice-cmds executable
   replaced by a marker shell script so the relaunch is observable).
3. Serve a fake release (manifest.json + zip) over localhost HTTP **with
   Range support** (the differential updater fetches byte ranges).
4. Run prepare_update() against the fake release, then launch the platform
   apply script for real and verify: new file copied, deleted file removed,
   _update/_update.json cleaned, and the "relaunched" marker script ran.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_cmds import updater  # noqa: E402
from build_release import build  # noqa: E402


class _RangeHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler + HTTP Range (the updater needs 206s)."""

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):  # noqa: N802
        try:
            rng = self.headers.get("Range")
            if not rng or not rng.startswith("bytes="):
                return super().do_GET()
            path = self.translate_path(self.path)
            data = Path(path).read_bytes()
            spec = rng[6:]
            if "-" not in spec:
                self.send_error(400)
                return
            start_s, end_s = spec.split("-", 1)
            start = int(start_s) if start_s else len(data) - int(end_s)
            end = int(end_s) if end_s else len(data) - 1
            if start >= len(data) or start > end:
                self.send_error(416)
                return
            body = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass  # client finished early — normal for aborted probes

    def log_message(self, *args):  # silence
        pass


def main() -> int:
    dist = Path(__file__).resolve().parent.parent / "dist" / "voice-cmds"
    if not dist.exists():
        print("SKIP: dist/voice-cmds not built")
        return 0
    tmp = Path(tempfile.mkdtemp())
    try:
        # 1. "installed" copy with a fake old manifest + a file to delete
        app = tmp / "old" / "voice-cmds"
        shutil.copytree(dist, app, symlinks=True)
        (app / "OLD.txt").write_text("stale", encoding="utf-8")
        old_files = [
            {"path": "OLD.txt", "size": 5, "sha256": "0" * 64},
        ]
        (app / "manifest.json").write_text(
            json.dumps({"version": "0.0.0", "files": old_files}),
            encoding="utf-8",
        )

        # 2. "new version" tree: new file + marker executable + v9.9.9 manifest
        new = tmp / "new" / "voice-cmds"
        shutil.copytree(dist, new, symlinks=True)
        (new / "NEWFILE.txt").write_text("hello new version", encoding="utf-8")
        marker = tmp / "relaunch.marker"
        marker_sh = (
            "#!/bin/sh\n"
            f"echo relaunched > '{marker}'\n"
        )
        (new / "voice-cmds").write_text(marker_sh, encoding="utf-8")
        (new / "voice-cmds").chmod(0o755)
        (new / "OLD.txt").unlink(missing_ok=True)

        import argparse
        import platform as _platform

        # The darwin updater only accepts zips carrying the arch marker.
        if sys.platform == "darwin":
            suffix = f"macos-{_platform.machine()}"
        else:
            suffix = "test"
        z = build(argparse.Namespace(
            dist=str(new), out=str(tmp / "release"),
            version="9.9.9", suffix=suffix,
        ))
        release_dir = tmp / "release"
        release_json = {
            "tag_name": "v9.9.9",
            "assets": [
                {
                    "name": "manifest.json",
                    "browser_download_url":
                        f"http://127.0.0.1:{{}}/manifest.json",
                },
                {
                    "name": z.name,
                    "browser_download_url": f"http://127.0.0.1:{{}}/{z.name}",
                },
            ],
        }

        # 3. local HTTP server with Range support
        handler = partial(_RangeHandler, directory=str(release_dir))
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        release_json["assets"][0]["browser_download_url"] = (
            release_json["assets"][0]["browser_download_url"].format(port)
        )
        release_json["assets"][1]["browser_download_url"] = (
            release_json["assets"][1]["browser_download_url"].format(port)
        )
        (tmp / "release" / "release.json").write_text(
            json.dumps(release_json), encoding="utf-8"
        )
        updater.GITHUB_API = f"http://127.0.0.1:{port}/release.json"
        print("fake release served on", port)

        # 4. differential prepare + real apply
        changed, deleted = updater.prepare_update(app, status_cb=print)
        print(f"prepare_update: changed={changed} deleted={deleted}")
        assert changed >= 2, f"expected NEWFILE.txt + manifest staged, got {changed}"
        assert deleted == 1, f"expected OLD.txt deleted, got {deleted}"
        if sys.platform != "darwin":
            # The Windows bat relaunches voice-cmds.exe by name; the staging
            # + diff logic is what this smoke targets there (the real exe
            # apply path is covered by the Windows release pipeline tests).
            assert (app / "_update" / "NEWFILE.txt").exists()
            assert (app / "_update" / "manifest.json").exists()
            print("UPDATE SMOKE OK (staging only on win32)")
            httpd.shutdown()
            return 0
        updater.launch_update_bat(app)  # update.sh on darwin

        deadline = time.time() + 15
        while time.time() < deadline and not marker.exists():
            time.sleep(0.3)
        assert marker.exists(), "relaunch marker never appeared"
        assert (app / "NEWFILE.txt").exists(), "new file not copied"
        assert not (app / "OLD.txt").exists(), "deleted file still present"
        assert not (app / "_update").exists(), "_update not cleaned"
        assert not (app / "_update.json").exists(), "_update.json not cleaned"
        assert json.loads((app / "manifest.json").read_text(encoding="utf-8"))["version"] == "9.9.9"
        print("UPDATE SMOKE OK (diff apply + relaunch)")
        httpd.shutdown()
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
