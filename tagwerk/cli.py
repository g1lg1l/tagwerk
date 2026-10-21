"""tagwerk <folder>: analyzes tracks (cached), organizes copies into subfolders, exports CSV, writes tags on request.
tagwerk setup: a few questions instead of flags. tagwerk edit <file|folder>: hand corrections and personal tags.
tagwerk similar <file>: tracks that sound alike. tagwerk dupes <folder>: tracks saved more than once."""
import argparse
import csv
import itertools
import json
import os
import shlex
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

import mutagen
import numpy as np

from .tags import (DUPE_MIN, KEY_RE, LAYOUT, LEARN_EXAMPLES, LEARN_MIN, MOOD_GROUPS, apply_manual, build_tags,
                   clean_filename, clean_title, compatible, fold_bpm, folder_for, mood_of, relabel_styles, tag,
                   track_energy, update_comment)

AUDIO_EXT = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}
MOODS = ("aggressive", "relaxed", "happy", "sad", "party", "danceable", "voice")  # same keys as analyze.HEADS
# one library database wherever you run tagwerk from
DB = str(Path(os.environ.get("TAGWERK_HOME", Path.home() / ".tagwerk")) / "tagwerk.db")
BY = "style,mood,bpm"


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS tracks"
               " (path TEXT PRIMARY KEY, mtime REAL, data TEXT, embedding BLOB, error TEXT)")
    # hand edits live apart from the analysis: re-analyzing a track never loses them
    db.execute("CREATE TABLE IF NOT EXISTS manual (path TEXT PRIMARY KEY, data TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS copies (dest TEXT PRIMARY KEY, src TEXT)")  # what --organize made
    return db


def audio_files(target):
    """A file, or every audio file under a folder. Absolute paths, so the cache works from any directory."""
    p = Path(os.path.abspath(target))
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXT)


def _duration(s):
    return f"{s // 3600:.0f}h{s % 3600 // 60:02.0f}m" if s >= 3600 else f"{s // 60:.0f}m{s % 60:02.0f}s"


@contextmanager
def _working(label):
    """A spinner with elapsed time after label while the block runs, so slow steps never look stuck.
    Yields [label]: change it to update the text. Only in a terminal, logs and pipes stay clean."""
    status = [label]
    if not sys.stdout.isatty():
        yield status
        return
    stop = threading.Event()

    def spin():
        start = time.monotonic()
        for c in itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"):
            print(f"\r\033[K{status[0]} {c} {time.monotonic() - start:.0f}s", end="", flush=True)
            if stop.wait(0.1):
                break

    t = threading.Thread(target=spin, daemon=True)
    t.start()
    try:
        yield status
    finally:
        stop.set()
        t.join()
        print("\r\033[K", end="", flush=True)


def _each(items, what):
    """Iterates items under a spinner showing 'what i/n'."""
    items = list(items)
    with _working(what) as status:
        for i, x in enumerate(items, 1):
            status[0] = f"{what} {i}/{len(items)}"
            yield x


def _add_themes(db):
    """Tracks analyzed before the mood/theme model existed get it from their cached embedding: seconds, not a
    re-analysis."""
    old = db.execute("SELECT path, embedding FROM tracks WHERE embedding IS NOT NULL"
                     " AND json_extract(data, '$.themes') IS NULL").fetchall()
    if not old:
        return
    with _working(f"Adding moods to {len(old)} tracks from the cache"):
        from .analyze import theme_model
        themes = theme_model()
        for p, b in old:
            db.execute("UPDATE tracks SET data = json_set(data, '$.themes', json(?)) WHERE path = ?",
                       (json.dumps(themes(np.frombuffer(b, np.float32))), p))
        db.commit()


def analyze_new(db, files, limit=None):
    """Analyzes only new or modified files; a broken file is logged and retried only if it changes."""
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    todo = [p for p in files if cached.get(str(p)) != p.stat().st_mtime][:limit]
    print(f"{len(files)} files, {len(files) - len(todo)} already analyzed, {len(todo)} to analyze")
    _add_themes(db)
    if not todo:
        return
    with _working("Loading models (the first run also downloads them, ~25 MB)"):
        from .analyze import Analyzer  # heavy import (TensorFlow): only when needed
        analyzer = Analyzer()
    start = time.monotonic()
    for i, p in enumerate(todo, 1):
        left = (time.monotonic() - start) / (i - 1) * (len(todo) - i + 1) if i > 1 else 0
        prefix = f"[{i}/{len(todo)}{f' · {_duration(left)} left' if left else ''}] {p.name}"
        try:
            with _working(prefix):
                r = analyzer.analyze(p)
            emb = r.pop("embedding").astype(np.float32).tobytes()
            db.execute("REPLACE INTO tracks VALUES (?, ?, ?, ?, NULL)", (str(p), p.stat().st_mtime, json.dumps(r), emb))
            line = f"→ {fold_bpm(r['bpm'], r['styles'])} {r['camelot']} {' '.join(build_tags(r))}"
        except Exception as e:
            db.execute("REPLACE INTO tracks VALUES (?, ?, NULL, NULL, ?)", (str(p), p.stat().st_mtime, str(e)))
            line = f"✗ {e}"
        db.commit()
        print(f"{prefix} {line}")


def _load(d, manual):
    r = json.loads(d)
    r["bpm"] = fold_bpm(r["bpm"], r["styles"])  # on read, so improving the rules needs no re-analysis
    r["styles"] = relabel_styles(r["styles"], r["bpm"])
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


def find_dupes(db, files):
    """The same track saved more than once (another format, bitrate or name): near-identical sound and length.
    Groups of paths, the one to keep first: the biggest file, i.e. the best quality at that length."""
    emb = _embeddings(db)
    res = [(p, r) for p, r, _ in _results(db, files) if r and str(p) in emb]
    buckets = defaultdict(list)  # by whole seconds: only tracks of about the same length get compared
    for i, (_, r) in enumerate(res):
        buckets[int(r["duration"])].append(i)
    parent = list(range(len(res)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i, (p, r) in enumerate(res):
        for j in (j for b in (-1, 0, 1) for j in buckets[int(r["duration"]) + b] if j > i):
            q, rq = res[j]
            if abs(rq["duration"] - r["duration"]) <= 1 and emb[str(p)] @ emb[str(q)] >= DUPE_MIN:
                parent[root(j)] = root(i)
    groups = defaultdict(list)
    for i, (p, _) in enumerate(res):
        groups[root(i)].append(p)
    return [sorted(g, key=lambda p: -p.stat().st_size) for g in groups.values() if len(g) > 1]


def _place(d, p, old):
    """Where p's copy goes in d: its cleaned name, or 'name (2)' if a different file already has it.
    old (our previous copy) counts as free, so a copy already in the right place stays put."""
    name = Path(clean_filename(p))
    dest, n = d / name, 2
    while dest.exists() and dest != old and dest.stat().st_size != p.stat().st_size:
        dest, n = d / f"{name.stem} ({n}){name.suffix}", n + 1  # never overwrite
    return dest


def _fresh_copy(p, dest):
    """Copies p to dest, then cleans the title tag of the copy (never the original's). The copy keeps p's mtime:
    that's how the next run tells whether the original changed."""
    shutil.copy2(p, dest)
    try:
        f = mutagen.File(dest, easy=True)
        if f is not None and f.get("title") and (title := clean_title(f["title"][0])) != f["title"][0]:
            f["title"] = title
            f.save()
    except Exception:
        pass  # untaggable copy: it keeps the title it had
    st = p.stat()
    os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


def _prune(folder, out):
    """Removes folders a move left empty, up to out."""
    for f in [folder, *folder.parents]:
        if f == out or not f.is_relative_to(out) or any(f.iterdir()):
            return
        f.rmdir()


def organize(db, files, out, by, compact=False):
    """Copies (never moves) originals into out/<level1>/<level2>/… (compact: out/<level1 - level2 - …>), with
    cleaned names. Remembers its copies, so a re-run after an edit moves the copy to its new folder and refreshes
    it if the original changed (e.g. --write-tags)."""
    skip = {p for g in find_dupes(db, files) for p in g[1:]}
    n = Counter()
    for p, r, _ in _each(_results(db, files), "Organizing"):
        if not r or p in skip:
            continue  # not analyzed yet (--limit), unreadable, or a duplicate of a better copy
        levels = folder_for(r, by)
        d = out / " - ".join(levels) if compact else out.joinpath(*levels)
        d.mkdir(parents=True, exist_ok=True)
        mine = [Path(x) for (x,) in db.execute("SELECT dest FROM copies WHERE src = ?", (str(p),))]
        old = next((x for x in mine if x.is_relative_to(out) and x.exists()), None)
        dest = _place(d, p, old)
        moved = old and old != dest  # new folder (an edit, new layout) or new name (cleaning)
        if moved:
            old.unlink() if dest.exists() else old.rename(dest)
            db.execute("DELETE FROM copies WHERE dest = ?", (str(old),))
            _prune(old.parent, out)
            n["moved or renamed"] += 1
        if not dest.exists():
            _fresh_copy(p, dest)
            n["copied"] += 1
        elif abs(dest.stat().st_mtime - p.stat().st_mtime) > 2:  # copy2 keeps mtime (FAT/exFAT: to 2 s), so the
            # original changed since (e.g. --write-tags)
            _fresh_copy(p, dest)
            n["updated"] += 1
        elif not moved:
            n["already there"] += 1
        db.execute("REPLACE INTO copies VALUES (?, ?)", (str(dest), str(p)))
    db.commit()
    n["duplicates skipped"] = len(skip)
    print(f"Organized into {out}: {', '.join(f'{v} {k}' for k, v in n.items() if v) or 'nothing to do'}")
    if skip:
        print("  tagwerk dupes <folder> lists the duplicates")


def _artist_title(p):
    try:
        t = mutagen.File(p, easy=True)
        return (t.get("artist") or [""])[0], (t.get("title") or [""])[0]
    except Exception:
        return "", ""


def _n(x):
    return str(x).replace(".", ",")


def export_csv(db, files, out):
    cols = ["file", "artist", "title", "bpm", "key", "camelot", "energy", "mood", "style1", "style2", "style3",
            *MOODS, "valence", "arousal", "tags", "error"]
    # BOM + ';' + decimal comma: an Italian-locale Excel opens it right on double click
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        for p, r, err in _each(_results(db, files), "Writing CSV"):
            head = [p.name, *_artist_title(p)]
            if not r:
                w.writerow(head + [""] * (len(cols) - 4) + [err or "not analyzed"])
                continue
            styles = [f"{s.split('---')[-1]} {round(pr * 100)}%" for s, pr in r["styles"]]
            w.writerow(head + [_n(r["bpm"]), r["key"], r["camelot"], track_energy(r), mood_of(r), *styles,
                               *(round(r["moods"][m] * 100) for m in MOODS), _n(r["valence"]), _n(r["arousal"]),
                               " ".join(build_tags(r)), ""])
    print(f"Report: {out}")


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
    print("  moods: " + " · ".join(f"{m} {n}" for m, n in Counter(mood_of(r) for r in done).most_common()))
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
    ap.add_argument("--mood", choices=MOOD_GROUPS)
    ap.add_argument("--add", nargs="+", action="extend", default=[], metavar="TAG", help="e.g. --add warmup peak")
    ap.add_argument("--remove", nargs="+", action="extend", default=[], metavar="TAG",
                    help="drop a tag, also one tagwerk found or learned")
    ap.add_argument("--reset", action="store_true", help="forget all hand edits on these tracks first")
    ap.add_argument("--db", default=DB)
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
    fields = {"style": a.style, "bpm": a.bpm, "key": a.key, "energy": a.energy, "mood": a.mood}
    changes = {k: v for k, v in fields.items() if v is not None}
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
    ap.add_argument("--db", default=DB)
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


def dupes(argv):
    ap = argparse.ArgumentParser(prog="tagwerk dupes", description="Tracks saved more than once (another format, "
                                 "bitrate or name). --organize copies only the first of each group.")
    ap.add_argument("folder")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    groups = find_dupes(open_db(a.db), audio_files(a.folder))
    for g in groups:
        print(g[0].name)
        for i, p in enumerate(g):
            print(f"  {'keep' if i == 0 else 'skip'}  {p.stat().st_size / 1e6:6.1f} MB  {p}")
    n = len(groups)
    print(f"{n} track{'s' * (n > 1)} saved more than once" if n else "No duplicates among the analyzed tracks.")


PRESETS = {"1": ("Style › Mood › BPM", BY), "2": ("Style › BPM", "style,bpm"), "3": ("Mood › BPM", "mood,bpm"),
           "4": ("Key › BPM", "key,bpm"), "5": ("Energy › Style", "energy,style")}


def setup(argv):
    """A few questions instead of flags. Ends with the command it built: re-run that to update."""
    argparse.ArgumentParser(prog="tagwerk setup", description=setup.__doc__).parse_args(argv)

    def ask(question, default):
        return input(f"{question} [{default}]: ").strip() or default

    try:
        while not (files := audio_files(src := Path(ask("Music folder to read", "~/Music")).expanduser())):
            print(f"  no audio files in {src}")
        print(f"  {len(files)} tracks")
        out = Path(ask("Where should the sorted copies go? Originals are never moved", src.parent / "Tagwerk"))
        print("Folder layout:")
        for k, (name, _) in PRESETS.items():
            print(f"  {k}. {name}")
        while True:
            by = ask(f"Pick one, or type levels like key,energy ({', '.join(LAYOUT)})", "1").replace(" ", "")
            by = PRESETS[by][1] if by in PRESETS else by
            if all(k in LAYOUT for k in by.split(",")):
                break
        compact = ask("Compact: one folder per combination (Techno - Dark - 140-144 BPM) instead of nested? y/n",
                      "n").lower().startswith("y")
        tags = ask("Also write tags into the files' Comment field (for rekordbox)? y/n", "n").lower().startswith("y")
        cmd = [str(src), "--organize", str(out.expanduser()), "--by", by] + ["--compact"] * compact
        cmd += ["--write-tags"] * tags
        print(f"\nYour command, run it again any time to update:\n  tagwerk {shlex.join(cmd)}\n")
        if ask("Run it now? y/n", "y").lower().startswith("y"):
            main(cmd)
    except (KeyboardInterrupt, EOFError):
        print()


COMMANDS = {"setup": setup, "edit": edit, "similar": similar, "dupes": dupes}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in COMMANDS and not Path(argv[0]).exists():
        return COMMANDS[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(
        prog="tagwerk", description="Analyze, tag and organize your music (BPM, key, style, mood).",
        epilog="new here? tagwerk setup asks a few questions instead. more: tagwerk edit|similar|dupes --help")
    ap.add_argument("folder", help="folder to analyze (recursive)")
    ap.add_argument("--organize", metavar="OUT",
                    help="where the sorted copies and the report go (default: a Tagwerk folder next to FOLDER)")
    ap.add_argument("--by", default=BY,
                    help=f"subfolder levels for --organize, comma separated: {', '.join(LAYOUT)} (default: {BY})")
    ap.add_argument("--compact", action="store_true",
                    help="one folder per combination (Techno - Dark - 140-144 BPM) instead of nested folders")
    ap.add_argument("--db", default=DB, help=f"library database (default: {DB})")
    ap.add_argument("--csv", help="report file (default: OUT/tagwerk-report.csv)")
    ap.add_argument("--analyze-only", action="store_true", help="just analyze: no copies, no report")
    ap.add_argument("--limit", type=int, help="analyze at most N new tracks (for trying things out)")
    ap.add_argument("--write-tags", action="store_true", help="write tags into the Comment field of the original files")
    a = ap.parse_args(argv)

    by = a.by.split(",")
    if unknown := [k for k in by if k not in LAYOUT]:
        ap.error(f"unknown --by level(s): {', '.join(unknown)}; choose from {', '.join(LAYOUT)}")
    src = Path(os.path.abspath(a.folder))
    base = src if src.is_dir() else src.parent
    out = None if a.analyze_only else Path(a.organize or base.parent / "Tagwerk").expanduser().resolve()
    csv_out = a.csv or (out / "tagwerk-report.csv" if out else None)
    db = open_db(a.db)
    with _working(f"Looking for music in {a.folder}"):
        made = {d for (d,) in db.execute("SELECT dest FROM copies")}
        # never re-analyze our own copies, wherever they are
        files = [p for p in audio_files(src)
                 if str(p.resolve()) not in made and not (out and out in p.resolve().parents)]
    if not files:
        ap.error(f"no audio files in {a.folder}")
    try:
        analyze_new(db, files, a.limit)
    except KeyboardInterrupt:
        sys.exit("\nStopped. Everything analyzed so far is saved: run the same command again to resume.")
    if a.write_tags:
        write_tags(db, files)
    if out:
        organize(db, files, out, by, a.compact)
    if csv_out:
        export_csv(db, files, csv_out)
    summary(db, files)


if __name__ == "__main__":
    main()
