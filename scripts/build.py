#!/usr/bin/env python3
"""Build dist/quick_deck_switcher-<version>.ankiaddon, ready for AnkiWeb.

AnkiWeb rules: files at the root of the zip (no top-level folder) and no
__pycache__. meta.json (your local settings) is never packaged.
"""
import json
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "quick_deck_switcher"
DIST = ROOT / "dist"
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_FILES = {"meta.json"}


def main() -> Path:
    manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    manifest["mod"] = int(time.time())  # stamped at build time only
    version = manifest.get("human_version", "dev")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"quick_deck_switcher-{version}.ankiaddon"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC.rglob("*")):
            rel = path.relative_to(SRC)
            if path.is_dir() or EXCLUDE_DIRS & set(rel.parts) or rel.name in EXCLUDE_FILES:
                continue
            if rel.name.endswith(".pyc"):
                continue
            if rel.as_posix() == "manifest.json":
                zf.writestr("manifest.json", json.dumps(manifest, indent=4, ensure_ascii=False))
            else:
                zf.write(path, rel.as_posix())
    print(f"Built {out.relative_to(ROOT)}")
    for name in zipfile.ZipFile(out).namelist():
        print("  ", name)
    return out


if __name__ == "__main__":
    main()
