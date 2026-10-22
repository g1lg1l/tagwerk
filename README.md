<p align="center"><img src="assets/icon.svg" width="128" alt="Tagwerk icon"></p>

<h1 align="center">Tagwerk</h1>

<p align="center"><b>Your music, sorted by sound.</b><br>
Point it at a folder: it listens to every track on your own machine, then sorts copies into folders by <b>style, mood and BPM</b>, writes a <b>report</b> you can open in Excel, and can tag the files for <b>rekordbox</b>.</p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fg1lg1l%2Ftagwerk%2Fmain%2Fpyproject.toml&query=%24.project.version&label=version&color=2de2e6">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-b6ff3b">
  <img alt="platforms" src="https://img.shields.io/badge/macOS%20%C2%B7%20Linux%20%C2%B7%20Windows%20(WSL2)-runs%20locally-555">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#install">Install</a> ·
  <a href="#commands">Commands</a> ·
  <a href="#how-it-sorts">How it sorts</a> ·
  <a href="#fix-it-by-hand">Fix it by hand</a> ·
  <a href="#rekordbox-and-other-dj-apps">rekordbox</a> ·
  <a href="#troubleshooting">Troubleshooting</a>
</p>

Built for DJs with big, messy electronic libraries. Nothing is uploaded: the AI models run on your computer.

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

`setup` asks a few questions (which folder, where the copies go, which layout, write tags or not), shows the command it built and runs it. Run that command again whenever you add music: only new tracks get analyzed.

What a run looks like:

```
tagwerk 0.7.0  ~/Music/HardGroove Set → ~/Music/Tagwerk
96 tracks · 94 already analyzed · 2 to analyze
1/2  Ocean Drive.mp3     145.0  8A  #techno #hardgroove #energetic E5
2/2  Moon Rotation.mp3   138.0  1A  #techno #deep-techno #deep #aggressive E5
✓ Sorted  ~/Music/Tagwerk  2 copied, 94 already there
✓ rekordbox  ~/Music/Tagwerk/rekordbox.xml
✓ Report  ~/Music/Tagwerk/tagwerk-report.csv

96 tracks · 121–153 BPM
  Techno       ██████████████████████████████ 78
  Tech Trance  ███ 7
  Hard Techno  ██ 4
  moods  Energetic 67 · Melodic 19 · Deep 8 · Dark 2

Done in 0m21s
```

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

**Speed**: the first run downloads the models (~30 MB, once) and listens to every track, several at a time: about 5 s per track on a 10-core Mac, so a 100-track folder takes around 8 minutes. After that every result is cached: re-runs, other layouts, reports and edits are instant.

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
    ├── rekordbox.xml          ← the same folders as rekordbox playlists
    └── tagwerk-report.csv     ← every track: BPM, key, energy, mood, styles, scores, tags
```

Every copy carries its BPM, key, style (as genre) and tags (in Comment), so rekordbox, Serato, Traktor or Engine show them straight away.

or with `--compact`, one folder per combination:

```
Tagwerk/
├── Techno - Energetic - 140-144 BPM/
├── Techno - Melodic - 140-144 BPM/
├── rekordbox.xml
└── tagwerk-report.csv
```

## Commands

| Command | What it does |
|---|---|
| `tagwerk setup` | Asks a few questions instead of flags, then runs |
| `tagwerk FOLDER` | Analyzes FOLDER and its subfolders, copies and sorts into `Tagwerk/` next to it, writes the report and `rekordbox.xml` |
| `tagwerk edit FILE_OR_FOLDER …` | Fix style, BPM, key, energy or mood by hand, add your own tags ([details](#fix-it-by-hand)) |
| `tagwerk similar FILE` | The tracks that sound closest, ✓ for the ones you can mix into it |
| `tagwerk search WORDS` | Find tracks by style, mood, BPM, key and your tags, e.g. `"dark acid techno 140"` |
| `tagwerk dupes FOLDER` | Tracks saved more than once (another format, bitrate or name) |

Every command has `--help`.

### Options for `tagwerk FOLDER`

| Option | Default | What it does |
|---|---|---|
| `--by LEVELS` | `style,mood,bpm` | Folder levels, any of `style` `mood` `bpm` `energy` `key` `vocals`, e.g. `--by key,bpm` |
| `--compact` | off | One folder per combination instead of a nested tree |
| `--organize OUT` | `Tagwerk/` next to FOLDER | Where the sorted copies, the report and `rekordbox.xml` go |
| `--csv FILE` | `OUT/tagwerk-report.csv` | Report somewhere else |
| `--analyze-only` | off | Just analyze: no copies, no report, no XML |
| `--limit N` | all | Analyze at most N new tracks: try it on a few first |
| `--jobs N` | half your cores, max 4 | Tracks analyzed at once; lower it if the computer gets sluggish |
| `--write-tags` | off | Write tags into the Comment field of your **original** files ([details](#tags-in-the-files)) |
| `--db FILE` | `~/.tagwerk/tagwerk.db` | Use another library database |

Ctrl‑C stops cleanly at any time: the next run picks up where it left off. While it works, a spinner with elapsed time shows what it's on; each run ends with an overview of your styles and moods. Colors turn off when the output isn't a terminal, or with `NO_COLOR=1`. `tagwerk --version` prints the version.

## How it sorts

| Level | Folder names | How |
|---|---|---|
| `style` | `Techno`, `Hardgroove`, `Tech Trance`… | The strongest of 400 Discogs styles. Discogs files a lot of hardgroove under Schranz, so Schranz below 150 BPM becomes Hardgroove |
| `mood` | `Energetic`, `Melodic`, `Deep`, `Dark`, `Emotional`, `Groovy`, `Uplifting` | The strongest group from a 56-label mood model (`Emotional` covers emotional, sad and melancholic). There's no cutoff, so near-identical tracks don't flip between a mood and "Neutral" |
| `bpm` | `140-144 BPM` | 5 BPM wide. Half-time detections (72 for a 144 techno track, 86 for 172 DnB) are doubled based on the style |
| `energy` | `Energy 1` … `Energy 10` | From the arousal model |
| `key` | `8A`, `11B`… | Camelot notation |
| `vocals` | `Vocal`, `Instrumental` | Voice detector |

The copies are your files, plus:

- **Tags**: BPM, key, style as genre, and the tags in Comment (see [below](#tags-in-the-files)). Your originals keep theirs.
- **Clean names**: copies lose the usual dirt in file name and title tag: track numbers (`22. `, `01 - `), `(Free Download)`, `[FREE DL]`, `PREMIERE:`, `| OUT NOW on …`, `(Official Video)`, `[HQ]`, `[320]`, yt-dlp video ids, underscores. Remixes, mix names, labels and names like `808 State` stay.
- **Duplicates**: the same track saved twice is copied once, the biggest file (best quality) wins. Nothing is ever deleted.
- **Updates**: running again copies new tracks, moves a copy whose folder changed (after an edit or a layout switch, removing emptied folders), refreshes a copy whose original changed and re-tags a copy whose tags changed.

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

## Search, similar tracks, duplicates

```bash
tagwerk search "dark hardgroove"            # every word must fit; the best matches first
tagwerk search melodic tech trance 138-142
tagwerk search deep techno 8A #warmup       # 8A: tracks that mix with it; #tags you added or tagwerk learned
tagwerk similar ".../track.mp3" -n 10       # closest-sounding tracks, ✓ = within ±6% BPM and a compatible key
tagwerk dupes ~/Music/playlists             # same track saved more than once, which one gets copied
```

`search` knows the 400 style names (`dub techno`, `acid`, `hardgroove`, `dnb`…), the 56 mood labels (`dark`, `deep`, `melodic`, `emotional`, `groovy`, `energetic`…), `aggressive`, `chill`, `vocal`, `instrumental`, BPM (`140` = 137–143, or `138-142`), Camelot keys and `#tags`. Words it doesn't know are shown, not guessed.

All three work from the cache: instant, no re-analysis.

## rekordbox and other DJ apps

**rekordbox**: each run writes `rekordbox.xml` next to the copies, with a *Tagwerk* folder of playlists that mirrors your folders, plus a *Your tags* folder with one playlist per tag you added by hand or tagwerk learned.

1. rekordbox › *Preferences* › *Advanced* › *Database* › *rekordbox xml* › *Imported Library*: pick `Tagwerk/rekordbox.xml`.
2. In the sidebar, turn on *rekordbox xml* (*Preferences* › *View* › *Layout*) and open it.
3. Right click *Tagwerk* › *Import Playlist*. rekordbox analyzes beat grids as usual.

Ran it inside WSL on Windows? Paths like `/mnt/c/…` are written as `C:/…`, so rekordbox on Windows finds the files.

**Serato, Traktor, Engine, Mixxx**: import the `Tagwerk/` folder. BPM, key, genre and comment come from the copies' tags.

## Tags in the files

Copies always get tags. Your **originals** only with `--write-tags`, the one option that touches them. Either way the Comment field gets a block like this at the front, keeping whatever was there (e.g. Mixed In Key):

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

Copies also get BPM, key (e.g. `Am`) and the main style as genre.

**rekordbox intelligent playlists**: *Comments contains* `#hardgroove`. After `--write-tags` on tracks already in your collection, right click › *Reload Tag* first.

## Files and tuning

| What | Where |
|---|---|
| Library database: analysis, edits, which copies exist | `~/.tagwerk/tagwerk.db` (`TAGWERK_HOME` to move it) |
| Models | `~/.cache/tagwerk/models` (`TAGWERK_MODELS` to move them) |
| Report | `tagwerk-report.csv` in the output folder: `;` and decimal commas, so an Italian-locale Excel opens it on double click |

Thresholds and vocabularies live at the top of [`tagwerk/tags.py`](tagwerk/tags.py): `STYLE_MIN`, `MOOD_MIN`, `VOCAL_MIN`, `MOOD_GROUPS`, `MOOD_TAGS`, `LEARN_MIN`, `LEARN_EXAMPLES`, `DUPE_MIN`, the clean-name patterns `_DIRT`, the style renames in `relabel_styles` and the half-time rule in `fold_bpm`. Everything is recomputed from the cache, so changing them needs no re-analysis.

## Troubleshooting

| Problem | Fix |
|---|---|
| First run is slow | Normal: models download once (~30 MB) and every track is listened to (~5 s each, several at a time). Re-runs only analyze new or changed files |
| `command not found: tagwerk` | Run `uv tool update-shell`, then open a new terminal |
| `no audio files in …` | Check the path; quote names with spaces: `tagwerk "~/Music/My Set"` won't expand `~`, use `tagwerk ~/Music/"My Set"` |
| A track got the wrong style or mood | `tagwerk edit <track> --style … --mood …`, then run again: the copy moves to the right folder |
| Copies landed in a folder you don't want | Delete the `Tagwerk/` folder and run again with other options: your originals are never touched |
| Windows: can't find my music | Inside WSL your drives are under `/mnt/c/`, e.g. `tagwerk /mnt/c/Users/<you>/Music` |
| Same track in two folders | It's copied once; `tagwerk dupes <folder>` shows which file was kept |

## Development

```bash
git clone https://github.com/g1lg1l/tagwerk && cd tagwerk
uv tool install --python 3.12 -e .
python tests/test_tagwerk.py      # needs numpy and mutagen, not Essentia
```

## Models

[Essentia models](https://essentia.upf.edu/models.html) (MTG, Universitat Pompeu Fabra), licensed CC BY-NC-SA 4.0: non-commercial use only.
