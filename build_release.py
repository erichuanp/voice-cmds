"""Cross-platform release packager: manifest.json + portable zip.

The Windows pipeline has build_release.ps1 (manifest + 7-Zip + Inno Setup);
macOS needs the same two first artifacts with nothing but the standard
library. Produces:

  <dist>/manifest.json                  (shipped — the updater diffs against it)
  release/manifest.json                 (GitHub release asset)
  release/voice-cmds-v<ver><suffix>-portable.zip

Excludes user-data dirs (models/, logs/) from the manifest exactly like the
PowerShell script does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path


def _repo_version() -> str:
    init = Path(__file__).resolve().parent / "voice_cmds" / "__init__.py"
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"no __version__ in {init}")
    return m.group(1)


def build(args: argparse.Namespace) -> Path:
    dist = Path(args.dist).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    version = args.version or _repo_version()

    # 1. manifest.json — every shipped file's sha256 (models/logs excluded).
    files = []
    for p in sorted(dist.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dist).as_posix()
        if rel.split("/")[0] in ("models", "logs"):
            continue
        files.append({
            "path": rel,
            "size": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
    manifest_text = json.dumps({"version": version, "files": files}, ensure_ascii=False)
    (dist / "manifest.json").write_text(manifest_text, encoding="utf-8")
    (out / "manifest.json").write_text(manifest_text, encoding="utf-8")
    print(f"[1/2] manifest.json: {len(files)} files")

    # 2. portable zip (paths include the top-level dir, like the ps1 script).
    suffix = f"-{args.suffix}" if args.suffix else ""
    zip_path = out / f"voice-cmds-v{version}{suffix}-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(dist.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(dist.parent).as_posix())
    print(f"[2/2] {zip_path.name}: {zip_path.stat().st_size / 1e6:.1f} MB")
    return zip_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default="")
    ap.add_argument("--dist", default="dist/voice-cmds")
    ap.add_argument("--out", default="release")
    ap.add_argument("--suffix", default="", help="e.g. 'macos-arm64' (dash added automatically)")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
