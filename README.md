# Archive of My Own

Here lies my portfolio. I hope to update it every year.

It is one static page of projects, writing and experiments. The structure and measurements follow [Gabriel Goh's homepage](https://gabgoh.github.io): one centred column, a rule under each heading, and a zigzag of square thumbnails beside short blurbs. No text, image or logo comes from that site. The page is plain HTML and CSS with no framework, no analytics and no third-party requests. The only JavaScript is a four-line inline script that turns the obfuscated email into a link.

Everything the page says lives in `content/projects.yaml`. `scripts/build.py` pulls your repositories from GitHub into that file and renders `index.html` from it.

## Quick start

You need Python 3.9 or newer.

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/build.py              # sync GitHub into the YAML, then write index.html
python -m http.server 8000           # open http://localhost:8000
```

Add `--offline` to skip GitHub and only render. That is what the deploy workflow does.

## What is where

| Path | What it is |
|---|---|
| `content/projects.yaml` | Every word, link and setting on the page. The one file you edit. |
| `index.html` | The page. Generated, so do not edit it by hand. |
| `styles.css` | All styling, including the three font presets. |
| `scripts/build.py` | Syncs GitHub into the YAML, then renders the page through `scripts/template.html`. |
| `scripts/thumbs.py` | Draws the thumbnails in `thumbs/`, one SVG per repo. |
| `scripts/fonts.py` | Rebuilds `fonts/` from the official CM Unicode release. |
| `scripts/capture.py` | Screenshots, favicon and social card (developer tool, needs Playwright). |
| `fonts/`, `thumbs/` | Self-hosted fonts and the thumbnails. |
| `tests/` | Unit tests, run against a fake GitHub server. |
| `docs/` | Screenshots and the report. Not published. |
| `.github/workflows/` | `deploy.yml` publishes to Pages; `tests.yml` runs the tests on pull requests. |

## How the sync works

`python scripts/build.py` calls `GET /users/Cosmic-Witness/repos?per_page=100&type=owner` and follows the pages. For each repository it stores the name, URL, description, topics, main language, homepage, last push date, star count and README text under that entry's `github:` block. Forks, archived repos, empty repos, private repos, your profile README repo and this site's repo are not shown. They are listed under `excluded:` with the reason, except private repos, whose names are never written anywhere.

The sync rewrites only the `github:` blocks, and it adds an entry when it meets a new repo. It never changes the fields that belong to you: `title`, `section`, `section_status`, `blurb`, `blurb_status`, `built_with`, `links`, `thumb`, `thumb_alt`, `order` and `hidden`. Your YAML comments survive too. Running it twice in a row changes nothing.

A new repo gets a proposed section, either `projects` or `fun`, chosen by counting telling words in its README, description and topics ("toy", "game", "bot" against "research", "dataset", "neural", "framework" and so on). The entry is marked `section_status: proposed` until you set it to `confirmed`. It also gets a placeholder blurb marked `[TODO]`.

If the API cannot be reached, the build stops with the reason and leaves the YAML untouched. If a repo vanishes from GitHub, its entry stays and the build says so.

## Everyday tasks

**Rebuild.** Run `python scripts/build.py`. Use `--offline` to skip the network, or `--font terminal` to try a preset without editing the YAML.

**Edit a blurb.** Change `blurb:` on the entry. A blank line starts a new paragraph, `[text](https://...)` makes a link and `**bold**` makes bold. Every blurb ends with a dotted `[draft]` mark until you set `blurb_status: final`. The "Built with:" line comes from `built_with:`; leave that empty and the line is made from GitHub's main language and topics instead.

**Move a repo between sections.** Change its `section:` to `projects`, `writing` or `fun`, and set `section_status: confirmed`.

**Change the order.** Give entries `order: 1`, `order: 2` and so on. Lower numbers come first within a section. Entries without `order` follow, pinned repos first and then the most recently pushed. Pinned order needs a token, described below.

**Hide a repo, or show an excluded one.** Set `hidden: true` on an entry to drop it from the page. To show something listed under `excluded:`, add its name to `force_include:` and rebuild.

**Switch the font preset.** Set `site.font` to `paper`, `terminal` or `mixed`, then rebuild. `paper` sets everything in CMU Concrete, which is the face the reference loads. `terminal` sets everything in CMU Typewriter Text. `mixed`, the default, uses typewriter for the name and headings and Concrete for the rest. The choice is one attribute on `<html>`, `data-font`, and two custom properties in `styles.css`, `--font-body` and `--font-head`. You can also change `data-font` in the browser's developer tools to compare presets live. The build writes the matching `<link rel="preload">` tags, so use the YAML setting for the real thing.

**Replace a thumbnail.** Save your SVG over `thumbs/<repo name>.svg`. `scripts/thumbs.py` leaves any file it did not generate alone. For a PNG or JPEG, save it anywhere and point `thumb:` at it. Use 300 by 300 pixels, because it is shown at 150. Update `thumb_alt` to describe the new picture. To draw a fresh generated one instead, run `python scripts/thumbs.py --name <repo name> --force`.

After changing thumbnails, run `python scripts/capture.py assets` to refresh `og.png`, the picture shown when the link is shared.

**Add a Writing entry.** Add this to `entries:` and rebuild. The Writing section appears as soon as it has one visible entry.

```yaml
  - id: my-first-note
    section: writing
    title: A Short Note on Something
    blurb: Two to five sentences, in your own voice.
    blurb_status: final
    links:
      - {label: Paper, url: "https://example.org/my-note.pdf"}
    thumb: thumbs/my-first-note.svg
    thumb_alt: A short description of the picture, for screen readers.
```

Then run `python scripts/thumbs.py --name my-first-note` to draw it a thumbnail, or draw your own.

**Fill in About.** Everything marked `[TODO]` in the YAML and on the page is yours to write: the three About paragraphs, the photo (`about.photo`, square, 300 by 300 or larger), and the LinkedIn and X lines, which you can delete if you do not want them. The email address is stored split into `user` and `domain` and is assembled by a few lines of script, so it does not appear in the page source as an address.

**Use a token.** The build works without one, but an unauthenticated client is limited to 60 requests an hour. A token raises that and unlocks pinned-repo ordering, which uses GraphQL. A token with no scopes is enough, because everything read is public.

```sh
GITHUB_TOKEN=$(gh auth token) python scripts/build.py
```

## Thumbnails

All thumbnails are 150 by 150 SVGs on one dark grey (`#333333`) in white and a six-step ramp of reds, drawn with thin strokes and no text. `scripts/thumbs.py` holds the palette. Three are redrawn from the real projects: the Projections figure of the vector (3, 7), the Naive DFT stem plot (recomputed with the repo's own formula), and the Stryder training loss (the 100 epoch losses printed in its notebook). Eliza, Ellie and Ada have no data to plot, so they get small drawings of what they do. Any other name gets an abstract plot, chosen by hashing the name, so it never changes between builds. Run `python scripts/thumbs.py --list` to see which style each name gets.

## Deploying

The workflow publishes to GitHub Pages and does nothing until you switch Pages on.

GitHub serves a repository at `https://cosmic-witness.github.io/` only if the repository is named `Cosmic-Witness.github.io`. Under the current name the address would be `https://cosmic-witness.github.io/Archive-of-My-Own/`. Pick one. Either rename the repository (Settings, General, Repository name), or keep the name and set `site.url` in the YAML to the longer address and rebuild. All paths in the page are relative, so it works at either address.

Then open Settings, Pages, and set Source to GitHub Actions. Pushing to `main` runs `.github/workflows/deploy.yml`, which renders the page from the committed YAML and publishes only `index.html`, `styles.css`, the icons, `fonts/` and `thumbs/`. It does not publish `docs/`, `content/` or the scripts.

## Checks

```sh
python -m unittest discover -s tests -v     # sync, privacy, ordering, escaping, thumbnails, fonts
python scripts/capture.py screenshots      # six full-page screenshots; fails on console messages or sideways scrolling
```

`fonts.py` and `capture.py` are developer tools. They need `pip install -r requirements-dev.txt`, and `capture.py` also needs a Chromium (set `CHROMIUM=/path/to/chrome` if Playwright cannot find one).

The tests run against a fake GitHub on localhost. They check that a sync never overwrites hand edits, that a private repo's name is never written, that paging, tokens, rate limits and pinned order behave, that user text is escaped, and that the committed `index.html` matches the YAML.

The HTML, the stylesheet and every SVG pass the W3C Nu Html Checker (`java -jar vnu.jar --also-check-css --also-check-svg ...`). Lighthouse on mobile scored 100 in all four categories for each font preset.

## Fonts

The fonts are subsets of Computer Modern Unicode 0.7.0, used under the SIL Open Font License. `fonts/README.md` says where they came from, what was changed and which maths symbols the faces lack. `python scripts/fonts.py` rebuilds them from the official release and checks the download's hash.

## Licence

The code and content are MIT, see `LICENSE`. The fonts in `fonts/` are SIL OFL 1.1, see `fonts/OFL.txt`.
