#!/usr/bin/env python3
"""Build the site.

    python scripts/build.py              # refresh GitHub data in content/projects.yaml, then write index.html
    python scripts/build.py --offline    # no network: just render index.html from the YAML (what CI does)

Step 1 (sync). Calls the GitHub REST API, GET /users/{user}/repos?per_page=100&type=owner, following
pages. A GITHUB_TOKEN in the environment is used if present (and unlocks pinned-repo ordering through
GraphQL); the build works without one. Forks, archived, empty, private, profile-README and site repos are
listed under `excluded:` and not shown, unless you add their name to `force_include:`.

The sync only ever rewrites each entry's `github:` block (and adds entries for new repos). Everything you
write by hand (blurb, section, order, thumb, hidden, links, built_with, title) is never touched.

Step 2 (render). Turns the YAML into index.html through scripts/template.html. Sections with no visible
entries are left out, so a new Writing entry makes its section appear on the next build.

Needs: pip install -r requirements.txt   (ruamel.yaml; everything else is the standard library)
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import pathlib
import re
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content" / "projects.yaml"
TEMPLATE = ROOT / "scripts" / "template.html"
DEFAULT_OUT = ROOT / "index.html"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

README_LIMIT = 6000
FONT_PRESETS = ("paper", "terminal", "mixed")
PRELOADS = {   # faces that appear above the fold in each preset (the headings are bold)
    "paper": ["roboto-serif-display", "roboto-serif-text"],
    "terminal": ["space-mono-regular", "space-mono-bold"],
    "mixed": ["roboto-serif-display", "space-mono-regular", "space-mono-bold"],
}


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# =============================================================================================
# GitHub client (standard library only)
# =============================================================================================
class ApiError(Exception):
    def __init__(self, status: int, message: str, headers=None):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.headers = headers or {}


def _api_message(body: str) -> str:
    try:
        return json.loads(body).get("message") or body[:200]
    except (ValueError, AttributeError):
        return body[:200]


class GitHub:
    def __init__(self, api_base: str, token: str | None = None, graphql_url: str | None = None):
        self.api = api_base.rstrip("/")
        self.graphql_url = graphql_url or self.api + "/graphql"
        self.token = token or None

    def _open(self, url: str, data: bytes | None = None, accept: str = "application/vnd.github+json"):
        headers = {"Accept": accept, "User-Agent": "portfolio-build", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code >= 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                msg = _api_message(body)
                if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
                    msg += " (rate limit reached; set GITHUB_TOKEN to raise it)"
                raise ApiError(e.code, msg, e.headers) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(0, f"could not reach {url}: {getattr(e, 'reason', e)}") from None
        raise ApiError(0, f"could not reach {url}")

    def get(self, path: str, accept: str = "application/vnd.github+json"):
        return self._open(self.api + path, accept=accept)

    def paginate(self, path: str) -> list:
        items, url = [], self.api + path
        while url:
            _, headers, body = self._open(url)
            items.extend(json.loads(body))
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or "")
            url = nxt.group(1) if nxt else None
        return items

    def graphql(self, query: str, variables: dict) -> dict:
        _, _, body = self._open(self.graphql_url, data=json.dumps({"query": query, "variables": variables}).encode())
        out = json.loads(body)
        if out.get("errors"):
            raise ApiError(200, out["errors"][0].get("message", "GraphQL error"))
        return out


def normalise(r: dict) -> dict:
    """Keep only what the site uses, with plain types."""
    return {
        "name": r["name"],
        "url": r.get("html_url") or "",
        "description": (r.get("description") or "").strip(),
        "topics": list(r.get("topics") or []),
        "language": r.get("language") or "",
        "homepage": (r.get("homepage") or "").strip(),
        "has_pages": r.get("has_pages"),
        "default_branch": r.get("default_branch") or "",
        "pushed_at": r.get("pushed_at") or "",
        "stars": r.get("stargazers_count") or 0,
        "private": bool(r.get("private")),
        "fork": bool(r.get("fork")),
        "archived": bool(r.get("archived")),
        "empty": bool(r.get("_empty")),
        "size": r.get("size") or 0,
        "readme": r.get("_readme"),
    }


def collect(gh: GitHub, user: str, want_readme) -> tuple[list[dict], list[str] | None]:
    """Fetch the repo list, then README text for the repos `want_readme(repo)` says we need."""
    path = f"/users/{urllib.parse.quote(user)}/repos?per_page=100&type=owner"
    try:
        raw = gh.paginate(path)
    except ApiError as e:
        if e.status == 401 and gh.token:
            warn("GITHUB_TOKEN was rejected (401); continuing without it")
            gh.token = None
            raw = gh.paginate(path)
        else:
            raise
    repos = [normalise(r) for r in raw]
    for repo in repos:
        if repo["private"]:
            continue
        if repo["size"] == 0:          # `size` is rounded to KB, so confirm a zero before calling it empty
            try:
                gh.get(f"/repos/{user}/{repo['name']}/commits?per_page=1")
            except ApiError as e:
                repo["empty"] = e.status == 409
        if want_readme(repo):
            try:
                _, _, body = gh.get(f"/repos/{user}/{repo['name']}/readme", accept="application/vnd.github.raw+json")
                repo["readme"] = body.decode("utf-8", "replace")
            except ApiError as e:
                if e.status != 404:
                    raise
                repo["readme"] = ""
    return repos, fetch_pinned(gh, user)


def fetch_pinned(gh: GitHub, user: str) -> list[str] | None:
    """Names of the repos pinned on the profile, in order. Needs a token; None means 'unknown'."""
    if not gh.token:
        return None
    query = ("query($login:String!){user(login:$login){pinnedItems(first:6,types:[REPOSITORY])"
             "{nodes{... on Repository{name owner{login}}}}}}")
    try:
        out = gh.graphql(query, {"login": user})
        nodes = out["data"]["user"]["pinnedItems"]["nodes"]
    except (ApiError, KeyError, TypeError) as e:
        warn(f"could not read pinned repos ({e}); keeping the order from the last successful run")
        return None
    return [n["name"] for n in nodes if n and n.get("owner", {}).get("login", "").lower() == user.lower()]


# =============================================================================================
# Rules: what is excluded, and which section a new repo is proposed for
# =============================================================================================
def exclusion_reason(repo: dict, user: str, site_repo: str) -> str | None:
    name = repo["name"].lower()
    if repo["private"]:
        return "private"
    if name == user.lower():
        return "profile README repo"
    if name in {site_repo.lower(), f"{user}.github.io".lower()}:
        return "this site's own repo"
    if repo["fork"]:
        return "fork"
    if repo["archived"]:
        return "archived"
    if repo["empty"]:
        return "empty"
    return None


FUN_WORDS = {"fun": 2, "toy": 2, "game": 3, "games": 3, "play": 1, "hobby": 2, "silly": 2, "joke": 2, "meme": 2,
             "bot": 1, "little": 1, "tiny": 1, "side project": 3, "for fun": 3, "experiment": 1, "just for": 2,
             "downloader": 1, "scraper": 1, "puzzle": 1, "demo": 1}
PROJECT_WORDS = {"research": 3, "paper": 2, "papers": 2, "dataset": 2, "benchmark": 3, "model": 1, "models": 1,
                 "neural": 2, "training": 2, "evaluation": 2, "framework": 2, "library": 2, "package": 1,
                 "algorithm": 2, "algorithms": 2, "analysis": 1, "theory": 1, "pipeline": 1, "architecture": 1,
                 "docker": 1, "deployment": 1, "deployed": 1, "regression": 1, "autoencoder": 2, "seq2seq": 2,
                 "transformer": 2, "embedding": 1, "latent": 1, "optimization": 2, "inference": 1, "essay": 1,
                 "api": 1, "production": 2, "system": 1}
FUN_TOPICS = {"game", "games", "fun", "toy", "hobby", "bot", "experiment", "art", "creative-coding"}
PROJECT_TOPICS = {"research", "machine-learning", "deep-learning", "nlp", "paper", "science", "library", "framework"}


def _score(text: str, words: dict[str, int]) -> int:
    total = 0
    for w, weight in words.items():
        total += weight * min(len(re.findall(r"\b" + re.escape(w) + r"\b", text)), 3)
    return total


def propose_section(repo: dict) -> str:
    """'fun' or 'projects', from README, description, name and topics. Ties go to projects."""
    text = " ".join([repo["name"].replace("-", " "), repo["description"], repo.get("readme") or ""]).lower()
    topics = set(repo["topics"])
    fun = _score(text, FUN_WORDS) + 3 * len(topics & FUN_TOPICS)
    proj = _score(text, PROJECT_WORDS) + 3 * len(topics & PROJECT_TOPICS)
    return "fun" if fun > proj else "projects"


def pretty_title(name: str) -> str:
    t = re.sub(r"[-_]+", " ", name).strip()
    return t.title() if t == t.lower() else t


# =============================================================================================
# Merge GitHub data into the YAML (round-trip, so comments and hand edits survive)
# =============================================================================================
def yaml_io():
    try:
        from ruamel.yaml import YAML
    except ImportError:
        sys.exit("ruamel.yaml is required: pip install -r requirements.txt")
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    return y


def _clean_readme(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
    if len(text) > README_LIMIT:
        text = text[:README_LIMIT].rsplit("\n", 1)[0] + "\n[... truncated]"
    return text + "\n" if text else ""


def gh_name(entry) -> str:
    gh = entry.get("github")
    return str(gh.get("name") if gh and gh.get("name") else entry.get("id") or "")


def update_github(entry, repo: dict, pinned: list[str] | None, source: str) -> None:
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ, LiteralScalarString as Lit
    gh = entry.get("github")
    if gh is None:
        gh = CommentedMap()
        entry["github"] = gh
    values = [("name", repo["name"]), ("url", repo["url"]), ("description", repo["description"]),
              ("topics", repo["topics"]), ("language", repo["language"]), ("homepage", repo["homepage"]),
              ("has_pages", repo["has_pages"]), ("default_branch", repo["default_branch"]),
              ("pushed_at", DQ(repo["pushed_at"]) if repo["pushed_at"] else ""), ("stars", repo["stars"])]
    for k, v in values:
        if k == "topics":                       # keep the flow style `[a, b]` readable
            from ruamel.yaml.comments import CommentedSeq
            seq = CommentedSeq(v)
            seq.fa.set_flow_style()
            gh[k] = seq
        else:
            gh[k] = v
    if pinned is not None:                      # no token this run: keep what we knew
        rank = pinned.index(repo["name"]) if repo["name"] in pinned else None
        gh["pinned"] = rank is not None
        gh["pinned_rank"] = rank
    else:
        gh.setdefault("pinned", False)
        gh.setdefault("pinned_rank", None)
    if repo.get("readme") is not None:
        text = _clean_readme(repo["readme"])
        gh["readme"] = Lit(text) if text else ""
    else:
        gh.setdefault("readme", "")
    gh["source"] = source


def new_entry(repo: dict, section: str):
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    from ruamel.yaml.scalarstring import FoldedScalarString as Fold
    e = CommentedMap()
    e["id"] = repo["name"]
    e["section"] = section
    e["section_status"] = "proposed"
    e["title"] = pretty_title(repo["name"])
    hint = f' GitHub says: "{repo["description"]}"' if repo["description"] else ""
    e["blurb"] = Fold("[TODO] Write 2 to 5 sentences: what it is, why you built it, and one specific detail." + hint)
    e["blurb_status"] = "draft"
    e["built_with"] = ""
    e["links"] = CommentedSeq()
    e["thumb"] = f"thumbs/{repo['name']}.svg"
    e["thumb_alt"] = ""
    e["order"] = None
    e["hidden"] = False
    e.yaml_set_comment_before_after_key("github", before="refreshed from GitHub on every build; edit the fields above, not these",
                                        indent=4)
    return e


def merge(data, repos: list[dict], pinned: list[str] | None, source: str, log=print) -> dict:
    """Apply repos to data in place. Returns counts for the summary."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq
    site = data["site"]
    user, site_repo = site["github_user"], site.get("site_repo", "")
    force = {str(f).lower() for f in (data.get("force_include") or [])}
    if data.get("entries") is None:
        data["entries"] = CommentedSeq()
    entries = data["entries"]
    by_name = {gh_name(e).lower(): e for e in entries}

    counts = {"added": 0, "updated": 0, "excluded": 0, "private": 0, "missing": 0}
    excluded = []
    seen = set()
    for repo in repos:
        key = repo["name"].lower()
        reason = exclusion_reason(repo, user, site_repo)
        if reason == "private":
            counts["private"] += 1            # never record the name of a private repo
            continue
        entry = by_name.get(key)
        if entry is None and reason and key not in force:
            excluded.append((repo, reason))
            counts["excluded"] += 1
            continue
        if entry is None:
            entry = new_entry(repo, propose_section(repo))
            entries.append(entry)
            by_name[key] = entry
            counts["added"] += 1
            log(f"  added   {repo['name']}  (proposed section: {entry['section']})")
        else:
            counts["updated"] += 1
            if reason and key not in force:
                warn(f"{repo['name']} is now {reason} on GitHub but has an entry, so it stays; set hidden: true to remove it")
        update_github(entry, repo, pinned, source)
        seen.add(key)

    for e in entries:
        if e.get("github") and gh_name(e).lower() not in seen:
            counts["missing"] += 1
            warn(f"{gh_name(e)} was not in the GitHub listing (renamed, deleted, or made private?); its entry is unchanged")

    old_notes = {str(x.get("name", "")).lower(): x.get("note") for x in (data.get("excluded") or []) if x.get("note")}
    new_excluded = CommentedSeq()
    for repo, reason in sorted(excluded, key=lambda t: t[0]["name"].lower()):
        m = CommentedMap()
        m["name"] = repo["name"]
        m["url"] = repo["url"]
        m["reason"] = reason
        m["description"] = repo["description"]
        if old_notes.get(repo["name"].lower()):
            m["note"] = old_notes[repo["name"].lower()]
        new_excluded.append(m)
    data["excluded"] = new_excluded
    return counts


# =============================================================================================
# Rendering
# =============================================================================================
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
TODO_RE = re.compile(r"\[(TODO[^\]]*)\]")


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def safe_url(u: str) -> str:
    u = str(u).strip()
    if re.match(r"^(https?:|mailto:|#|[\w./-]+$)", u, re.I):
        return u
    raise ValueError(f"unsafe or malformed URL in content: {u!r}")


def _fmt(s: str) -> str:
    s = html.escape(s, quote=False)
    s = BOLD_RE.sub(r"<b>\1</b>", s)
    return TODO_RE.sub(lambda m: f'<span class="todo">[{m.group(1)}]</span>', s)


def inline(text: str) -> str:
    """Escape text, then allow [label](url), **bold** and [TODO ...] markers. Nothing else."""
    out, pos = [], 0
    for m in LINK_RE.finditer(text):
        if m.group(1).startswith("TODO"):
            continue
        out.append(_fmt(text[pos:m.start()]))
        out.append(f'<a href="{esc(safe_url(m.group(2)))}">{_fmt(m.group(1))}</a>')
        pos = m.end()
    out.append(_fmt(text[pos:]))
    return "".join(out)


def paragraphs(text: str, tail: str = "") -> str:
    parts = [p.strip() for p in re.split(r"\n\s*\n", str(text).strip()) if p.strip()]
    parts = [" ".join(p.split()) for p in parts]
    html_parts = [inline(p) for p in parts]
    if html_parts:
        html_parts[-1] += tail
    return "\n".join(f"<p>{p}</p>" for p in html_parts)


def sort_key(e):
    gh = e.get("github") or {}
    order = e.get("order")
    if order is not None and order != "":
        return (0, float(order), 0, "", str(e.get("title", "")).lower())
    pushed = str(gh.get("pushed_at") or "")
    rank = gh.get("pinned_rank")
    pinned_first = 0 if (gh.get("pinned") and rank is not None) else 1
    # newest first: invert the ISO string character by character so a plain ascending sort works;
    # an entry with no date sorts last
    inverted = "".join(chr(0x10FFFF - ord(ch)) for ch in pushed) if pushed else chr(0x10FFFF)
    return (1, pinned_first, rank if pinned_first == 0 else 0, inverted, str(e.get("title", "")).lower())


def built_with(e) -> str:
    manual = str(e.get("built_with") or "").strip()
    if manual:
        return manual
    gh = e.get("github") or {}
    items, seen = [], set()
    for x in [gh.get("language")] + [str(t).replace("-", " ") for t in (gh.get("topics") or [])]:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            items.append(str(x))
    return ", ".join(items[:6])


def entry_links(e, user: str) -> list[tuple[str, str]]:
    gh = e.get("github") or {}
    links: list[tuple[str, str]] = []
    if gh.get("url"):
        links.append(("Code", gh["url"]))
    site = (gh.get("homepage") or "").strip()
    if not site and gh.get("has_pages") and gh.get("name"):
        site = f"https://{user.lower()}.github.io/{gh['name']}/"
    if site:
        links.append(("Site", site))
    for l in e.get("links") or []:
        if l.get("url"):
            links.append((str(l.get("label") or "Link"), str(l["url"])))
    seen, out = set(), []
    for label, url in links:
        if url.rstrip("/") not in seen:
            seen.add(url.rstrip("/"))
            out.append((label, url))
    return out


def render_entry(e, side: str, user: str, thumbs_mod) -> str:
    name = gh_name(e)
    links = entry_links(e, user)
    title = esc(e.get("title") or pretty_title(name))
    primary = next((u for lab, u in links if lab == "Site"), links[0][1] if links else "")
    title_html = f'<a href="{esc(safe_url(primary))}">{title}</a>' if primary else title
    status = str(e.get("blurb_status") or "").lower()
    tail = ' <span class="draft">[draft]</span>' if status == "draft" else ""
    body = paragraphs(e.get("blurb") or "", tail)
    bw = built_with(e)
    built = f'\n<p class="entry__built"><b>Built with:</b> {inline(bw)}</p>' if bw else ""
    link_html = ""
    if links:
        link_html = '\n<p class="entry__links">' + " ".join(f'<a href="{esc(safe_url(u))}">{esc(l)}</a>' for l, u in links) + "</p>"
    thumb = str(e.get("thumb") or f"thumbs/{name}.svg")
    alt = str(e.get("thumb_alt") or "").strip() or thumbs_mod.describe(name)
    if not (ROOT / thumb).exists():
        warn(f"{thumb} does not exist; run: python scripts/thumbs.py")
    anchor = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (f'<article class="entry entry--{side}" id="{esc(anchor)}">\n'
            f'<div class="entry__media"><img class="thumb" src="{esc(thumb)}" width="150" height="150" alt="{esc(alt)}"></div>\n'
            f'<div class="entry__text">\n<h3 class="entry__title">{title_html}</h3>\n{body}{built}{link_html}\n</div>\n</article>')


def email_parts(value) -> tuple[str, str] | None:
    if not value:
        return None
    if isinstance(value, str):
        u, _, d = value.partition("@")
        return (u, d) if u and d else None
    u, d = str(value.get("user") or ""), str(value.get("domain") or "")
    return (u, d) if u and d else None


def render_about(about, side: str = "left") -> tuple[str, bool]:
    """The About block. Returns (html, uses_email_script)."""
    contact = about.get("contact") or {}
    rows, uses_script = [], False
    em = email_parts(contact.get("email"))
    if em:
        uses_script = True
        u, d = em
        shown = f"{u} [at] {d.replace('.', ' [dot] ')}"
        rows.append(f'<b>Email:</b> <span class="email" data-user="{esc(u)}" data-domain="{esc(d)}">{esc(shown)}</span>')
    elif "email" in contact:
        rows.append('<b>Email:</b> <span class="todo">[TODO]</span>')
    for link in contact.get("links") or []:
        label, url = str(link.get("label") or ""), str(link.get("url") or "").strip()
        if url:
            text = str(link.get("text") or "") or (urllib.parse.urlparse(url).path.strip("/") or urllib.parse.urlparse(url).netloc)
            rows.append(f'<b>{esc(label)}:</b> <a href="{esc(safe_url(url))}">{esc(text)}</a>')
        else:
            rows.append(f'<b>{esc(label)}:</b> <span class="todo">[TODO]</span>')
    photo = str(about.get("photo") or "thumbs/photo-placeholder.svg")
    alt = str(about.get("photo_alt") or "Placeholder portrait")
    hello = f'<p class="hello">{inline(str(about.get("hello") or "Hi!"))}</p>\n' if about.get("hello", "Hi!") else ""
    paras = "\n".join(f"<p>{inline(' '.join(str(p).split()))}</p>" for p in (about.get("paragraphs") or []))
    contact_html = '\n<p class="contact">' + "<br>\n".join(rows) + "</p>" if rows else ""
    return (f'<section id="about" aria-labelledby="h-about">\n<h2 id="h-about">About</h2>\n'
            f'<div class="entry entry--{side}">\n'
            f'<div class="entry__media"><img class="thumb" src="{esc(photo)}" width="150" height="150" alt="{esc(alt)}"></div>\n'
            f'<div class="entry__text">\n{hello}{paras}{contact_html}\n</div>\n</div>\n</section>'), uses_script


EMAIL_SCRIPT = ("<script>\n"
                "document.querySelectorAll('.email').forEach(function (el) {\n"
                "  var a = document.createElement('a'), addr = el.dataset.user + '@' + el.dataset.domain;\n"
                "  a.href = 'mailto:' + addr; a.textContent = addr; el.replaceWith(a);\n"
                "});\n"
                "</script>")


def render(data, font: str | None = None) -> tuple[str, dict]:
    import thumbs
    site = data["site"]
    user = site["github_user"]
    preset = font or site.get("font") or "mixed"
    if preset not in FONT_PRESETS:
        sys.exit(f"font must be one of {', '.join(FONT_PRESETS)} (got {preset!r})")

    about_html, uses_script = render_about(data.get("about") or {})
    stats = {"entries": 0, "drafts": 0, "sections": 0, "todos": 0}
    entries = [e for e in (data.get("entries") or []) if not e.get("hidden")]
    ids = [str(e.get("id") or gh_name(e)) for e in data.get("entries") or []]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        sys.exit(f"duplicate entry ids in {CONTENT.name}: {', '.join(sorted(dupes))}")

    section_ids = [s["id"] for s in data.get("sections") or []]
    for e in entries:
        if e.get("section") not in section_ids:
            sys.exit(f"entry {gh_name(e)!r} has section {e.get('section')!r}, which is not in sections: {section_ids}")

    blocks, index = [], 0
    for s in data.get("sections") or []:
        mine = sorted((e for e in entries if e.get("section") == s["id"]), key=sort_key)
        if not mine:
            continue
        stats["sections"] += 1
        parts = []
        for e in mine:
            index += 1
            parts.append(render_entry(e, "right" if index % 2 == 1 else "left", user, thumbs))
            stats["entries"] += 1
            stats["drafts"] += str(e.get("blurb_status") or "").lower() == "draft"
        sid = esc(s["id"])
        blocks.append(f'<section id="{sid}" aria-labelledby="h-{sid}">\n<h2 id="h-{sid}">{esc(s["title"])}</h2>\n' + "\n".join(parts) + "\n</section>")

    url = str(site.get("url") or "").rstrip("/") + "/"
    footer = inline(str(site.get("footer") or "Set in [Roboto Serif](fonts/OFL-roboto-serif.txt) and [Space Mono](fonts/OFL-space-mono.txt), under the SIL Open Font License."))
    preloads = "\n".join(f'<link rel="preload" href="fonts/{f}.woff2" as="font" type="font/woff2" crossorigin>' for f in PRELOADS[preset])
    name = str(site["name"])
    page = string.Template(TEMPLATE.read_text(encoding="utf-8")).safe_substitute(
        lang=esc(site.get("language") or "en"), font=preset, title=esc(site.get("title") or name),
        description=esc(site.get("description") or ""), url=esc(url), site_name=esc(name),
        og_image=esc(url + "og.png"), og_image_alt=esc(site.get("og_image_alt") or f"{name}: a row of project thumbnails"),
        preloads=preloads, name=esc(name), about=about_html, sections="\n".join(blocks), footer=footer,
        scripts=EMAIL_SCRIPT if uses_script else "")
    stats["todos"] = len(re.findall(r'class="todo"', page))
    return page, stats


# =============================================================================================
# Command line
# =============================================================================================
def load_fixture(path: pathlib.Path) -> tuple[list[dict], list[str] | None]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, dict):
        return [normalise(r) for r in obj["repos"]], obj.get("pinned")
    return [normalise(r) for r in obj], None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip the GitHub sync; render only")
    ap.add_argument("--no-render", action="store_true", help="sync the YAML but do not write index.html")
    ap.add_argument("--from-json", type=pathlib.Path, metavar="FILE",
                    help="read the repo list from a JSON file shaped like the API response instead of calling GitHub")
    ap.add_argument("--font", choices=FONT_PRESETS, help="render with this preset instead of site.font")
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT, help="where to write the page (default index.html)")
    ap.add_argument("--content", type=pathlib.Path, default=CONTENT, help="path to the YAML file")
    args = ap.parse_args(argv)

    yaml = yaml_io()
    if not args.content.exists():
        sys.exit(f"{args.content} not found")
    data = yaml.load(args.content.read_text(encoding="utf-8"))
    user = data["site"]["github_user"]

    if not args.offline:
        token = os.environ.get("GITHUB_TOKEN") or None
        api = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
        names = {gh_name(e).lower() for e in data.get("entries") or []}
        force = {str(f).lower() for f in data.get("force_include") or []}
        site_repo = data["site"].get("site_repo", "")

        def want(repo):
            return repo["name"].lower() in names or repo["name"].lower() in force or exclusion_reason(repo, user, site_repo) is None

        if args.from_json:
            repos, pinned = load_fixture(args.from_json)
            source = f"fixture ({args.from_json.name})"
        else:
            print(f"syncing {user} from {api} ({'with' if token else 'without'} a token)")
            try:
                repos, pinned = collect(GitHub(api, token, os.environ.get("GITHUB_GRAPHQL_URL")), user, want)
            except ApiError as e:
                print(f"error: {e}", file=sys.stderr)
                print("The existing content/projects.yaml was left unchanged. Use --offline to render without syncing.", file=sys.stderr)
                return 1
            source = urllib.parse.urlparse(api).netloc
        counts = merge(data, repos, pinned, source)
        buf = io.StringIO()
        yaml.dump(data, buf)
        if buf.getvalue() != args.content.read_text(encoding="utf-8"):
            tmp = args.content.with_suffix(".yaml.tmp")
            tmp.write_text(buf.getvalue(), encoding="utf-8")
            tmp.replace(args.content)
            print("content/projects.yaml updated")
        else:
            print("content/projects.yaml already up to date")
        print(f"  {counts['updated']} refreshed, {counts['added']} added, {counts['excluded']} excluded, "
              f"{counts['private']} private skipped"
              + (f", pinned order {'applied' if pinned is not None else 'unknown (no token)'}"))

    seeded = [gh_name(e) for e in data.get("entries") or [] if str((e.get("github") or {}).get("source", "")).startswith("fixture")]
    if seeded:
        print(f"note: {len(seeded)} entries hold GitHub fields seeded from a file, not from GitHub "
              f"({', '.join(seeded)}). Run `python scripts/build.py` without --offline to replace them.")

    if not args.no_render:
        page, stats = render(data, args.font)
        if not args.out.exists() or args.out.read_text(encoding="utf-8") != page:
            args.out.write_text(page, encoding="utf-8")
        print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: "
              f"{stats['entries']} entries in {stats['sections']} sections (empty sections hidden), "
              f"{stats['drafts']} draft blurbs, {stats['todos']} [TODO] markers still on the page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
