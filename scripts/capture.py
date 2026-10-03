#!/usr/bin/env python3
"""PNG assets and screenshots, made with headless Chromium through Playwright (developer tool).

    python scripts/capture.py assets                      # favicon.png, apple-touch-icon.png, og.png
    python scripts/capture.py screenshots                 # docs/screenshots/<preset>-<width>.png, 3 presets x 2 widths
    python scripts/capture.py compare --reference DIR     # docs/screenshots/compare-1440.png, next to the reference site

`screenshots` also fails if a page logs a console message, has a failed request, scrolls sideways at 320px,
or shows text in a face other than the one its preset asks for.
Needs: pip install -r requirements-dev.txt. If Playwright cannot find its own browser, set CHROMIUM=/path/to/chrome.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import os
import pathlib
import shutil
import sys
import tempfile
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

SITE_FILES = ["styles.css", "favicon.svg", "favicon.png", "apple-touch-icon.png", "og.png"]
SITE_DIRS = ["fonts", "thumbs"]
PRESETS = ("paper", "terminal", "mixed")
EXPECT = {   # CSS family that the body and the h1 should really be drawn in, per preset
    "paper": ("CMU Concrete", "CMU Concrete"),
    "terminal": ("CMU Typewriter Text", "CMU Typewriter Text"),
    "mixed": ("CMU Concrete", "CMU Typewriter Text"),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".woff2": "font/woff2", ".svg": "image/svg+xml", ".ttf": "font/ttf", "": "application/octet-stream"}

    def log_message(self, *a):   # quiet
        pass


@contextlib.contextmanager
def serve(directory: pathlib.Path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@contextlib.contextmanager
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {}
        if os.environ.get("CHROMIUM"):
            kw["executable_path"] = os.environ["CHROMIUM"]
        elif pathlib.Path("/opt/pw-browsers/chromium").exists():
            kw["executable_path"] = "/opt/pw-browsers/chromium"
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            kw["args"] = ["--no-sandbox"]
        b = p.chromium.launch(**kw)
        try:
            yield b
        finally:
            b.close()


def stage(preset: str | None, dest: pathlib.Path) -> pathlib.Path:
    """Copy the deployable files into dest and render index.html there with the given font preset."""
    import build
    for f in SITE_FILES:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, dest / f)
    for d in SITE_DIRS:
        shutil.copytree(ROOT / d, dest / d, dirs_exist_ok=True)
    data = build.yaml_io().load(build.CONTENT.read_text(encoding="utf-8"))
    page, _ = build.render(data, preset)
    (dest / "index.html").write_text(page, encoding="utf-8")
    return dest


def new_page(b, width: int, height: int = 900, scale: int = 1):
    ctx = b.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
    page = ctx.new_page()
    problems: list[str] = []
    page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    page.on("requestfailed", lambda r: problems.append(f"request failed: {r.url} ({r.failure})"))
    page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 else None)
    return ctx, page, problems


# ---- assets ---------------------------------------------------------------------------------
def cmd_assets(args) -> None:
    import thumbs
    with tempfile.TemporaryDirectory() as tmp, browser() as b:
        tmp = pathlib.Path(tmp)
        (tmp / "icon.svg").write_text(thumbs.favicon(True), encoding="utf-8")
        (tmp / "icon-square.svg").write_text(thumbs.favicon(False), encoding="utf-8")
        with serve(tmp) as base:
            for name, src, size, transparent in (("favicon.png", "icon.svg", 32, True),
                                                 ("apple-touch-icon.png", "icon-square.svg", 180, False)):
                ctx, page, _ = new_page(b, size, size)
                page.set_content(f'<body style="margin:0"><img src="{base}/{src}" width="{size}" height="{size}" style="display:block">')
                page.wait_for_load_state("networkidle")
                page.screenshot(path=str(ROOT / name), omit_background=transparent)
                ctx.close()
        print("favicon.png, apple-touch-icon.png: written")

        site = stage(None, pathlib.Path(tempfile.mkdtemp(dir=tmp)))
        import build
        data = build.yaml_io().load(build.CONTENT.read_text(encoding="utf-8"))
        thumbs_used = [str(e.get("thumb") or f"thumbs/{build.gh_name(e)}.svg") for e in sorted(
            (e for e in data["entries"] if not e.get("hidden")), key=lambda e: (e.get("section") != "projects", build.sort_key(e)))][:6]
        cells = "".join(f'<img src="{t}" width="186" height="186">' for t in thumbs_used)
        name = str(data["site"]["name"]).replace(" ", "<br>")
        (site / "og.html").write_text(f"""<!doctype html><html data-font="mixed"><head><meta charset="utf-8">
<link rel="icon" href="data:,"><link rel="stylesheet" href="styles.css"><style>
body{{margin:0;padding:0;width:1200px;height:630px;display:flex;align-items:center;gap:64px;padding:0 72px;box-sizing:border-box;overflow:hidden}}
.l{{flex:1}} h1{{font-size:84px;line-height:1.08;text-align:left;margin:0 0 28px}}
.rule{{border-bottom:3px solid #000;width:100%;margin-bottom:22px}} p{{font-size:28px;margin:0;line-height:1.3}}
.g{{display:grid;grid-template-columns:repeat(3,186px);gap:16px}} .g img{{border-radius:10px;display:block}}
</style></head><body><div class="l"><h1>{name}</h1><div class="rule"></div><p>Projects, writing and experiments.</p></div>
<div class="g">{cells}</div></body></html>""", encoding="utf-8")
        with serve(site) as base:
            ctx, page, problems = new_page(b, 1200, 630)
            page.goto(f"{base}/og.html")
            page.evaluate("document.fonts.ready")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(ROOT / "og.png"))
            ctx.close()
        print("og.png: written", "" if not problems else problems)


# ---- screenshots ----------------------------------------------------------------------------
def cmd_screenshots(args) -> None:
    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    failures = 0
    with browser() as b:
        for preset in PRESETS:
            with tempfile.TemporaryDirectory() as tmp:
                site = stage(preset, pathlib.Path(tmp))
                with serve(site) as base:
                    for width in (1440, 390):
                        ctx, page, problems = new_page(b, width)
                        page.goto(f"{base}/index.html")
                        page.evaluate("document.fonts.ready")
                        page.wait_for_load_state("networkidle")
                        info = page.evaluate("""() => ({
                            body: getComputedStyle(document.body).fontFamily, h1: getComputedStyle(document.querySelector('h1')).fontFamily,
                            scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
                            loaded: [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family + ' ' + f.weight)})""")
                        want_body, want_h1 = EXPECT[preset]
                        if want_body not in info["body"] or want_h1 not in info["h1"]:
                            problems.append(f"font mismatch: body={info['body']} h1={info['h1']}")
                        if info["scrollW"] > info["innerW"]:
                            problems.append(f"horizontal overflow: {info['scrollW']} > {info['innerW']}")
                        page.screenshot(path=str(out / f"{preset}-{width}.png"), full_page=True)
                        print(f"{preset}-{width}.png  faces loaded: {', '.join(info['loaded'])}" + (f"   PROBLEMS: {problems}" if problems else ""))
                        failures += bool(problems)
                        ctx.close()
                    ctx, page, problems = new_page(b, 320)         # the narrowest phone we promise to support
                    page.goto(f"{base}/index.html")
                    page.evaluate("document.fonts.ready")
                    w = page.evaluate("document.documentElement.scrollWidth")
                    if w > 320:
                        print(f"{preset} @320: horizontal overflow ({w}px)")
                        failures += 1
                    ctx.close()
    if failures:
        sys.exit(f"{failures} problem(s)")
    print("no console messages, failed requests, wrong fonts or sideways scrolling")


# ---- side by side with the reference --------------------------------------------------------
def cmd_compare(args) -> None:
    ref = pathlib.Path(args.reference).resolve()
    if not (ref / "index.html").exists():
        sys.exit(f"{ref} has no index.html")
    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    with browser() as b, tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        with serve(ref) as base:
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.route("**/*", lambda route, req: route.continue_() if req.url.startswith(base) else route.abort())
            page.goto(f"{base}/index.html")
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(400)
            page.screenshot(path=str(tmp / "ref.png"), full_page=True)
            ctx.close()
        site = stage("mixed", pathlib.Path(tempfile.mkdtemp(dir=tmp)))
        with serve(site) as base:
            ctx, page, _ = new_page(b, 1440)
            page.goto(f"{base}/index.html")
            page.evaluate("document.fonts.ready")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(tmp / "mine.png"), full_page=True)
            ctx.close()
        (tmp / "cmp.html").write_text("""<!doctype html><meta charset="utf-8"><body style="margin:0;padding:24px;background:#e8e8e8;font:20px monospace">
<div style="display:flex;gap:24px;align-items:flex-start">
<figure style="margin:0"><figcaption style="margin:0 0 8px">Reference: gabgoh.github.io (1440px)</figcaption><img src="ref.png" style="display:block;background:#fff"></figure>
<figure style="margin:0"><figcaption style="margin:0 0 8px">This site, mixed preset (1440px)</figcaption><img src="mine.png" style="display:block;background:#fff"></figure>
</div></body>""", encoding="utf-8")
        with serve(tmp) as base:
            ctx = b.new_context(viewport={"width": 2976, "height": 900})
            page = ctx.new_page()
            page.goto(f"{base}/cmp.html")
            page.wait_for_load_state("networkidle")
            page.screenshot(path=str(out / "compare-1440.png"), full_page=True)
            ctx.close()
    print("docs/screenshots/compare-1440.png: written")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("assets").set_defaults(fn=cmd_assets)
    sub.add_parser("screenshots").set_defaults(fn=cmd_screenshots)
    c = sub.add_parser("compare")
    c.add_argument("--reference", required=True, help="path to a local clone of the reference site")
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
