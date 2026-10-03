# Fonts

The four `.woff2` files here are subsets of **Computer Modern Unicode 0.7.0**
(CMU), made by Andrey V. Panov from Donald Knuth's Metafont sources. They are
used under the **SIL Open Font License 1.1**; the full text is in `OFL.txt` and
is also stored inside each font file (name IDs 0, 13 and 14).

| File | Face | Used for |
|---|---|---|
| `cmu-concrete-roman.woff2` | CMU Concrete Roman | body text in the `paper` and `mixed` presets |
| `cmu-concrete-bold.woff2` | CMU Concrete Bold | links and strong text in those presets |
| `cmu-typewriter-regular.woff2` | CMU Typewriter Text Regular | all text in `terminal` |
| `cmu-typewriter-bold.woff2` | CMU Typewriter Text Bold | name and headings in `mixed`; links in `terminal` |

The reference site loads `cmunorm.ttf`. Its name table says family "CMU
Concrete", style "Roman", PostScript name `CMUConcrete-Roman`, version 0.7.0, so
that file is CMU Concrete Roman and not CMU Serif.

## Where they came from

The originals are the official release at
<https://sourceforge.net/projects/cm-unicode/> (`cm-unicode-0.7.0-ttf.tar.xz`,
SHA-256 `2609c14450f42d0bcd40203900afcb1d693521a9b24a18c65e14b6b0585ff150`).
`scripts/fonts.py` downloads that file, checks the hash, and rebuilds everything
in this folder.

## What was changed

The OFL treats any subset or format conversion as a Modified Version, so here is
the full list of changes. Glyphs outside Latin, Latin Extended-A, common
punctuation, Greek and a few maths symbols were removed. Hinting, embedded
bitmaps and two tool-specific tables (`TeX `, `BDF `) were removed. The result
was written as WOFF2. No outline, metric or name was edited.

The copyright holder reserves the name "Computer Modern Unicode fonts". That
string appears nowhere in these files (`scripts/fonts.py` asserts it), and the
fonts keep their original family names.

## Symbols the faces do not have

CMU's text faces leave most maths symbols to Knuth's separate maths fonts. These
are present: Greek letters, `×  ÷  ±  −  ∗  √`, arrows `← ↑ → ↓`, superscript and
subscript digits, and the usual fractions. These are absent and will fall back
to a system font: `≤ ≥ ≠ ≈ ∞ ∂ ∇ ∑ ∏ ∫ ∈ ⊂ ∧ ∨ ∩ ∪ ⇒`. If you need real
mathematics, use images or a maths renderer rather than plain text.
