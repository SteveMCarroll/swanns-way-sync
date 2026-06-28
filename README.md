# Swann&rsquo;s Way &mdash; Reading Sync

A cross-medium navigation map for Marcel Proust&rsquo;s *Swann&rsquo;s Way*, so you can move
freely between the **audiobook**, the **Lydia Davis** (Penguin) edition, and the
**Moncrieff** (Modern Library) edition even though the book has almost no chapter breaks.

The book is only three enormous parts &mdash; *Combray*, *Swann in Love*,
*Place-Names: The Name* &mdash; with no natural stop-and-switch points. This project lays a
grid of ~130 **landmarks** spaced about **10 minutes of audio** apart. Each landmark records:

- the **audio timestamp** (HH:MM:SS) and track number in the m4b,
- the **Davis** page in both the physical Penguin Classics and the Kindle Deluxe edition,
- the **Moncrieff** page in the Modern Library edition (exact),
- the first ~7 words of the paragraph in **both** translations, as a search locator.

The live site: **https://stevemcarroll.github.io/swanns-way-sync/**

## How it&rsquo;s built

Everything is generated locally from the ebooks and audiobook; **no book text or audio is
committed** (a copyright firewall enforces this).

| Step | Tool | Output |
|---|---|---|
| Parse both ebooks into paragraphs + Moncrieff print-page anchors | `tools/epub_parse.py` | in-memory |
| Transcribe the audiobook with timestamps | `tools/transcribe_audio.py` (faster-whisper, GPU) | `tools/transcript.json` |
| Pick landmarks, cross-map translations, assign pages, fuzzy-match audio times | `tools/build_correspondence.py` | `src/_data/landmarks.json`, `correspondence.csv`, `out/*.cue`, `out/*.ffmetadata` |
| Static site | Eleventy | `_site/` |

### Method notes
- **Audio times** come from matching each landmark&rsquo;s opening words against the
  timestamped transcript (monotonic, windowed search), so they absorb the audiobook&rsquo;s
  intro, pace changes, and any wording differences. Unmatched landmarks fall back to a
  constant words-per-minute estimate.
- **Davis &harr; Moncrieff** is a cross-translation map (no shared words). Each landmark is
  placed by word-fraction within its Part, then snapped to the nearby Davis paragraph that
  best shares proper nouns (Swann, Odette, Vinteuil&hellip;) with the Moncrieff paragraph.
- **Davis pages** are interpolated per sub-part from the two editions&rsquo; tables of
  contents (physical: Combray 3 / 49, Swann 193, Place-Names 397; Kindle: 29 / 81 / 245 / 476).

## Regenerate

```bash
# 1. extract the epubs next to the originals (one-time)
#    _extract/Swanns_Way_Modern_Library, _extract/Swanns_Way_Penguin_Davis

# 2. (optional) transcribe the audiobook  — needs an NVIDIA GPU + the .m4b present
python -m venv .venv && .venv/Scripts/pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12 rapidfuzz
.venv/Scripts/python tools/transcribe_audio.py

# 3. build the correspondence data + cue + ffmetadata
.venv/Scripts/python tools/build_correspondence.py

# 4. build / preview the site
npm install
npm run dev        # http://localhost:8080
npm run build:docs # render into docs/ for GitHub Pages
```

## Publishing (GitHub Pages)

The site is served from the **`docs/` folder on `main`** (Pages → Build and deployment →
Source: *Deploy from a branch*, Branch: `main` / `/docs`). Regenerate and push with:

```powershell
npm run build:docs
git add docs && git commit -m "Rebuild site" && git push
```

A ready-to-use GitHub **Actions** workflow is included at
`tools/github-pages-deploy.yml.reference`. If you'd rather have CI build the site on every
push, move it to `.github/workflows/deploy.yml` and switch the Pages source to *GitHub
Actions* (this requires pushing with a token that has the `workflow` scope).

## Audiobook chapters (Smart Audiobook Player)

`tools/build_correspondence.py` writes `out/Swanns_Way_landmarks.cue` (named landmarks) and
`out/Swanns_Way_chapters.ffmetadata`. To embed named chapters into a copy of the m4b
(stream copy, no re-encode):

```powershell
pwsh tools/make_chaptered_m4b.ps1
# -> out/Swann's Way (chaptered).m4b   (copy this to your phone)
```

Smart Audiobook Player reads the embedded chapters; alternatively drop the `.cue` next to the
audio file.

## Copyright

This repository is a personal reading aid. It never stores or reproduces the copyrighted
translations or the audiobook &mdash; only page numbers, timestamps, and short (&le;8-word)
opening-word locators. `npm run check` fails the build if any ebook/audio file is committed
or if an incipit exceeds the locator cap.
