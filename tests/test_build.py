"""Tests for the build pipeline. Standard library only (plus ruamel.yaml, which the build needs anyway).

    python -m unittest discover -s tests -v

A fake GitHub server on localhost stands in for api.github.com, so these run offline and never use a real token.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build  # noqa: E402
import thumbs  # noqa: E402

SKELETON = """\
# a comment the user wrote, which must survive every sync
site:
  name: Test Person
  title: Test Person
  description: A test.
  url: https://tester.github.io/
  github_user: tester
  site_repo: tester-site
  font: mixed
  language: en
about:
  hello: Hi!
  paragraphs:
    - "[TODO: say something.]"
  contact:
    email: {user: someone, domain: example.org}
    links:
      - {label: GitHub, url: "https://github.com/tester", text: tester}
      - {label: LinkedIn, url: ""}
sections:
  - {id: projects, title: Projects}
  - {id: writing, title: Writing}
  - {id: fun, title: Fun}
force_include: []
entries: []
excluded: []
"""


def repo(name, pushed, **kw):
    r = {"name": name, "full_name": f"tester/{name}", "html_url": f"https://github.com/tester/{name}", "description": None,
         "fork": False, "archived": False, "private": False, "size": 10, "language": "Python", "stargazers_count": 0,
         "topics": [], "homepage": None, "has_pages": False, "default_branch": "main", "pushed_at": pushed,
         "owner": {"login": "tester"}}
    r.update(kw)
    return r


class Fake:
    """State of the fake GitHub."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.repos = [
            repo("alpha", "2026-03-01T00:00:00Z", description="A toy game", topics=["game"]),
            repo("beta", "2026-02-01T00:00:00Z", description="Research on a dataset"),
            repo("gamma", "2026-01-01T00:00:00Z", size=0),                      # size 0 but has commits: not empty
            repo("delta", "2026-01-02T00:00:00Z", fork=True),
            repo("epsilon", "2026-01-03T00:00:00Z", archived=True),
            repo("zeta", "2026-01-04T00:00:00Z", size=0),                       # really empty
            repo("secret-project-xyz", "2026-01-05T00:00:00Z", private=True),
            repo("tester", "2026-01-06T00:00:00Z"),                             # profile README repo
            repo("tester-site", "2026-01-07T00:00:00Z"),                        # this site
            repo("theta", "2025-12-01T00:00:00Z", has_pages=True),              # no README, has Pages
        ]
        self.readmes = {"alpha": "# alpha\nJust a fun little game.", "beta": "# beta\nA research paper and benchmark."}
        self.empty = {"zeta"}
        self.pinned = ["beta"]
        self.valid_tokens = {"good"}
        self.mode = None            # "rate_limit" makes every call fail with 403
        self.page_size = 3
        self.log = []


FAKE = Fake()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        auth = self.headers.get("Authorization")
        FAKE.log.append((self.command, self.path, auth))
        if FAKE.mode == "rate_limit":
            self._send(403, {"message": "API rate limit exceeded"}, headers={"X-RateLimit-Remaining": "0"})
            return False
        if auth and auth.removeprefix("Bearer ") not in FAKE.valid_tokens:
            self._send(401, {"message": "Bad credentials"})
            return False
        return True

    def do_GET(self):
        if not self._auth_ok():
            return
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        parts = u.path.strip("/").split("/")
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if parts[:1] == ["users"] and parts[-1] == "repos":
            page = int(q.get("page", ["1"])[0])
            n = FAKE.page_size
            chunk = FAKE.repos[(page - 1) * n: page * n]
            headers = {}
            if page * n < len(FAKE.repos):
                headers["Link"] = f'<{base}{u.path}?per_page=100&type=owner&page={page + 1}>; rel="next"'
            return self._send(200, chunk, headers=headers)
        if parts[0] == "repos" and parts[3] == "readme":
            text = FAKE.readmes.get(parts[2])
            return self._send(200, text, "text/plain") if text is not None else self._send(404, {"message": "Not Found"})
        if parts[0] == "repos" and parts[3] == "commits":
            return self._send(409, {"message": "Git Repository is empty."}) if parts[2] in FAKE.empty else self._send(200, [{}])
        self._send(404, {"message": "Not Found"})

    def do_POST(self):
        if not self._auth_ok():
            return
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        nodes = [{"name": n, "owner": {"login": "tester"}} for n in FAKE.pinned]
        nodes.append({"name": "someone-elses", "owner": {"login": "other"}})
        self._send(200, {"data": {"user": {"pinnedItems": {"nodes": nodes}}}})


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        FAKE.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.content = self.dir / "projects.yaml"
        self.content.write_text(SKELETON, encoding="utf-8")
        self.out = self.dir / "index.html"

    def run_build(self, *args, token=None):
        env = {"GITHUB_API_URL": self.base, "GITHUB_GRAPHQL_URL": self.base + "/graphql"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if token:
                os.environ["GITHUB_TOKEN"] = token
            else:
                os.environ.pop("GITHUB_TOKEN", None)
            code = build.main(["--content", str(self.content), "--out", str(self.out), *args])
        return code, out.getvalue(), err.getvalue()

    def load(self):
        return build.yaml_io().load(self.content.read_text(encoding="utf-8"))

    def entry(self, name):
        return next(e for e in self.load()["entries"] if e["id"] == name)

    # ---- sync ----------------------------------------------------------------------------------
    def test_paging_exclusions_and_privacy(self):
        code, out, err = self.run_build()
        self.assertEqual(code, 0, err)
        data = self.load()
        self.assertEqual(sorted(e["id"] for e in data["entries"]), ["alpha", "beta", "gamma", "theta"])
        reasons = {x["name"]: x["reason"] for x in data["excluded"]}
        self.assertEqual(reasons, {"delta": "fork", "epsilon": "archived", "zeta": "empty",
                                   "tester": "profile README repo", "tester-site": "this site's own repo"})
        text = self.content.read_text(encoding="utf-8") + self.out.read_text(encoding="utf-8") + out + err
        self.assertNotIn("secret-project-xyz", text, "a private repo's name must never be written anywhere")
        self.assertIn("1 private skipped", out)
        paths = [p for _, p, _ in FAKE.log if "/users/tester/repos" in p]
        self.assertEqual(len(paths), 4, "10 repos at 3 per page is 4 requests")
        self.assertTrue(paths[0].startswith("/users/tester/repos?per_page=100&type=owner"))

    def test_no_token_means_no_auth_header_and_no_graphql(self):
        self.run_build()
        self.assertTrue(all(auth is None for _, _, auth in FAKE.log))
        self.assertFalse([1 for m, p, _ in FAKE.log if m == "POST"])

    def test_fields_collected(self):
        self.run_build()
        e = self.entry("alpha")
        gh = e["github"]
        self.assertEqual((gh["name"], gh["url"], gh["description"], list(gh["topics"]), gh["language"], gh["pushed_at"], gh["stars"]),
                         ("alpha", "https://github.com/tester/alpha", "A toy game", ["game"], "Python", "2026-03-01T00:00:00Z", 0))
        self.assertIn("fun little game", gh["readme"])
        self.assertEqual(self.entry("theta")["github"]["readme"], "", "a repo without a README gets an empty string")

    def test_section_is_proposed_from_readme_and_flagged(self):
        self.run_build()
        self.assertEqual((self.entry("alpha")["section"], self.entry("alpha")["section_status"]), ("fun", "proposed"))
        self.assertEqual((self.entry("beta")["section"], self.entry("beta")["section_status"]), ("projects", "proposed"))

    def test_force_include(self):
        self.run_build()
        text = self.content.read_text(encoding="utf-8").replace("force_include: []", "force_include: [delta]")
        self.content.write_text(text, encoding="utf-8")
        self.run_build()
        data = self.load()
        self.assertIn("delta", [e["id"] for e in data["entries"]])
        self.assertNotIn("delta", [x["name"] for x in data["excluded"]])

    def test_hand_edits_survive_a_resync(self):
        self.run_build()
        text = self.content.read_text(encoding="utf-8")
        text = text.replace("title: Alpha", "title: My Own Title")
        text = re.sub(r"(  - id: alpha\n    section: )fun", r"\1projects", text)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    blurb: )>-\n(?:      .*\n)+", r"\1Hand written blurb.\n", text, count=1)
        text = text.replace("blurb_status: draft", "blurb_status: final", 1)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    order:)\n", r"\1 7\n", text, count=1)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    hidden: )false", r"\1true", text, count=1)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    built_with: )''", r"\1Lua, love2d", text, count=1)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    links: )\[\]", r"\1[{label: Demo, url: 'https://example.org/demo'}]", text, count=1)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    thumb: ).*", r"\1thumbs/mine.svg", text, count=1)
        text = text.replace("force_include: []", "# keep this comment\nforce_include: []")
        self.content.write_text(text, encoding="utf-8")
        before = self.entry("alpha")
        keep = {k: before[k] for k in ("title", "section", "blurb", "blurb_status", "order", "hidden", "built_with", "thumb")}
        keep["links"] = [dict(x) for x in before["links"]]
        self.assertEqual(keep["blurb"], "Hand written blurb.")

        FAKE.repos[0] = repo("alpha", "2026-04-01T00:00:00Z", description="Changed upstream", stargazers_count=42, language="Rust")
        code, _, err = self.run_build()
        self.assertEqual(code, 0, err)
        after = self.entry("alpha")
        for k, v in keep.items():
            self.assertEqual(after[k] if k != "links" else [dict(x) for x in after[k]], v, f"{k} was overwritten")
        gh = after["github"]
        self.assertEqual((gh["description"], gh["stars"], gh["language"], gh["pushed_at"]), ("Changed upstream", 42, "Rust", "2026-04-01T00:00:00Z"))
        final = self.content.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", final)
        self.assertIn("# a comment the user wrote", final)

    def test_second_run_changes_nothing(self):
        self.run_build()
        first = self.content.read_bytes()
        _, out, _ = self.run_build()
        self.assertEqual(first, self.content.read_bytes())
        self.assertIn("already up to date", out)

    def test_removed_repo_keeps_its_entry_with_a_warning(self):
        self.run_build()
        FAKE.repos = [r for r in FAKE.repos if r["name"] != "beta"]
        code, _, err = self.run_build()
        self.assertEqual(code, 0)
        self.assertIn("beta was not in the GitHub listing", err)
        self.assertIn("beta", [e["id"] for e in self.load()["entries"]])

    # ---- token, pinned, failures ---------------------------------------------------------------
    def test_token_unlocks_pinned_and_ordering(self):
        self.run_build(token="good")
        self.assertTrue(any(m == "POST" for m, _, _ in FAKE.log))
        self.assertTrue(all(auth == "Bearer good" for _, _, auth in FAKE.log))
        beta, alpha = self.entry("beta")["github"], self.entry("alpha")["github"]
        self.assertEqual((beta["pinned"], beta["pinned_rank"], alpha["pinned"]), (True, 0, False))
        # make alpha and beta share a section; the pinned one (older) must come first
        text = self.content.read_text(encoding="utf-8")
        text = re.sub(r"(id: alpha\n    section: )fun", r"\1projects", text)
        self.content.write_text(text, encoding="utf-8")
        self.run_build(token="good")
        html = self.out.read_text(encoding="utf-8")
        self.assertLess(html.index('id="beta"'), html.index('id="alpha"'))

    def test_without_token_pinned_flags_are_kept(self):
        self.run_build(token="good")
        self.run_build()                       # no token this time
        self.assertTrue(self.entry("beta")["github"]["pinned"])

    def test_without_pins_order_is_most_recently_pushed_first(self):
        self.run_build()
        text = re.sub(r"(id: alpha\n    section: )fun", r"\1projects", self.content.read_text(encoding="utf-8"))
        self.content.write_text(text, encoding="utf-8")
        self.run_build()
        html = self.out.read_text(encoding="utf-8")
        self.assertLess(html.index('id="alpha"'), html.index('id="beta"'))      # alpha pushed 03-01, beta 02-01

    def test_manual_order_beats_everything(self):
        self.run_build(token="good")
        text = self.content.read_text(encoding="utf-8")
        text = re.sub(r"(id: alpha\n    section: )fun", r"\1projects", text)
        text = re.sub(r"(id: alpha\n(?:.*\n)*?    order:)\n", r"\1 1\n", text, count=1)
        self.content.write_text(text, encoding="utf-8")
        self.run_build(token="good")
        html = self.out.read_text(encoding="utf-8")
        self.assertLess(html.index('id="alpha"'), html.index('id="beta"'))      # beta is pinned, alpha has order: 1

    def test_rejected_token_falls_back_to_unauthenticated(self):
        code, out, err = self.run_build(token="expired")
        self.assertEqual(code, 0, err)
        self.assertIn("GITHUB_TOKEN was rejected", err)
        self.assertEqual(sorted(e["id"] for e in self.load()["entries"]), ["alpha", "beta", "gamma", "theta"])

    def test_api_failure_leaves_the_yaml_alone(self):
        self.run_build()
        before = self.content.read_bytes()
        FAKE.mode = "rate_limit"
        code, _, err = self.run_build()
        self.assertEqual(code, 1)
        self.assertIn("rate limit", err)
        self.assertIn("GITHUB_TOKEN", err)
        self.assertEqual(before, self.content.read_bytes())

    def test_offline_never_touches_the_network(self):
        self.run_build()
        FAKE.log.clear()
        code, out, _ = self.run_build("--offline")
        self.assertEqual(code, 0)
        self.assertEqual(FAKE.log, [])

    # ---- render --------------------------------------------------------------------------------
    def render(self, **kw):
        data = self.load()
        with contextlib.redirect_stderr(io.StringIO()):
            return build.render(data, kw.get("font"))

    def test_empty_sections_are_hidden_and_appear_when_filled(self):
        self.run_build()
        html, stats = self.render()
        self.assertIn('id="projects"', html)
        self.assertNotIn('id="writing"', html)
        self.assertNotIn(">Writing<", html)
        text = self.content.read_text(encoding="utf-8").replace("entries:", """entries:
  - id: first-note
    section: writing
    title: A Note
    blurb: Plain words.
    blurb_status: final
    links: [{label: Paper, url: "https://example.org/n.pdf"}]
    thumb: thumbs/first-note.svg""", 1)
        self.content.write_text(text, encoding="utf-8")
        html, stats = self.render()
        self.assertIn('<h2 id="h-writing">Writing</h2>', html)
        self.assertLess(html.index('id="projects"'), html.index('id="writing"'), "sections keep the order of sections:")
        self.assertNotIn("[draft]", html.split('id="writing"')[1].split("</section>")[0])

    def test_zigzag_alternates_across_sections(self):
        self.run_build()
        html, _ = self.render()
        sides = re.findall(r'class="entry entry--(left|right)"', html)
        self.assertEqual(sides[0], "left", "the About photo is on the left, as in the reference")
        self.assertEqual(sides, ["left" if i % 2 == 0 else "right" for i in range(len(sides))])

    def test_email_is_not_in_the_page_source(self):
        self.run_build()
        html, _ = self.render()
        self.assertNotIn("someone@example.org", html)
        self.assertNotRegex(html, r"[\w.+-]+@[\w-]+\.[\w.]+")
        self.assertIn('data-user="someone" data-domain="example.org"', html)
        self.assertIn("someone [at] example [dot] org", html)
        self.assertNotIn("mailto:", html.replace("a.href = 'mailto:'", ""))

    def test_user_text_is_escaped_and_unsafe_links_refused(self):
        self.run_build()
        text = self.content.read_text(encoding="utf-8")
        text = re.sub(r"(id: beta\n(?:.*\n)*?    blurb: )>-\n(?:      .*\n)+", r'\1"<script>alert(1)</script> & \\"quoted\\" [ok](https://example.org/a?b=1&c=2)"\n', text, count=1)
        self.content.write_text(text, encoding="utf-8")
        html, _ = self.render()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp;", html)
        self.assertIn('<a href="https://example.org/a?b=1&amp;c=2">ok</a>', html)
        bad = self.content.read_text(encoding="utf-8").replace("https://example.org/a?b=1&c=2", "javascript:alert(1)")
        self.content.write_text(bad, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.render()

    def test_drafts_and_todos_are_marked(self):
        self.run_build()
        html, stats = self.render()
        self.assertEqual(stats["drafts"], 4)
        self.assertEqual(html.count('class="draft"'), 4)
        self.assertGreaterEqual(stats["todos"], 3)       # about paragraph, LinkedIn, and the placeholder blurbs

    def test_font_presets(self):
        self.run_build()
        for preset, faces in build.PRELOADS.items():
            html, _ = self.render(font=preset)
            self.assertIn(f'data-font="{preset}"', html)
            self.assertEqual(re.findall(r'rel="preload" href="fonts/([\w-]+)\.woff2"', html), faces)
            self.assertTrue(all((ROOT / "fonts" / f"{f}.woff2").exists() for f in faces))
        html, _ = self.render()
        self.assertIn('data-font="mixed"', html, "default preset")

    def test_page_basics(self):
        self.run_build()
        html, _ = self.render()
        for needle in ('<html lang="en"', '<meta name="viewport"', "<title>Test Person</title>", '<meta name="description"',
                       'property="og:title"', 'property="og:image" content="https://tester.github.io/og.png"',
                       'rel="canonical" href="https://tester.github.io/"', 'rel="icon"', '<h1>Test Person</h1>'):
            self.assertIn(needle, html)
        self.assertEqual(len(re.findall(r"<img ", html)), len(re.findall(r'<img [^>]*alt="[^"]+"', html)), "alt text on every image")
        self.assertNotRegex(html, r'https?://(?!github\.com|tester\.github\.io|example\.org|www\.w3\.org)', "no third-party hosts")

    def test_duplicate_ids_and_unknown_sections_stop_the_build(self):
        self.run_build()
        text = self.content.read_text(encoding="utf-8")
        self.content.write_text(text.replace("section: fun", "section: nonsense", 1), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.render()

    def test_pretty_title(self):
        self.assertEqual(build.pretty_title("naive-discrete-fourier-transform"), "Naive Discrete Fourier Transform")
        self.assertEqual(build.pretty_title("Naive-Discrete-Fourier-Transform"), "Naive Discrete Fourier Transform")
        self.assertEqual(build.pretty_title("iOS-notes"), "iOS notes")


class ThumbTest(unittest.TestCase):
    PALETTE = {thumbs.BG, thumbs.WHITE, *thumbs.REDS, "none"}

    def test_deterministic(self):
        for name in ("Some-Repo", "another_one", "Eliza", "Stryder"):
            self.assertEqual(thumbs.render(name), thumbs.render(name))

    def test_names_get_different_pictures(self):
        pics = {thumbs.render(n) for n in ("a1", "b2", "c3", "d4", "e5", "f6", "g7", "h8")}
        self.assertGreater(len(pics), 6)

    def test_house_style(self):
        names = list(thumbs.BESPOKE) + ["x1", "y2", "z3", "w4", "v5", "u6", "t7", "s8", "r9"]
        styles = set()
        for name in names:
            svg = thumbs.render(name)
            ET.fromstring(svg)                                                   # well-formed
            self.assertIn('viewBox="0 0 150 150"', svg)
            self.assertIn('width="150" height="150"', svg)
            self.assertNotIn("<text", svg, "no text inside thumbnails")
            self.assertNotIn("<image", svg, "no embedded bitmaps")
            used = set(re.findall(r'(?:fill|stroke)="([^"]+)"', svg))
            self.assertLessEqual(used, self.PALETTE, f"{name} strays from the palette: {used - self.PALETTE}")
            self.assertEqual(svg.count(f'fill="{thumbs.BG}"'), 1, "one background, once")
            self.assertLess(len(svg), 20000)
            self.assertTrue(thumbs.describe(name))
            styles.add(thumbs.style_of(name))
        self.assertTrue({"fan", "bundle", "contour", "scatter", "rose"} & styles)

    def test_committed_thumbnails_match_the_generator(self):
        for p in sorted((ROOT / "thumbs").glob("*.svg")):
            text = p.read_text(encoding="utf-8")
            if thumbs.MARK in text[:400]:
                self.assertEqual(text, thumbs.render(p.stem), f"{p.name} is stale: run python scripts/thumbs.py")

    def test_hand_made_files_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "mine.svg"
            f.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
            self.assertIn("kept", thumbs.write(f, thumbs.render("mine"), force=False))
            self.assertEqual(f.read_text(encoding="utf-8"), "<svg xmlns='http://www.w3.org/2000/svg'/>")
            thumbs.write(f, thumbs.render("mine"), force=True)
            self.assertIn(thumbs.MARK, f.read_text(encoding="utf-8"))

    def test_real_data_is_really_used(self):
        f, mags = thumbs._ndft_data()
        self.assertEqual([round(m) for m in mags[:7]], [3, 12, 1, 0, 15, 0, 100])        # the repo's own amplitudes
        self.assertEqual(len(thumbs.STRYDER_LOSS), 100)
        self.assertEqual((thumbs.STRYDER_LOSS[0], thumbs.STRYDER_LOSS[-1]), (1.7642, 0.0039))


class ShippedContentTest(unittest.TestCase):
    """The real content file."""

    def setUp(self):
        self.data = build.yaml_io().load((ROOT / "content" / "projects.yaml").read_text(encoding="utf-8"))

    def test_every_entry_has_what_the_page_needs(self):
        for e in self.data["entries"]:
            self.assertTrue(e.get("title") and e.get("blurb") and e.get("section"), e.get("id"))
            self.assertTrue((ROOT / str(e["thumb"])).exists(), f"{e['thumb']} missing")
            self.assertIn(e.get("blurb_status"), ("draft", "final"))
            self.assertIn(e.get("section_status"), ("proposed", "confirmed"))

    def test_index_html_is_current(self):
        page, _ = build.render(self.data)
        self.assertEqual(page, (ROOT / "index.html").read_text(encoding="utf-8"), "index.html is stale: run python scripts/build.py --offline")


if __name__ == "__main__":
    unittest.main()
