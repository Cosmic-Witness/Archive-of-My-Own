#!/usr/bin/env python3
"""Build the self-hosted web fonts in fonts/ from their official sources. All three families are SIL OFL 1.1.

    roboto-serif-display.woff2    Roboto Serif, fixed at width 62, weight 900, optical size 144
                                  (the heavy condensed serif used for the name and the headings)
    roboto-serif-text.woff2       Roboto Serif at normal width and text optical size, weights 400 to 700
                                  (body text in the `paper` preset)
    space-mono-regular.woff2      Space Mono Regular   (body text in `mixed` and `terminal`)
    space-mono-bold.woff2         Space Mono Bold

Sources are files in github.com/google/fonts at one pinned commit, fetched from raw.githubusercontent.com and
checked against the SHA-256 values below. Each family's licence is copied next to its font as OFL-<family>.txt.

Subsetting, fixing variable axes and converting to WOFF2 make a "Modified Version" under the OFL. Neither
family declares a Reserved Font Name, so no renaming is needed, and the copyright and licence strings stay
inside every font file.

Usage:
    python scripts/fonts.py            # download (cached in .cache/fonts), verify, build
Needs: pip install -r requirements-dev.txt
"""
from __future__ import annotations

import hashlib
import io
import logging
import pathlib
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts"
CACHE = ROOT / ".cache" / "fonts"

GF_COMMIT = "9710da1eacb3be272583c3224dcb70f9da6eadbb"
RAW = f"https://raw.githubusercontent.com/google/fonts/{GF_COMMIT}/ofl/"

SOURCES = {   # path under ofl/ -> sha256
    "spacemono/SpaceMono-Regular.ttf": "95837e182baeeada83368f7748db28357f0a1b75c6b84ff7065b5edf933c8e18",
    "spacemono/SpaceMono-Bold.ttf": "405e73d41afb7e5906efce206a326af5c956f38e255f35421c260e861e599c59",
    "spacemono/OFL.txt": "8e4ee42b2553e1e01504e61cb0d46d148cd8c9e5eacaa3622a7df2d4f2955b9f",
    "robotoserif/RobotoSerif[GRAD,opsz,wdth,wght].ttf": "351ced75f3851806aa6d846b669361521eb1925cfc530396df9c1a1b77061ddb",
    "robotoserif/OFL.txt": "34dbfbb43e0b4fdeef445d77b9ac0b988e5ad7a9bbf16808c97b66c66d51f553",
}
LICENCES = {"spacemono/OFL.txt": "OFL-space-mono.txt", "robotoserif/OFL.txt": "OFL-roboto-serif.txt"}

LATIN = [(0x20, 0x7E), (0xA0, 0xFF), (0x100, 0x17F), (0x2010, 0x2015), (0x2018, 0x201E), (0x2020, 0x2022),
         (0x2026, 0x2026), (0x2030, 0x2030), (0x2032, 0x2033), (0x2039, 0x203A), (0x20AC, 0x20AC),
         (0x2122, 0x2122), (0x2190, 0x2193), (0x2197, 0x2198)]


def fetch(path: str) -> bytes:
    cached = CACHE / path
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        url = RAW + urllib.parse.quote(path, safe="/")
        print(f"downloading {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "font build script"})
        with urllib.request.urlopen(req, timeout=180) as r:
            cached.write_bytes(r.read())
    data = cached.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != SOURCES[path]:
        sys.exit(f"sha256 mismatch for {path}\n  expected {SOURCES[path]}\n  got      {got}")
    return data


def codepoints(ranges) -> list[int]:
    return [c for lo, hi in ranges for c in range(lo, hi + 1)]


def build(name: str, raw: bytes, unicodes: list[int], features: list[str], instance: dict | None = None,
          variable: bool = False) -> None:
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(io.BytesIO(raw))
    if instance:
        font = instancer.instantiateVariableFont(font, instance, inplace=False)
        assert ("fvar" in font) == variable, "unexpected axes left in the font"
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)

    opts = subset.Options()
    opts.flavor = "woff2"
    opts.hinting = False
    opts.layout_features = features
    opts.name_IDs = ["*"]              # keep the copyright and licence strings inside the file
    opts.name_legacy = True
    opts.name_languages = ["*"]
    opts.glyph_names = False
    opts.notdef_outline = True
    opts.drop_tables += ["STAT", "DSIG"]
    font = subset.load_font(buf, opts)
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=unicodes)
    sub.subset(font)
    subset.save_font(font, OUT / name, opts)

    out = TTFont(OUT / name)
    assert out["name"].getDebugName(13), f"{name}: licence string missing"
    assert "Reserved Font Name" not in (out["name"].getDebugName(0) or ""), f"{name}: declares a reserved font name"
    cmap = out.getBestCmap()
    print(f"{name:30s} {(OUT / name).stat().st_size:6d} B  {out['name'].getDebugName(4) or ''!s:34s} {len(cmap):5d} code points")


def main() -> None:
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    OUT.mkdir(exist_ok=True)
    latin = codepoints(LATIN)
    feats = ["kern", "liga", "ccmp", "locl", "mark", "mkmk"]

    build("roboto-serif-display.woff2", fetch("robotoserif/RobotoSerif[GRAD,opsz,wdth,wght].ttf"), latin, feats,
          instance={"wdth": 62, "wght": 900, "opsz": 144, "GRAD": 0})
    build("roboto-serif-text.woff2", fetch("robotoserif/RobotoSerif[GRAD,opsz,wdth,wght].ttf"), latin, feats,
          instance={"wdth": 100, "opsz": 14, "GRAD": 0, "wght": (400, 700)}, variable=True)
    build("space-mono-regular.woff2", fetch("spacemono/SpaceMono-Regular.ttf"), latin, feats)
    build("space-mono-bold.woff2", fetch("spacemono/SpaceMono-Bold.ttf"), latin, feats)

    for src, dst in LICENCES.items():
        (OUT / dst).write_bytes(fetch(src))
    print("licences:", ", ".join(LICENCES.values()))


if __name__ == "__main__":
    main()
