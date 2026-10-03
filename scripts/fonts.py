#!/usr/bin/env python3
"""Build the self-hosted web fonts in fonts/ from the official CM Unicode release.

Source:   Computer Modern Unicode 0.7.0 (TrueType), by Andrey V. Panov
          https://sourceforge.net/projects/cm-unicode/
Licence:  SIL Open Font License 1.1 (see fonts/OFL.txt)

What this does to the originals (and nothing else):
  * keeps only Latin, punctuation, Greek and the maths symbols the faces contain;
  * drops hinting, embedded bitmaps and a few tool-specific tables;
  * writes WOFF2.
The OFL calls that a "Modified Version". Its one naming rule is that the Reserved
Font Family Name ("Computer Modern Unicode fonts") must not appear in the
modified fonts. The internal names stay "CMU Concrete" / "CMU Typewriter Text", and
this script checks that the reserved name is absent from every output file.

Usage:
    python scripts/fonts.py                    # download (cached), verify, subset
    python scripts/fonts.py --tarball PATH     # use a tarball you already have

Needs: pip install fonttools brotli
"""
from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "fonts"
CACHE = ROOT / ".cache"

URL = ("https://downloads.sourceforge.net/project/cm-unicode/cm-unicode/0.7.0/"
       "cm-unicode-0.7.0-ttf.tar.xz")
SHA256 = "2609c14450f42d0bcd40203900afcb1d693521a9b24a18c65e14b6b0585ff150"
RESERVED_NAME = "Computer Modern Unicode"

# source file in the tarball -> output name
FACES = {
    "cmunorm.ttf": "cmu-concrete-roman.woff2",       # CMU Concrete Roman
    "cmunobx.ttf": "cmu-concrete-bold.woff2",        # CMU Concrete Bold
    "cmuntt.ttf": "cmu-typewriter-regular.woff2",    # CMU Typewriter Text Regular
    "cmuntb.ttf": "cmu-typewriter-bold.woff2",       # CMU Typewriter Text Bold
}

# Code points we ask for. The subsetter keeps the ones a face actually has.
RANGES = [
    (0x0020, 0x007E),   # Basic Latin
    (0x00A0, 0x00FF),   # Latin-1 Supplement (accents, x, /, +-, fractions, degrees)
    (0x0100, 0x017F),   # Latin Extended-A
    (0x0192, 0x0192),
    (0x02C6, 0x02C7), (0x02D8, 0x02DD),
    (0x0391, 0x03A9), (0x03B1, 0x03C9),          # Greek capitals and small letters
    (0x2000, 0x200B),                            # spaces, including thin space
    (0x2010, 0x2015), (0x2018, 0x201E), (0x2020, 0x2022), (0x2026, 0x2026),
    (0x2030, 0x2030), (0x2032, 0x2033), (0x2039, 0x203A), (0x2044, 0x2044),
    (0x2070, 0x2079), (0x207F, 0x207F), (0x2080, 0x2089),   # super/subscripts
    (0x20AC, 0x20AC), (0x2113, 0x2113), (0x2122, 0x2122),
    (0x2190, 0x2193),                            # arrows
    (0x2212, 0x2212), (0x2217, 0x2217), (0x221A, 0x221A), (0x221E, 0x221E),
    (0x2248, 0x2248), (0x2260, 0x2260), (0x2264, 0x2265),   # minus * sqrt inf ~= != <= >=
    (0xFB00, 0xFB06),                            # f-ligatures
]
# Symbols a reader might expect that the CMU text faces do NOT contain (reported only).
WANTED_MATHS = "×÷±−∗√∞≈≠≤≥∂∇∑∏∫∈⊂∧∨∩∪→⇒"


def codepoints() -> list[int]:
    return [c for lo, hi in RANGES for c in range(lo, hi + 1)]


def fetch_tarball(path: pathlib.Path | None) -> bytes:
    if path is None:
        CACHE.mkdir(exist_ok=True)
        path = CACHE / "cm-unicode-0.7.0-ttf.tar.xz"
        if not path.exists():
            print(f"downloading {URL}")
            req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (font build script)"})
            with urllib.request.urlopen(req, timeout=120) as r:
                path.write_bytes(r.read())
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != SHA256:
        sys.exit(f"sha256 mismatch for {path}\n  expected {SHA256}\n  got      {got}")
    print(f"verified {path.name} (sha256 {got[:16]}...)")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tarball", type=pathlib.Path, help="local copy of cm-unicode-0.7.0-ttf.tar.xz")
    args = ap.parse_args()

    import logging

    from fontTools import subset
    from fontTools.ttLib import TTFont

    # The subsetter logs a line for every table we ask it to drop; that is not news.
    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

    data = fetch_tarball(args.tarball)
    OUT.mkdir(exist_ok=True)
    unicodes = codepoints()

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
        members = {pathlib.PurePosixPath(m.name).name: m for m in tar.getmembers() if m.isfile()}
        (OUT / "OFL.txt").write_bytes(tar.extractfile(members["OFL.txt"]).read())
        for src, dst in FACES.items():
            raw = tar.extractfile(members[src]).read()
            opts = subset.Options()
            opts.flavor = "woff2"
            opts.hinting = False
            opts.layout_features = ["kern", "liga", "locl", "ccmp", "mark", "mkmk"]
            opts.name_IDs = ["*"]            # keep copyright + licence strings inside the file
            opts.name_legacy = True
            opts.name_languages = ["*"]
            opts.glyph_names = False
            opts.notdef_outline = True
            opts.drop_tables += ["FFTM", "TeX ", "BDF "]
            font = subset.load_font(io.BytesIO(raw), opts)
            sub = subset.Subsetter(opts)
            sub.populate(unicodes=unicodes)
            sub.subset(font)
            subset.save_font(font, OUT / dst, opts)

            out = TTFont(OUT / dst)
            names = " ".join(r.toUnicode() for r in out["name"].names)
            assert RESERVED_NAME not in names, f"{dst}: reserved font name present"
            assert out["name"].getDebugName(13), f"{dst}: licence string missing"
            cmap = out.getBestCmap()
            missing = "".join(ch for ch in WANTED_MATHS if ord(ch) not in cmap)
            fam, sty = out["name"].getDebugName(1), out["name"].getDebugName(2)
            print(f"{dst:30s} {(OUT / dst).stat().st_size:6d} B  {fam} {sty:8s}"
                  f"  {len(cmap):4d} code points   not in face: {missing or '-'}")


if __name__ == "__main__":
    main()
