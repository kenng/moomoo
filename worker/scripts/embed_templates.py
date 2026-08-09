#!/usr/bin/env python3
"""Embed local momo API templates + CSS into the Worker package.

Source of truth: src/momo/api/templates/ and src/momo/api/static/style.css
Output: worker/src/template_sources.py and worker/public/static/style.css
(Assets directory is public/, so /static/style.css matches the HTML.)
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SRC = REPO / "src" / "momo" / "api" / "templates"
CSS_SRC = REPO / "src" / "momo" / "api" / "static" / "style.css"
CSS_DST = ROOT / "public" / "static" / "style.css"
OUT = ROOT / "src" / "template_sources.py"


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"missing templates dir: {SRC}")
    if not CSS_SRC.is_file():
        raise SystemExit(f"missing stylesheet: {CSS_SRC}")

    parts = [
        '"""Auto-generated Jinja template strings for Cloudflare Workers (no filesystem)."""',
        "from __future__ import annotations",
        "",
        "TEMPLATES: dict[str, str] = {",
    ]
    paths = sorted(SRC.glob("*.html"))
    if not paths:
        raise SystemExit(f"no HTML templates in {SRC}")
    for path in paths:
        parts.append(f"    {path.name!r}: {path.read_text(encoding='utf-8')!r},")
    parts.append("}")
    parts.append("")
    OUT.write_text("\n".join(parts), encoding="utf-8")

    CSS_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CSS_SRC, CSS_DST)

    # Drop legacy flat static/ copy if present
    legacy = ROOT / "static" / "style.css"
    if legacy.is_file() and legacy.resolve() != CSS_DST.resolve():
        legacy.unlink()

    print(f"wrote {OUT.relative_to(ROOT)} ({len(paths)} templates from {SRC.relative_to(REPO)})")
    print(f"copied {CSS_SRC.relative_to(REPO)} → {CSS_DST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
