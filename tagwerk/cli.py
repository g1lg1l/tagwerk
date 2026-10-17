"""tagwerk <folder>: analyzes tracks (cached), organizes copies into subfolders, exports CSV, writes tags on request."""
import argparse
import csv
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import mutagen
import numpy as np

from .tags import LAYOUT, build_tags, energy, fold_bpm, folder_for, update_comment

AUDIO_EXT = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}
MOODS = ("aggressive", "relaxed", "happy", "sad", "party", "danceable", "voice")  # same keys as analyze.HEADS


def open_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS tracks"
               " (path TEXT PRIMARY KEY, mtime REAL, data TEXT, embedding BLOB, error TEXT)")
    return db


def analyze_new(db, files, limit=None):
    """Analyzes only new or modified files; a broken file is logged and retried only if it changes."""
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    todo = [p for p in files if cached.get(str(p)) != p.stat().st_mtime][:limit]
    print(f"{len(files)} files, {len(files) - len(todo)} already analyzed, {len(todo)} to analyze")
    if not todo:
        return
    from .analyze import Analyzer  # heavy import (TensorFlow): only when needed
    analyzer = Analyzer()
    for i, p in enumerate(todo, 1):
        try:
            r = analyzer.analyze(p)
            emb = r.pop("embedding").astype(np.float32).tobytes()
            db.execute("REPLACE INTO tracks VALUES (?, ?, ?, ?, NULL)", (str(p), p.stat().st_mtime, json.dumps(r), emb))
            print(f"[{i}/{len(todo)}] {p.name} → {fold_bpm(r['bpm'], r['styles'])} {r['camelot']} {' '.join(build_tags(r))}")
        except Exception as e:
            db.execute("REPLACE INTO tracks VALUES (?, ?, NULL, NULL, ?)", (str(p), p.stat().st_mtime, str(e)))
            print(f"[{i}/{len(todo)}] {p.name} ✗ {e}", file=sys.stderr)
        db.commit()


def _load(d):
    r = json.loads(d)
    r["bpm"] = fold_bpm(r["bpm"], r["styles"])  # on read, so improving the rule needs no re-analysis
    return r


def _results(db, files):
    rows = {p: (_load(d) if d else None, e) for p, d, e in db.execute("SELECT path, data, error FROM tracks")}
    return [(p, *rows.get(str(p), (None, None))) for p in files]


def write_tags(db, files):
    """Writes tags into Comment. Tags are recomputed from the cache, so changing thresholds needs no re-analysis."""
    for p, r, _ in _results(db, files):
        if not r or r.get("written") == (tags := build_tags(r)):
            continue
        try:
            update_comment(p, tags)
        except Exception as e:  # e.g. file open in another program
            print(f"{p.name}: tags not written ({e})", file=sys.stderr)
            continue
        r["written"] = tags
        # store the new mtime, otherwise next run would see the file as modified and re-analyze it
        db.execute("UPDATE tracks SET data = ?, mtime = ? WHERE path = ?", (json.dumps(r), p.stat().st_mtime, str(p)))
        db.commit()
        print(f"{p.name}: {' '.join(tags)}")


def organize(db, files, out, by):
    """Copies (never moves) every analyzed track to out/<level1>/<level2>/…; the originals stay where they are."""
    copied = skipped = 0
    for p, r, _ in _results(db, files):
        if not r:
            continue  # not analyzed yet (--limit) or unreadable
        d = out.joinpath(*folder_for(r, by))
        d.mkdir(parents=True, exist_ok=True)
        dest, n = d / p.name, 2
        while dest.exists() and dest.stat().st_size != p.stat().st_size:
            dest, n = d / f"{p.stem} ({n}){p.suffix}", n + 1  # same name, different file: never overwrite
        if dest.exists():
            skipped += 1  # already copied on a previous run, or the same track sits in two playlists
            continue
        shutil.copy2(p, dest)
        copied += 1
    print(f"Organized into {out}: {copied} copied, {skipped} already there")


def _artist_title(p):
    try:
        t = mutagen.File(p, easy=True)
        return (t.get("artist") or [""])[0], (t.get("title") or [""])[0]
    except Exception:
        return "", ""


def _n(x):
    return str(x).replace(".", ",")


def export_csv(db, files, out):
    cols = ["file", "artist", "title", "bpm", "key", "camelot", "energy", "style1", "style2", "style3",
            *MOODS, "valence", "arousal", "tags", "error"]
    # BOM + ';' + decimal comma: an Italian-locale Excel opens it right on double click
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        for p, r, err in _results(db, files):
            head = [p.name, *_artist_title(p)]
            if not r:
                w.writerow(head + [""] * (len(cols) - 4) + [err or "not analyzed"])
                continue
            styles = [f"{s.split('---')[-1]} {round(pr * 100)}%" for s, pr in r["styles"]]
            w.writerow(head + [_n(r["bpm"]), r["key"], r["camelot"], energy(r["arousal"]), *styles,
                               *(round(r["moods"][m] * 100) for m in MOODS), _n(r["valence"]), _n(r["arousal"]),
                               " ".join(build_tags(r)), ""])
    print(f"CSV: {out}")


def main():
    ap = argparse.ArgumentParser(prog="tagwerk", description="Analyze, tag and organize your music (BPM, key, style, mood).")
    ap.add_argument("folder", help="folder to analyze (recursive)")
    ap.add_argument("--organize", metavar="OUT", help="copy analyzed tracks into OUT, sorted into subfolders")
    ap.add_argument("--by", default="style,bpm",
                    help=f"subfolder levels for --organize, comma separated: {', '.join(LAYOUT)} (default: style,bpm)")
    ap.add_argument("--db", default="tagwerk.db", help="results cache (default: tagwerk.db)")
    ap.add_argument("--csv", help="export results to a CSV file")
    ap.add_argument("--limit", type=int, help="analyze at most N new tracks (for trying things out)")
    ap.add_argument("--write-tags", action="store_true", help="write tags into the Comment field of the original files")
    a = ap.parse_args()

    by = a.by.split(",")
    if unknown := [k for k in by if k not in LAYOUT]:
        ap.error(f"unknown --by level(s): {', '.join(unknown)}; choose from {', '.join(LAYOUT)}")
    files = sorted(p for p in Path(a.folder).rglob("*") if p.suffix.lower() in AUDIO_EXT)
    if a.organize:
        out = Path(a.organize).resolve()
        files = [p for p in files if out not in p.resolve().parents]  # never re-analyze our own copies
    db = open_db(a.db)
    analyze_new(db, files, a.limit)
    if a.write_tags:
        write_tags(db, files)
    if a.organize:
        organize(db, files, out, by)
    if a.csv:
        export_csv(db, files, a.csv)


if __name__ == "__main__":
    main()
