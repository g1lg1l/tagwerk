"""tagwerk <folder>: analyzes tracks (cached), organizes copies into subfolders, exports CSV, writes tags on request.
tagwerk edit <file|folder>: hand corrections and personal tags. tagwerk similar <file>: tracks that sound alike."""
import argparse
import csv
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import mutagen
import numpy as np

from .tags import (KEY_RE, LAYOUT, LEARN_EXAMPLES, LEARN_MIN, apply_manual, build_tags, compatible, fold_bpm, folder_for, tag,
                   track_energy, update_comment)

AUDIO_EXT = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}
MOODS = ("aggressive", "relaxed", "happy", "sad", "party", "danceable", "voice")  # same keys as analyze.HEADS


def open_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS tracks"
               " (path TEXT PRIMARY KEY, mtime REAL, data TEXT, embedding BLOB, error TEXT)")
    # hand edits live apart from the analysis: re-analyzing a track never loses them
    db.execute("CREATE TABLE IF NOT EXISTS manual (path TEXT PRIMARY KEY, data TEXT)")
    return db


def audio_files(target):
    """A file, or every audio file under a folder. Absolute paths, so the cache works from any directory."""
    p = Path(os.path.abspath(target))
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXT)


def _duration(s):
    return f"{s // 3600:.0f}h{s % 3600 // 60:02.0f}m" if s >= 3600 else f"{s // 60:.0f}m{s % 60:02.0f}s"


def analyze_new(db, files, limit=None):
    """Analyzes only new or modified files; a broken file is logged and retried only if it changes."""
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    todo = [p for p in files if cached.get(str(p)) != p.stat().st_mtime][:limit]
    print(f"{len(files)} files, {len(files) - len(todo)} already analyzed, {len(todo)} to analyze")
    if not todo:
        return
    from .analyze import Analyzer  # heavy import (TensorFlow): only when needed
    analyzer = Analyzer()
    start = time.monotonic()
    for i, p in enumerate(todo, 1):
        try:
            r = analyzer.analyze(p)
            emb = r.pop("embedding").astype(np.float32).tobytes()
            db.execute("REPLACE INTO tracks VALUES (?, ?, ?, ?, NULL)", (str(p), p.stat().st_mtime, json.dumps(r), emb))
            line = f"{p.name} → {fold_bpm(r['bpm'], r['styles'])} {r['camelot']} {' '.join(build_tags(r))}"
        except Exception as e:
            db.execute("REPLACE INTO tracks VALUES (?, ?, NULL, NULL, ?)", (str(p), p.stat().st_mtime, str(e)))
            line = f"{p.name} ✗ {e}"
        db.commit()
        left = (time.monotonic() - start) / i * (len(todo) - i)
        print(f"[{i}/{len(todo)}{f' · {_duration(left)} left' if i < len(todo) else ''}] {line}")


def _load(d, manual):
    r = json.loads(d)
    r["bpm"] = fold_bpm(r["bpm"], r["styles"])  # on read, so improving the rule needs no re-analysis
    return apply_manual(r, manual)


def _embeddings(db):
    """path -> unit-length embedding, so a dot product is the cosine similarity."""
    out = {}
    for p, b in db.execute("SELECT path, embedding FROM tracks WHERE embedding IS NOT NULL"):
        e = np.frombuffer(b, np.float32)
        out[p] = e / (np.linalg.norm(e) or 1)
    return out


def _learn(db, rows, manual):
    """Personal categories (#11): every track you never hand-tagged takes the hand tags of its most similar
    hand-tagged track. A track where you only removed tags counts as a "none of these" example."""
    # ponytail: 1-nearest-neighbour, a per-tag classifier if it turns out noisy on a real library
    labeled = [p for p, m in manual.items() if ("add" in m or "remove" in m) and rows.get(p, (None,))[0]]
    if not labeled:
        return
    emb = _embeddings(db)
    labeled = [p for p in labeled if p in emb]
    skip = set(labeled)
    others = [p for p, (r, _) in rows.items() if r and p in emb and p not in skip]
    if not labeled or not others:
        return
    examples = Counter(t for m in manual.values() for t in m.get("add", []))
    sims = np.stack([emb[p] for p in others]) @ np.stack([emb[p] for p in labeled]).T
    for p, row in zip(others, sims):
        if row.max() >= LEARN_MIN:
            near = manual[labeled[row.argmax()]].get("add", [])
            rows[p][0]["learned"] = [t for t in near if examples[t] >= LEARN_EXAMPLES]  # one-offs stay put


def _results(db, files):
    manual = {p: json.loads(m) for p, m in db.execute("SELECT path, data FROM manual")}
    rows = {p: (_load(d, manual.get(p, {})) if d else None, e)
            for p, d, e in db.execute("SELECT path, data, error FROM tracks")}
    _learn(db, rows, manual)
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
        # store the new mtime, otherwise next run would see the file as modified and re-analyze it
        db.execute("UPDATE tracks SET data = json_set(data, '$.written', json(?)), mtime = ? WHERE path = ?",
                   (json.dumps(tags), p.stat().st_mtime, str(p)))
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
            w.writerow(head + [_n(r["bpm"]), r["key"], r["camelot"], track_energy(r), *styles,
                               *(round(r["moods"][m] * 100) for m in MOODS), _n(r["valence"]), _n(r["arousal"]),
                               " ".join(build_tags(r)), ""])
    print(f"CSV: {out}")


def summary(db, files):
    """A quick look at the library: top styles as bars, BPM range, hand-tagged and learned counts."""
    results = _results(db, files)
    done = [r for _, r, _ in results if r]
    if not done:
        return
    bpms = [r["bpm"] for r in done]
    print(f"\n{len(done)} tracks · {min(bpms):.0f}–{max(bpms):.0f} BPM")
    styles = Counter(r["styles"][0][0].split("---")[-1] for r in done).most_common()
    top, width = styles[0][1], max(len(s) for s, _ in styles[:8])
    for s, n in styles[:8]:
        print(f"  {s:<{width}}  {'█' * max(1, round(n / top * 30))} {n}")
    if len(styles) > 8:
        print(f"  … {len(styles) - 8} more styles")
    hand, learned = sum(bool(r["manual"]) for r in done), sum(bool(r.get("learned")) for r in done)
    broken = sum(1 for _, r, e in results if e)
    extra = [f"{n} {what}" for n, what in [(hand, "edited by hand"), (learned, "tagged from your examples"),
                                           (broken, "unreadable (see --csv)")] if n]
    if extra:
        print("  " + " · ".join(extra))


def edit(argv):
    ap = argparse.ArgumentParser(
        prog="tagwerk edit",
        description="Correct the analysis or tag tracks by hand. Edits live in the cache, not in the files: "
                    "apply them with tagwerk <folder> --write-tags / --organize / --csv. "
                    "Tracks you never tagged learn your tags from the most similar track you did.")
    ap.add_argument("targets", nargs="+", metavar="FILE_OR_FOLDER", help="a folder edits every track inside it")
    ap.add_argument("--style", help='main style, e.g. "Dub Techno"')
    ap.add_argument("--bpm", type=float)
    ap.add_argument("--key", help="Camelot key, e.g. 8A")
    ap.add_argument("--energy", type=int, choices=range(1, 11), metavar="1-10")
    ap.add_argument("--add", nargs="+", action="extend", default=[], metavar="TAG", help="e.g. --add warmup peak")
    ap.add_argument("--remove", nargs="+", action="extend", default=[], metavar="TAG",
                    help="drop a tag, also one tagwerk found or learned")
    ap.add_argument("--reset", action="store_true", help="forget all hand edits on these tracks first")
    ap.add_argument("--db", default="tagwerk.db")
    a = ap.parse_args(argv)
    a.key = a.key and a.key.upper()
    if a.key and not KEY_RE.match(a.key):
        ap.error("--key takes a Camelot code like 8A or 11B")
    if a.bpm is not None and not 40 <= a.bpm <= 250:
        ap.error("--bpm must be between 40 and 250")
    files = [f for t in a.targets for f in audio_files(t)]
    if not files:
        ap.error("no audio files found")

    db = open_db(a.db)
    changes = {k: v for k, v in [("style", a.style), ("bpm", a.bpm), ("key", a.key), ("energy", a.energy)]
               if v is not None}
    add, remove = [tag(t) for t in a.add], [tag(t) for t in a.remove]
    if a.reset or changes or add or remove:
        for p in files:
            row = db.execute("SELECT data FROM manual WHERE path = ?", (str(p),)).fetchone()
            m = {} if a.reset or not row else json.loads(row[0])
            m.update(changes)
            if add or remove:
                m["add"] = [t for t in dict.fromkeys(m.get("add", []) + add) if t not in remove]
                m["remove"] = [t for t in dict.fromkeys(m.get("remove", []) + remove) if t not in add]
            if m:
                db.execute("REPLACE INTO manual VALUES (?, ?)", (str(p), json.dumps(m)))
            else:
                db.execute("DELETE FROM manual WHERE path = ?", (str(p),))
        db.commit()
    for p, r, err in _results(db, files):  # with no options, edit just shows what tagwerk thinks
        if r:
            print(f"{p.name}: {r['bpm']} {r['camelot']} {' '.join(build_tags(r))}")
        else:
            print(f"{p.name}: not analyzed yet ({err or 'run tagwerk on its folder'}), edits apply once it is")


def similar(argv):
    ap = argparse.ArgumentParser(prog="tagwerk similar", description="Tracks that sound most like FILE (#12).")
    ap.add_argument("file")
    ap.add_argument("-n", type=int, default=10, help="how many (default: 10)")
    ap.add_argument("--db", default="tagwerk.db")
    a = ap.parse_args(argv)
    db = open_db(a.db)
    emb, me = _embeddings(db), os.path.abspath(a.file)
    if me not in emb:
        ap.error(f"{a.file} is not analyzed yet: run tagwerk on its folder first")
    paths = [p for p in emb if p != me and Path(p).exists()]
    if not paths:
        sys.exit("No other analyzed tracks yet.")
    sims = np.stack([emb[p] for p in paths]) @ emb[me]
    order = [i for i in sims.argsort()[::-1] if sims[i] < 0.9999][:a.n]  # 1.0 = the same file in another playlist
    (_, ref, _), *rest = _results(db, [Path(me)] + [Path(paths[i]) for i in order])
    print(f"Like {Path(me).name} ({ref['bpm']} BPM, {ref['camelot']}); ✓ = mixable (±6% BPM, compatible key)")
    for i, (p, r, _) in zip(order, rest):
        ok = abs(r["bpm"] / ref["bpm"] - 1) <= 0.06 and compatible(r["camelot"], ref["camelot"])
        style = r["styles"][0][0].split("---")[-1]
        print(f"  {sims[i]:.2f} {'✓' if ok else ' '} {r['bpm']:>5.1f} {r['camelot']:>3}  {style[:18]:<18} {p.name}")


COMMANDS = {"edit": edit, "similar": similar}


def main():
    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS and not Path(sys.argv[1]).exists():
        return COMMANDS[sys.argv[1]](sys.argv[2:])
    ap = argparse.ArgumentParser(
        prog="tagwerk", description="Analyze, tag and organize your music (BPM, key, style, mood).",
        epilog="more: tagwerk edit --help (fix results, tag by hand) · tagwerk similar --help (tracks alike)")
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
    files = audio_files(a.folder)
    if a.organize:
        out = Path(a.organize).resolve()
        files = [p for p in files if out not in p.resolve().parents]  # never re-analyze our own copies
    if not files:
        ap.error(f"no audio files in {a.folder}")
    db = open_db(a.db)
    try:
        analyze_new(db, files, a.limit)
    except KeyboardInterrupt:
        sys.exit("\nStopped. Everything analyzed so far is saved: run the same command again to resume.")
    if a.write_tags:
        write_tags(db, files)
    if a.organize:
        organize(db, files, out, by)
    if a.csv:
        export_csv(db, files, a.csv)
    summary(db, files)


if __name__ == "__main__":
    main()
