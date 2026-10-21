<p align="center"><img src="assets/icon.svg" width="128" alt="Tagwerk icon"></p>

# Tagwerk

Analyzes your music locally and tags it on its own: BPM, key (Camelot), Discogs style (techno, dub techno, minimal, acid, DnB…), mood, vocals/instrumental and energy. It can copy your tracks into a neatly organized folder tree, export a CSV, and optionally write tags into the files' *Comment* field so rekordbox can use them in intelligent playlists.

## Setup (Windows → WSL2)

Essentia only runs on Linux/macOS, so on Windows use WSL2 (Ubuntu).

```bash
python3 -m venv ~/.venvs/tagwerk          # if ensurepip is missing: --without-pip, then get-pip.py
~/.venvs/tagwerk/bin/pip install -e /mnt/c/Users/<you>/workspace/tagwerk
```

On first run the models (~25 MB) are downloaded to `~/.cache/tagwerk/models` (override with `TAGWERK_MODELS`).

## Usage

From WSL, Windows folders live under `/mnt/c/...`.

```bash
# organize a folder (and all its subfolders) into a new folder, by style then BPM range
tagwerk "/mnt/c/Users/<you>/Music/playlists" --organize "/mnt/c/Users/<you>/Music/Tagwerk"

# other layouts: any combination of style, bpm, energy, key, mood, vocals
tagwerk "/mnt/c/Users/<you>/Music/playlists" --organize ".../By key" --by key,bpm

# review results in a spreadsheet
tagwerk "/mnt/c/Users/<you>/Music/playlists" --csv tagwerk.csv

# try on a few tracks first
tagwerk "/mnt/c/Users/<you>/Music/playlists" --limit 20 --organize ".../Test"
```

- `--organize` **copies**, it never moves: your original folders stay untouched. Same track in two playlists → copied once. Same filename but a different file → `name (2).mp3`. Running again only copies what's new.
- Progress shows time left; Ctrl‑C stops cleanly and the next run picks up where it left off. Each run ends with a quick overview of your library's styles.
- Results are cached in `tagwerk.db`: already analyzed tracks are never analyzed again, so switching `--by` layout is instant.
- The CSV uses `;` and decimal commas so an Italian-locale Excel opens it on double click.

Example output with the default `--by style,bpm` (27 tracks from 9 techno/DnB/house playlists):

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

The analysis is a starting point. `tagwerk edit` corrects it or adds your own tags; edits live in `tagwerk.db` (never lost when a track is re-analyzed) and apply on the next `--write-tags`, `--organize` or `--csv`.

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

`--organize` only ever adds copies: after changing a style, delete the output folder and run it again for a clean tree.

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
