<p align="center"><img src="assets/icon.svg" width="128" alt="Tagwerk icon"></p>

# Tagwerk

Point it at a folder of music and it listens to every track, on your own machine, then sorts copies into folders by **style, mood and BPM**, writes a **report** you can open in Excel, and can tag the files for **rekordbox**. Built for DJs with big, messy electronic libraries.

- **BPM** (half-time detections fixed) and **key** in Camelot notation
- **Style** from 400 Discogs styles: techno, hardgroove, dub techno, minimal, acid, DnB…
- **Mood**: Energetic, Melodic, Deep, Dark, Emotional, Groovy, Uplifting
- **Energy** 1–10, **vocal / instrumental**
- Clean file names, duplicates copied once, your originals never touched

## Quick start

```bash
uv tool install --python 3.12 git+https://github.com/g1lg1l/tagwerk
tagwerk setup
```

`setup` asks four questions (which folder, where the copies go, which layout, write tags or not), shows the command it built and runs it. Run that command again whenever you add music: only new tracks get analyzed.

## Install

tagwerk is a command-line tool. Install it once and `tagwerk` works from any folder. You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv` on a Mac).

| System | How |
|---|---|
| **macOS** (Apple Silicon or Intel) | `uv tool install --python 3.12 git+https://github.com/g1lg1l/tagwerk` |
| **Linux** | same command |
| **Windows** | Essentia doesn't run on Windows itself: install [WSL2](https://learn.microsoft.com/windows/wsl/install) (Ubuntu), then the same command inside it. Your Windows folders are under `/mnt/c/Users/<you>/…` |

| To… | Run |
|---|---|
| update to the latest version | `uv tool upgrade tagwerk` |
| uninstall | `uv tool uninstall tagwerk` (your music and copies stay) |
| hack on it | clone, then `uv tool install --python 3.12 -e .` in the clone: code changes apply without reinstalling |

**Speed**: the first run downloads the models (~30 MB, once) and analyzes each track, about 5–10 s per track on a recent laptop, so a 100-track folder takes 10–15 minutes. After that every result is cached: re-runs, other layouts, reports and edits are instant.

## What you get

```bash
tagwerk ~/Music/"HardGroove Set"
```

creates, next to that folder:

```
Music/
├── HardGroove Set/            ← your originals, untouched
└── Tagwerk/
    ├── Techno/
    │   ├── Energetic/
    │   │   ├── 140-144 BPM/
    │   │   └── 145-149 BPM/
    │   └── Melodic/
    │       └── 140-144 BPM/
    ├── Tech Trance/
    │   └── Energetic/
    │       └── 140-144 BPM/
    └── tagwerk-report.csv     ← every track: BPM, key, energy, mood, styles, scores, tags
```

or with `--compact`, one folder per combination:

```
Tagwerk/
├── Techno - Energetic - 140-144 BPM/
├── Techno - Melodic - 140-144 BPM/
└── tagwerk-report.csv
```

## Commands

| Command | What it does |
|---|---|
| `tagwerk setup` | Asks a few questions instead of flags, then runs |
| `tagwerk FOLDER` | Analyzes FOLDER and its subfolders, copies and sorts into `Tagwerk/` next to it, writes the report |
| `tagwerk edit FILE_OR_FOLDER …` | Fix style, BPM, key, energy or mood by hand, add your own tags ([details](#fix-it-by-hand)) |
| `tagwerk similar FILE` | The tracks that sound closest, ✓ for the ones you can mix into it |
| `tagwerk dupes FOLDER` | Tracks saved more than once (another format, bitrate or name) |

Every command has `--help`.

### Options for `tagwerk FOLDER`

| Option | Default | What it does |
|---|---|---|
| `--by LEVELS` | `style,mood,bpm` | Folder levels, any of `style` `mood` `bpm` `energy` `key` `vocals`, e.g. `--by key,bpm` |
| `--compact` | off | One folder per combination instead of a nested tree |
| `--organize OUT` | `Tagwerk/` next to FOLDER | Where the sorted copies and the report go |
| `--csv FILE` | `OUT/tagwerk-report.csv` | Report somewhere else |
| `--analyze-only` | off | Just analyze: no copies, no report |
| `--limit N` | all | Analyze at most N new tracks: try it on a few first |
| `--write-tags` | off | Write tags into the Comment field of your **original** files ([details](#tags-in-the-files)) |
| `--db FILE` | `~/.tagwerk/tagwerk.db` | Use another library database |

Ctrl‑C stops cleanly at any time: the next run picks up where it left off. A spinner with elapsed time shows it's working, and each run ends with an overview of your styles and moods.

## How it sorts

| Level | Folder names | How |
|---|---|---|
| `style` | `Techno`, `Hardgroove`, `Tech Trance`… | The strongest of 400 Discogs styles. Discogs files a lot of hardgroove under Schranz, so Schranz below 150 BPM becomes Hardgroove |
| `mood` | `Energetic`, `Melodic`, `Deep`, `Dark`, `Emotional`, `Groovy`, `Uplifting` | The strongest group from a 56-label mood model (`Emotional` covers emotional, sad and melancholic). There's no cutoff, so near-identical tracks don't flip between a mood and "Neutral" |
| `bpm` | `140-144 BPM` | 5 BPM wide. Half-time detections (72 for a 144 techno track, 86 for 172 DnB) are doubled based on the style |
| `energy` | `Energy 1` … `Energy 10` | From the arousal model |
| `key` | `8A`, `11B`… | Camelot notation |
| `vocals` | `Vocal`, `Instrumental` | Voice detector |

The copies keep your files, but:

- **Clean names**: copies lose the usual dirt in file name and title tag: track numbers (`22. `, `01 - `), `(Free Download)`, `[FREE DL]`, `PREMIERE:`, `| OUT NOW on …`, `(Official Video)`, `[HQ]`, `[320]`, yt-dlp video ids, underscores. Remixes, mix names, labels and names like `808 State` stay.
- **Duplicates**: the same track saved twice is copied once, the biggest file (best quality) wins. Nothing is ever deleted.
- **Updates**: running again copies new tracks, moves a copy whose folder changed (after an edit or a layout switch, removing emptied folders) and refreshes a copy whose original changed.

## Fix it by hand

The analysis is a starting point. `tagwerk edit` corrects it or adds your own tags. Edits live in the library database, survive re-analysis, and apply on the next run (copies, report, tags).

```bash
# wrong style, BPM, key, energy or mood
tagwerk edit ".../track.mp3" --style "Dub Techno" --bpm 126 --key 8A --energy 4 --mood Dark

# your own categories: tag a whole playlist folder at once
tagwerk edit ".../playlists/warm up" --add warmup
tagwerk edit ".../track.mp3" --add peak closing --remove melodic

# see what tagwerk thinks of a track, or undo your edits
tagwerk edit ".../track.mp3"
tagwerk edit ".../track.mp3" --reset
```

**Tagwerk learns your tags.** Once a tag is on 5+ tracks, every track you haven't hand-tagged gets the hand tags of the most similar track you did, if it's similar enough. Wrong guess? `--remove` it on that track: it becomes a "not this" example and its neighbours follow.

## Similar tracks and duplicates

```bash
tagwerk similar ".../track.mp3" -n 10    # closest-sounding tracks, ✓ = within ±6% BPM and a compatible key
tagwerk dupes ~/Music/playlists          # same track saved more than once, which one gets copied
```

Both work from the cache: instant, no re-analysis.

## Tags in the files

Optional, and the only thing that touches your originals. `--write-tags` puts a block like this at the front of each file's Comment field, keeping whatever was there (e.g. Mixed In Key):

```
tw: #techno #hardgroove #energetic #aggressive E6; 8A - Energy 7
```

| Part | What |
|---|---|
| `#techno #hardgroove` | Main style always, 2nd and 3rd if strong enough |
| `#energetic` | The mood |
| `#aggressive #chill #happy #vocal` | Extra mood flags, when strong |
| `E6` | Energy 1–10 |
| your tags | Added by hand or learned; `--remove` beats anything tagwerk found |

**rekordbox**: after writing tags, right click › *Reload Tag* on tracks already in the collection, then build intelligent playlists with *Comments contains* `#hardgroove`.

## Files and tuning

| What | Where |
|---|---|
| Library database: analysis, edits, which copies exist | `~/.tagwerk/tagwerk.db` (`TAGWERK_HOME` to move it) |
| Models | `~/.cache/tagwerk/models` (`TAGWERK_MODELS` to move them) |
| Report | `tagwerk-report.csv` in the output folder: `;` and decimal commas, so an Italian-locale Excel opens it on double click |

Thresholds and vocabularies live at the top of [`tagwerk/tags.py`](tagwerk/tags.py): `STYLE_MIN`, `MOOD_MIN`, `MOOD_GROUPS`, `MOOD_TAGS`, `LEARN_MIN`, `LEARN_EXAMPLES`, `DUPE_MIN`, the clean-name patterns `_DIRT`, the style renames in `relabel_styles` and the half-time rule in `fold_bpm`. Everything is recomputed from the cache, so changing them needs no re-analysis.

## Development

```bash
git clone https://github.com/g1lg1l/tagwerk && cd tagwerk
uv tool install --python 3.12 -e .
python tests/test_tagwerk.py      # needs numpy and mutagen, not Essentia
```

## Models

[Essentia models](https://essentia.upf.edu/models.html) (MTG, Universitat Pompeu Fabra), licensed CC BY-NC-SA 4.0: non-commercial use only.
