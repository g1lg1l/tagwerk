<p align="center"><img src="assets/icon.svg" width="128" alt="Tagwerk icon"></p>

# Tagwerk

Analyzes your music locally and tags it on its own: BPM, key (Camelot), Discogs style (techno, dub techno, minimal, acid, DnB…), mood, vocals/instrumental and energy. It can copy your tracks into a neatly organized folder tree, export a CSV, and optionally write tags into the files' *Comment* field so rekordbox can use them in intelligent playlists.

## Install

tagwerk is a command-line tool: install it once and the `tagwerk` command works from any folder.

**macOS** (Apple Silicon or Intel), with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install --python 3.12 git+https://github.com/g1lg1l/tagwerk
```

From a local clone, `uv tool install --python 3.12 -e .` instead, so your code changes apply without reinstalling.

**Windows**: Essentia doesn't run on Windows itself, so use WSL2 (Ubuntu) and the same command there. Windows folders live under `/mnt/c/...`.

On first run the models (~25 MB) are downloaded to `~/.cache/tagwerk/models` (override with `TAGWERK_MODELS`).

## Usage

The easy way: `tagwerk setup` asks which folder to read, where the sorted copies go, which layout and whether to write tags, then shows the command it built and runs it. Run that same command again whenever you add music.

```bash
tagwerk setup
```

Or with flags:

```bash
# organize a folder (and all its subfolders) into a new folder: style, then mood, then BPM range
tagwerk ~/Music/playlists --organize ~/Music/Tagwerk

# other layouts: any combination of style, bpm, energy, key, mood, vocals
tagwerk ~/Music/playlists --organize ~/Music/"By key" --by key,bpm

# review results in a spreadsheet
tagwerk ~/Music/playlists --csv tagwerk.csv

# try on a few tracks first
tagwerk ~/Music/playlists --limit 20 --organize ~/Music/Test
```

- `--organize` **copies**, it never moves your originals. Running it again copies what's new, moves a copy whose folder changed (after `tagwerk edit` or new thresholds) and refreshes a copy whose original changed (e.g. after `--write-tags`). Same name but a different file → `name (2).mp3`.
- **Duplicates**: the same track saved twice (another format, bitrate or name) is copied once, the biggest file wins. `tagwerk dupes ~/Music/playlists` lists them; nothing is ever deleted.
- Progress shows time left; Ctrl‑C stops cleanly and the next run picks up where it left off. Each run ends with a quick overview of your library's styles.
- Everything lives in one library database, `~/.tagwerk/tagwerk.db` (override with `TAGWERK_HOME` or `--db`): analysis, your edits and which copies tagwerk made. Already analyzed tracks are never analyzed again, so switching `--by` layout is instant. Upgrading from 0.2 or older? Move your old `tagwerk.db` there.
- The CSV uses `;` and decimal commas so an Italian-locale Excel opens it on double click.

Example output with `--by style,bpm` (27 tracks from 9 techno/DnB/house playlists):

```
Tagwerk/
├── Drum n Bass/
│   └── 170-174 BPM/
├── Hard Techno/
│   ├── 140-144 BPM/
│   └── 145-149 BPM/
├── House/
│   └── 120-124 BPM/
└── Techno/
    ├── 140-144 BPM/
    ├── 145-149 BPM/
    └── 150-154 BPM/
```

Half-time detections (72 BPM for a 144 techno track, 86 for 172 DnB) are doubled based on the detected style; the rule lives in `fold_bpm` and is applied on read, so tuning it needs no re-analysis.

## Fix it by hand

The analysis is a starting point. `tagwerk edit` corrects it or adds your own tags; edits live in the library database (never lost when a track is re-analyzed) and apply on the next `--write-tags`, `--organize` or `--csv`.

```bash
# wrong style, BPM, key or energy
tagwerk edit ".../track.mp3" --style "Dub Techno" --bpm 126 --key 8A --energy 4

# your own categories: tag a whole playlist folder at once
tagwerk edit ".../playlists/warm up" --add warmup
tagwerk edit ".../track.mp3" --add peak closing --remove dark

# see what tagwerk thinks of a track, or undo your edits
tagwerk edit ".../track.mp3"
tagwerk edit ".../track.mp3" --reset
```

**Tagwerk learns your tags.** Once a tag is on 5+ tracks (`LEARN_EXAMPLES`), every track you haven't hand-tagged gets the hand tags of the most similar track you did (if it's similar enough, `LEARN_MIN`). Wrong guess? `--remove` it on that track: it becomes a "not this" example and its neighbours follow. Thresholds live in `tagwerk/tags.py`.

## Similar tracks

```bash
tagwerk similar ".../track.mp3" -n 10
```

Lists the tracks that sound closest, from the cached analysis (instant, no re-analysis). ✓ marks the mixable ones: within ±6% BPM and a compatible Camelot key.

## Tags

`--write-tags` writes a block like this into the Comment field of the **original** files, in front of whatever was there already (e.g. Mixed In Key), which stays intact:

```
tw: #dub-techno #techno #dark E6; 8A - Energy 7
```

- **styles**: the first always, 2nd and 3rd only above threshold (`STYLE_MIN` in `tagwerk/tags.py`)
- **mood**: `#aggressive #chill #happy #dark #vocal` above `MOOD_MIN`; change the vocabulary in `MOOD_TAGS`
- **E1–E10**: energy, derived from arousal
- **your tags**: added by hand or learned (see above); `--remove` beats anything tagwerk found

Tags are recomputed from the cache, so changing thresholds or vocabulary and running `--write-tags` again needs no re-analysis.

**rekordbox**: after writing tags, right click › *Reload Tag* on tracks already in the collection. Then build intelligent playlists with *Comments contains* `#dub-techno`.

## Tests

```bash
python tests/test_tagwerk.py
```

## Models

[Essentia models](https://essentia.upf.edu/models.html) (MTG, Universitat Pompeu Fabra), licensed CC BY-NC-SA 4.0: non-commercial use only.
