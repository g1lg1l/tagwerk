"""tagwerk <folder>: analyzes tracks (cached), organizes copies into subfolders, exports CSV, writes tags on request.
tagwerk setup: a few questions instead of flags. tagwerk edit <file|folder>: hand corrections and personal tags.
tagwerk similar <file>: tracks that sound alike. tagwerk search <words>: find tracks.
tagwerk dupes <folder>: tracks saved more than once."""
import argparse
import csv
import itertools
import json
import os
import re
import shlex
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import mutagen
import numpy as np

from .tags import (DUPE_MIN, KEY_RE, LAYOUT, LEARN_EXAMPLES, LEARN_MIN, MOOD_GROUPS, apply_manual, build_tags,
                   clean_filename, clean_title, compatible, fold_bpm, folder_for, key_name, mood_of, read_fields,
                   relabel_styles, tag, track_energy, update_comment, write_fields)

AUDIO_EXT = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}
MOODS = ("aggressive", "relaxed", "happy", "sad", "party", "danceable", "voice")  # same keys as analyze.HEADS
# one library database wherever you run tagwerk from
DB = str(Path(os.environ.get("TAGWERK_HOME", Path.home() / ".tagwerk")) / "tagwerk.db")
BY = "style,mood,bpm"
# tracks analyzed at once. On a 10-core M-series one worker already keeps ~3.4 cores busy (TensorFlow threads):
# 16 tracks took 146 s with 1, 88 s with 2, 76 s with 4-5 at 2 TF threads each, worse beyond
JOBS = max(1, min(4, (os.cpu_count() or 2) // 2))


def _version():
    """pyproject.toml when running from a checkout: an editable install's metadata keeps the version it was
    installed with."""
    toml = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if toml.exists() and (m := re.search(r'^version = "(.+)"', toml.read_text(), re.M)):
        return m[1]
    try:
        return version("tagwerk")
    except PackageNotFoundError:
        return "dev"


VERSION = _version()

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")  # https://no-color.org
BOLD, DIM, RED, GREEN, YELLOW, CYAN = "1", "2", "31", "32", "33", "36"


def _c(style, text):
    return f"\033[{style}m{text}\033[0m" if _COLOR else str(text)


def _home(p):
    """/Users/gil/Music/x -> ~/Music/x: shorter lines."""
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s


def _fit(name, width):
    return (name if len(name) <= width else name[:width - 1] + "…").ljust(width)


@contextmanager
def _quiet_native():
    """TensorFlow's C++ side logs (absl, MLIR) straight to file descriptor 2, past every Python setting: mute it
    while models load and run. Python errors still arrive as exceptions."""
    sys.stderr.flush()
    saved = os.dup(2)
    with open(os.devnull, "w") as null:
        os.dup2(null.fileno(), 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def open_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS tracks"
               " (path TEXT PRIMARY KEY, mtime REAL, data TEXT, embedding BLOB, error TEXT)")
    # hand edits live apart from the analysis: re-analyzing a track never loses them
    db.execute("CREATE TABLE IF NOT EXISTS manual (path TEXT PRIMARY KEY, data TEXT)")
    # what --organize made; tagged = what was last written into the copy's tags
    db.execute("CREATE TABLE IF NOT EXISTS copies (dest TEXT PRIMARY KEY, src TEXT, tagged TEXT)")
    if "tagged" not in [c[1] for c in db.execute("PRAGMA table_info(copies)")]:  # databases from tagwerk < 0.6
        db.execute("ALTER TABLE copies ADD COLUMN tagged TEXT")
    return db


def disk_path(target):
    """The absolute path as the disk spells it: symlinks resolved, and every name with the disk's letter case and
    accents (macOS stores 'û' decomposed, a typed or pasted path has it composed). Cache keys must match exactly."""
    p = Path(os.path.realpath(os.path.expanduser(target)))
    out = Path(p.anchor)
    for part in p.parts[1:]:
        try:
            names = os.listdir(out)
        except OSError:
            names = []
        if part not in names:
            want = unicodedata.normalize("NFC", part).casefold()
            part = next((n for n in names if unicodedata.normalize("NFC", n).casefold() == want), part)
        out /= part
    return out


def _hidden(parts):
    """macOS '._x.mp3' twins, .Trashes, .Spotlight… and Windows' recycle bin: never music."""
    return any(s.startswith(".") or s.upper() == "$RECYCLE.BIN" for s in parts)


def audio_files(target):
    """A file, or every audio file under a folder, spelled as on disk, so the cache works from any directory."""
    p = disk_path(target)
    if p.is_file():
        return [p]
    return sorted(f for f in p.rglob("*")
                  if f.suffix.lower() in AUDIO_EXT and not _hidden(f.relative_to(p).parts) and f.is_file())


def _duration(s):
    return f"{s // 3600:.0f}h{s % 3600 // 60:02.0f}m" if s >= 3600 else f"{s // 60:.0f}m{s % 60:02.0f}s"


_LOCK = threading.Lock()  # the spinner and _say share the line


def _say(line):
    """Prints a line, above a running spinner if there is one."""
    with _LOCK:
        print(f"\r\033[K{line}" if sys.stdout.isatty() else line, flush=True)


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
            with _LOCK:
                print(f"\r\033[K{status[0]} {_c(CYAN, c)} {_c(DIM, f'{time.monotonic() - start:.0f}s')}", end="",
                      flush=True)
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
    with _working(f"Adding moods to {len(old)} tracks from the cache"), _quiet_native():
        from .analyze import theme_model
        themes = theme_model()
        for p, b in old:
            db.execute("UPDATE tracks SET data = json_set(data, '$.themes', json(?)) WHERE path = ?",
                       (json.dumps(themes(np.frombuffer(b, np.float32))), p))
        db.commit()


def _mtimes(files):
    """One stat per file, at scan time: a file renamed or deleted meanwhile is just left out."""
    out = {}
    for p in files:
        try:
            out[p] = p.stat().st_mtime
        except OSError:
            pass
    return out


def _rehome(db, mtimes):
    """A renamed or moved set folder keeps its analysis, hand edits and copies: a path that's gone, with the same
    file name and mtime as a new one, is the same file."""
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    gone = {(Path(q).name, m): q for q, m in cached.items() if not os.path.exists(q)}
    moved = 0
    for p, m in mtimes.items():
        if str(p) not in cached and (old := gone.pop((p.name, m), None)):
            for table, col in (("tracks", "path"), ("manual", "path"), ("copies", "src")):
                db.execute(f"UPDATE OR IGNORE {table} SET {col} = ? WHERE {col} = ?", (str(p), old))
            moved += 1
    db.commit()
    if moved:
        print(_c(DIM, f"{moved} tracks moved or renamed since last time: kept their analysis and edits"))


def analyze_new(db, files, limit=None, jobs=JOBS):
    """Analyzes only new or modified files, `jobs` at a time; a broken file is logged and retried only if it
    changes."""
    mtimes = _mtimes(files)
    _rehome(db, mtimes)
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    new = [p for p, m in mtimes.items() if cached.get(str(p)) != m]
    todo = new[:limit]
    later = f" (--limit: {len(new) - len(todo)} left for next time)" if len(todo) < len(new) else ""
    print(f"{_c(BOLD, len(files))} tracks · {len(files) - len(new)} already analyzed · "
          f"{_c(BOLD, len(todo))} to analyze{later}")
    _add_themes(db)
    if not todo:
        return
    with _working("Getting the models ready (the first run downloads them, ~30 MB)"), _quiet_native():
        from . import analyze  # heavy import (TensorFlow): only when needed
        analyze.fetch_models()
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from concurrent.futures.process import BrokenProcessPool
    from multiprocessing import get_context
    jobs = max(1, min(jobs, len(todo)))
    # each worker's TensorFlow would grab every core; sharing them out beats letting them fight (inherited by spawn)
    os.environ.setdefault("TF_NUM_INTRAOP_THREADS", str(max(1, (os.cpu_count() or 2) // jobs)))
    os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")
    start, width, digits = time.monotonic(), min(40, max(len(p.name) for p in todo)), len(str(len(todo)))
    # spawn: TensorFlow doesn't survive fork; the workers inherit the muted stderr. An executor, not a Pool: a
    # worker that dies (out of memory, a native crash) ends the run with a message instead of hanging it
    with _quiet_native(), ProcessPoolExecutor(jobs, mp_context=get_context("spawn"),
                                              initializer=analyze.init_worker) as pool, \
            _working(f"Loading models in {jobs} worker{'s' * (jobs > 1)}") as status:
        futures = [pool.submit(analyze.analyze_file, p) for p in todo]
        try:
            for i, f in enumerate(as_completed(futures), 1):
                p, r, err = f.result()
                if r:
                    emb = r.pop("embedding")
                    db.execute("REPLACE INTO tracks VALUES (?, ?, ?, ?, NULL)", (str(p), mtimes[p], json.dumps(r), emb))
                    r = _load(json.dumps(r), {})  # as every other command sees it: half-time fixed, styles relabelled
                    bpm, key, tags = f"{r['bpm']:5.1f}", f"{r['camelot']:>3}", " ".join(build_tags(r))
                    line = f"{_c(BOLD, bpm)} {_c(CYAN, key)}  {_c(DIM, tags)}"
                else:
                    db.execute("REPLACE INTO tracks VALUES (?, ?, NULL, NULL, ?)", (str(p), mtimes[p], err))
                    line = _c(RED, f"✗ {err}")
                db.commit()
                left = (time.monotonic() - start) / i * (len(todo) - i)
                status[0] = f"Analyzing {jobs} at a time" + (f" · {_duration(left)} left" if i < len(todo) else "")
                _say(f"{_c(DIM, f'{i:>{digits}}/{len(todo)}')}  {_fit(p.name, width)}  {line}")
        except BrokenProcessPool:
            pool.shutdown(wait=False, cancel_futures=True)
            raise SystemExit(f"\n{_c(RED, 'A worker crashed or could not load the models')} (out of memory? try "
                             "--jobs 1). Everything analyzed so far is saved: run again to resume.")
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise


def _load(d, manual):
    r = json.loads(d)
    r["bpm"] = fold_bpm(r["bpm"], r["styles"])  # on read, so improving the rules needs no re-analysis
    r["styles"] = relabel_styles(r["styles"], manual.get("bpm", r["bpm"]))
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
    examples = Counter(t for m in manual.values() for t in m.get("add", []))
    # removing an automatic tag (a false #vocal) is a correction, not a "none of your tags" example
    labeled = [p for p, m in manual.items()
               if (m.get("add") or set(m.get("remove", [])) & set(examples)) and rows.get(p, (None,))[0]]
    if not labeled:
        return
    emb = _embeddings(db)
    labeled = [p for p in labeled if p in emb]
    skip = set(labeled)
    others = [p for p, (r, _) in rows.items() if r and p in emb and p not in skip]
    if not labeled or not others:
        return
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
    same = defaultdict(list)  # the same file under two paths (a hardlink, a symlink): updated together
    for p in files:
        try:
            st = p.stat()
            same[st.st_dev, st.st_ino].append(p)
        except OSError:
            pass
    for p, r, _ in _results(db, files):
        if not r or r.get("written") == (tags := build_tags(r)):
            continue
        try:
            update_comment(p, tags)
        except Exception as e:  # e.g. file open in another program
            print(_c(RED, f"✗ {p.name}: tags not written ({e})"), file=sys.stderr)
            continue
        # store the new mtime, otherwise next run would see the file as modified and re-analyze it
        st = p.stat()
        db.executemany("UPDATE tracks SET data = json_set(data, '$.written', json(?)), mtime = ? WHERE path = ?",
                       [(json.dumps(tags), st.st_mtime, str(q)) for q in same[st.st_dev, st.st_ino] or [p]])
        db.commit()
        print(f"{_c(GREEN, '✎')} {p.name}  {_c(DIM, ' '.join(tags))}")


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
    while dest.exists() and not _same(dest, old) and dest.stat().st_size != p.stat().st_size:
        dest, n = d / f"{name.stem} ({n}){name.suffix}", n + 1  # never overwrite
    return old if _same(dest, old) else dest


def _same(a, b):
    """Same file on disk: 'Techno/x.mp3' and 'techno/x.mp3' are one file on macOS and Windows."""
    try:
        return b is not None and a.samefile(b)
    except OSError:
        return False


def _style(r):
    return r["styles"][0][0].split("---")[-1]


def _tag_copy(p, dest, r):
    """Writes what tagwerk knows into its own copy (never the original): clean title, BPM, key, style as genre and
    the tags in Comment, so any DJ app shows them without analyzing. The copy keeps p's mtime: that's how the
    next run tells whether the original changed."""
    _, title = _artist_title(p)
    fields = {"bpm": str(round(r["bpm"])), "initialkey": key_name(r["key"]), "genre": _style(r)}
    if title:
        fields["title"] = clean_title(title)
    try:
        write_fields(dest, fields)
        update_comment(dest, build_tags(r))
    except Exception as e:  # untaggable copy: it stays as it was
        _say(_c(YELLOW, f"! couldn't tag {dest.name}: {e}"))
    st = p.stat()
    os.utime(dest, ns=(st.st_atime_ns, st.st_mtime_ns))


def _location(p):
    """file://localhost/… as rekordbox wants it. Under WSL, /mnt/c/… becomes C:/…: rekordbox runs on Windows."""
    s = str(p)
    if m := re.match(r"^/mnt/([a-z])/(.*)", s):
        s = f"{m[1].upper()}:/{m[2]}"
    return "file://localhost/" + urllib.parse.quote(s.lstrip("/"), safe="/:")


def write_rekordbox(entries, path):
    """rekordbox.xml: the copies as a collection, plus a Tagwerk folder of playlists mirroring the folders and one
    playlist per tag of yours. entries: (copy path, result, [folder levels])."""
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="tagwerk", Version=VERSION, Company="")
    coll = ET.SubElement(root, "COLLECTION", Entries=str(len(entries)))
    tree, mine = {}, defaultdict(list)  # tree: nested dicts of folder names, leaves are lists of track ids
    for i, (dest, r, levels) in enumerate(entries, 1):
        artist, title = _artist_title(dest)
        ET.SubElement(coll, "TRACK", TrackID=str(i), Name=title or dest.stem, Artist=artist, Genre=_style(r),
                      TotalTime=str(round(r.get("duration", 0))), AverageBpm=f"{r['bpm']:.2f}",
                      Tonality=key_name(r["key"]), Comments=" ".join(build_tags(r)), Location=_location(dest))
        node = tree
        for name in levels[:-1]:
            node = node.setdefault(name, {})
        node.setdefault(levels[-1], []).append(i)
        for t in r["manual"].get("add", []) + r.get("learned", []):
            mine[t].append(i)
    if mine:
        tree["Your tags"] = dict(sorted(mine.items()))

    def add(parent, name, content):
        if isinstance(content, list):  # a playlist
            pl = ET.SubElement(parent, "NODE", Type="1", Name=name, KeyType="0", Entries=str(len(content)))
            for i in content:
                ET.SubElement(pl, "TRACK", Key=str(i))
        else:  # a folder
            folder = ET.SubElement(parent, "NODE", Type="0", Name=name, Count=str(len(content)))
            for k in sorted(content):
                add(folder, k, content[k])

    top = ET.SubElement(ET.SubElement(root, "PLAYLISTS"), "NODE", Type="0", Name="ROOT", Count="1")
    add(top, "Tagwerk", tree)
    for el in root.iter():  # one control character in a tag would make the whole file unreadable for rekordbox
        for k, v in el.attrib.items():
            el.attrib[k] = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


_OS_JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _prune(folder, out):
    """Removes folders a move left empty (Finder's .DS_Store and Explorer's Thumbs.db don't count), up to out."""
    for f in [folder, *folder.parents]:
        if f == out or not f.is_relative_to(out) or not f.exists():
            return
        if any(x.name not in _OS_JUNK for x in f.iterdir()):
            return
        for x in f.iterdir():
            x.unlink()
        f.rmdir()


def _copy(p, dest):
    """Through a .part file: an interrupted copy (Ctrl-C, a full disk) never leaves a truncated track under the real
    name for a DJ app to import. A .part isn't audio, so no scan ever picks it up."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(p, tmp)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def organize(db, files, out, by, compact=False):
    """Copies (never moves) originals into out/<level1>/<level2>/… (compact: out/<level1 - level2 - …>), with
    cleaned names. Remembers its copies, so a re-run after an edit moves the copy to its new folder and refreshes
    it if the original changed (e.g. --write-tags)."""
    skip = {p for g in find_dupes(db, files) for p in g[1:]}
    n, entries = Counter(), []
    # a better file of a track arrived (the FLAC of an mp3): its old copy goes, the original is still there
    for dest, src in db.execute("SELECT dest, src FROM copies").fetchall():
        if Path(src) in skip and Path(dest).is_relative_to(out):
            Path(dest).unlink(missing_ok=True)
            db.execute("DELETE FROM copies WHERE dest = ?", (dest,))
            _prune(Path(dest).parent, out)
            n["replaced by a better file"] += 1
    db.commit()
    for p, r, _ in _each(_results(db, files), "Organizing"):
        if not r or p in skip:
            continue  # not analyzed yet (--limit), unreadable, or a duplicate of a better copy
        levels = folder_for(r, by)
        d = out / " - ".join(levels) if compact else out.joinpath(*levels)
        d.mkdir(parents=True, exist_ok=True)
        mine = {Path(x): t for x, t in db.execute("SELECT dest, tagged FROM copies WHERE src = ?", (str(p),))}
        old = next((x for x in mine if x.is_relative_to(out) and x.exists()), None)
        dest = _place(d, p, old)
        moved = old and old != dest  # new folder (an edit, new layout) or new name (cleaning)
        if moved:
            old.unlink() if dest.exists() else old.rename(dest)
            db.execute("DELETE FROM copies WHERE dest = ?", (str(old),))
            _prune(old.parent, out)
            n["moved or renamed"] += 1
        # copy2 keeps the original's mtime (FAT/exFAT: to 2 s). Only an original NEWER than its copy (--write-tags,
        # a re-download) refreshes it: a copy changed by Serato, Traktor or Mixed In Key (cues, grids) is theirs
        fresh = not dest.exists() or p.stat().st_mtime - dest.stat().st_mtime > 2
        if fresh:
            n["copied" if not dest.exists() else "updated"] += 1
            _copy(p, dest)
        elif not moved:
            n["already there"] += 1
        title = clean_title(_artist_title(p)[1])
        tagged = json.dumps([round(r["bpm"]), key_name(r["key"]), _style(r), build_tags(r), title])
        if fresh or mine.get(old) != tagged:  # new copy, or an edit / new rule changed what it should say
            _tag_copy(p, dest, r)
        db.execute("REPLACE INTO copies VALUES (?, ?, ?)", (str(dest), str(p), tagged))
        db.commit()  # per track: an interrupted run keeps what it did, and other tagwerk commands aren't locked out
        entries.append((dest, r, [" - ".join(levels)] if compact else levels))
    n["duplicates skipped"] = len(skip)
    counts = ", ".join(f"{v} {k}" for k, v in n.items() if v) or "nothing to do"
    print(f"{_c(GREEN, '✓')} Sorted  {_c(BOLD, _home(out))}  {_c(DIM, counts)}")
    if skip:
        print(_c(DIM, "          tagwerk dupes <folder> lists the duplicates"))
    if entries:
        write_rekordbox(entries, out / "rekordbox.xml")
        print(f"{_c(GREEN, '✓')} rekordbox  {_c(BOLD, _home(out / 'rekordbox.xml'))}")


def _artist_title(p):
    return tuple(read_fields(p))


def _n(x):
    return str(x).replace(".", ",")


def export_csv(db, files, out):
    cols = ["file", "artist", "title", "bpm", "key", "camelot", "energy", "mood", "style1", "style2", "style3",
            *MOODS, "valence", "arousal", "tags", "error"]
    # BOM + ';' + decimal comma: an Italian-locale Excel opens it right on double click
    Path(out).parent.mkdir(parents=True, exist_ok=True)
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
    print(f"{_c(GREEN, '✓')} Report  {_c(BOLD, _home(out))}")


def summary(db, files):
    """A quick look at the library: top styles as bars, BPM range, hand-tagged and learned counts."""
    results = _results(db, files)
    done = [r for _, r, _ in results if r]
    if not done:
        return
    bpms = [r["bpm"] for r in done]
    print(f"\n{_c(BOLD, f'{len(done)} tracks')} · {min(bpms):.0f}–{max(bpms):.0f} BPM")
    styles = Counter(r["styles"][0][0].split("---")[-1] for r in done).most_common()
    top, width = styles[0][1], max(len(s) for s, _ in styles[:8])
    for s, n in styles[:8]:
        print(f"  {s:<{width}}  {_c(CYAN, '█' * max(1, round(n / top * 30)))} {n}")
    if len(styles) > 8:
        print(_c(DIM, f"  … {len(styles) - 8} more styles"))
    moods = Counter(mood_of(r) for r in done).most_common()
    print(f"  {_c(DIM, 'moods')}  " + " · ".join(f"{m} {_c(BOLD, n)}" for m, n in moods))
    hand, learned = sum(bool(r["manual"]) for r in done), sum(bool(r.get("learned")) for r in done)
    broken = sum(1 for _, r, e in results if e)
    extra = [f"{n} {what}" for n, what in [(hand, "edited by hand"), (learned, "tagged from your examples"),
                                           (broken, "unreadable (see --csv)")] if n]
    if extra:
        print(_c(DIM, "  " + " · ".join(extra)))


def _originals(db, files):
    """A sorted copy in Tagwerk/ (the file Finder and DJ apps hand out) stands for its original."""
    src = dict(db.execute("SELECT dest, src FROM copies"))
    return list(dict.fromkeys(Path(src.get(str(f.resolve()), f)) for f in files))


def _known_style(db, style):
    """The spelling the library already uses: 'dub techno' -> 'Dub Techno', so one style stays one folder."""
    names = {}
    for (d,) in db.execute("SELECT data FROM tracks WHERE data IS NOT NULL"):
        for s, _ in json.loads(d)["styles"]:
            names.setdefault(s.split("---")[-1].lower(), s.split("---")[-1])
    for (m,) in db.execute("SELECT data FROM manual"):
        if "style" in (m := json.loads(m)):
            names.setdefault(m["style"].lower(), m["style"])
    return names.get(style.lower(), style)


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
    ap.add_argument("--mood", type=str.capitalize, choices=MOOD_GROUPS)
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
    files = _originals(db, files)
    a.style = a.style and _known_style(db, a.style)
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
            tags = " ".join(build_tags(r))
            print(f"{p.name}  {_c(BOLD, r['bpm'])} {_c(CYAN, r['camelot'])} {mood_of(r)}  {_c(DIM, tags)}")
        else:
            print(f"{p.name}  {_c(YELLOW, 'not analyzed yet')} ({err or 'run tagwerk on its folder'}), "
                  "edits apply once it is")


def similar(argv):
    ap = argparse.ArgumentParser(prog="tagwerk similar", description="Tracks that sound most like FILE (#12).")
    ap.add_argument("file")
    ap.add_argument("-n", type=int, default=10, help="how many (default: 10)")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    db = open_db(a.db)
    emb, me = _embeddings(db), str(_originals(db, [disk_path(a.file)])[0])
    if me not in emb:
        ap.error(f"{a.file} is not analyzed yet: run tagwerk on its folder first")
    paths = [p for p in emb if p != me and Path(p).exists()]
    if not paths:
        sys.exit("No other analyzed tracks yet.")
    sims = np.stack([emb[p] for p in paths]) @ emb[me]
    order = [i for i in sims.argsort()[::-1] if sims[i] < 0.9999][:a.n]  # 1.0 = the same file in another playlist
    (_, ref, _), *rest = _results(db, [Path(me)] + [Path(paths[i]) for i in order])
    print(f"Like {_c(BOLD, Path(me).name)} ({ref['bpm']} BPM, {ref['camelot']})  "
          + _c(DIM, "✓ = mixable: ±6% BPM, compatible key"))
    for i, (p, r, _) in zip(order, rest):
        ok = abs(r["bpm"] / ref["bpm"] - 1) <= 0.06 and compatible(r["camelot"], ref["camelot"])
        style, sim, key = r["styles"][0][0].split("---")[-1], f"{sims[i]:.2f}", f"{r['camelot']:>3}"
        mark = _c(GREEN, "✓") if ok else " "
        print(f"  {_c(DIM, sim)} {mark} {r['bpm']:>5.1f} {_c(CYAN, key)}  {_fit(style, 18)} {p.name}")


# words that only make a query read well
_STOP = {"a", "an", "and", "the", "with", "some", "of", "in", "for", "track", "tracks", "song", "songs", "music",
         "vibe", "vibes", "style", "kind", "like", "very", "bit", "little", "really", "bpm"}
DNB = ("drum", "n", "bass")
_ALIASES = {("hardgroove",): ("schranz",),  # the model's name for it (shown as Hardgroove below 150 BPM)
            ("dnb",): DNB, ("d&b",): DNB, ("drum", "and", "bass"): DNB, ("drum", "&", "bass"): DNB,
            ("drum&bass",): DNB,
            ("chill",): ("relaxed",), ("sad",): ("emotional",)}


def _words(text):
    """#tags whole (#warm-up, #già), BPM ranges whole (138-142), everything else by word."""
    return tuple(re.findall(r"#[^\s#]+|\d{2,3}-\d{2,3}|[a-z0-9&]+", text.lower()))


def search_scores(query, results, styles, style_names):
    """Ranks tracks for a query in tagwerk's own vocabulary: the 400 styles, the 56 mood/theme labels and the mood
    groups (scores), BPM like 140 or 138-142, Camelot keys and #tags (filters). Every term must fit, so the score is
    the geometric mean of each term's score relative to the library's best. Returns (scores, understood, ignored).
    results: analyzed tracks; styles: their (n, 400) style scores."""
    # ponytail: a fixed vocabulary, not free text; CLAP would understand "rolling" but needs PyTorch (#12)
    concepts = {}

    def concept(phrase, vec):
        concepts[phrase] = np.maximum(concepts[phrase], vec) if phrase in concepts else vec

    for i, name in enumerate(style_names):
        concept(_words(name.split("---")[-1]), styles[:, i])
    labels = results[0].get("themes") or {}
    for label in labels:
        concept((label,), np.array([r["themes"][label] for r in results]))
    for group, members in MOOD_GROUPS.items():
        concept(_words(group), np.array([max(r["themes"][m] for m in members) for r in results]))
    for word, mood in [("aggressive", "aggressive"), ("relaxed", "relaxed"), ("happy", "happy"), ("vocal", "voice"),
                       ("party", "party"), ("danceable", "danceable")]:
        concept((word,), np.array([r["moods"][mood] for r in results]))
    concept(("instrumental",), 1 - concepts[("vocal",)])
    style_phrases = {_words(n.split("---")[-1]) for n in style_names}
    for k, r in enumerate(results):  # a hand-set style or mood counts as a perfect score for that track
        for v in (r["manual"].get("style"), r["manual"].get("mood")):
            if v:
                w = _ALIASES.get(_words(v), _words(v))
                concepts[w] = concepts.get(w, np.zeros(len(results))).copy()
                concepts[w][k] = concepts[w].max() or 1

    words, i, terms, keep, understood, ignored = _words(query), 0, [], np.ones(len(results), bool), [], []
    while i < len(words):
        for size in (4, 3, 2, 1):
            phrase = words[i:i + size]
            phrase = _ALIASES.get(phrase, phrase)
            if len(words[i:i + size]) == size and phrase in concepts:
                terms.append((concepts[phrase], phrase))
                understood.append(" ".join(words[i:i + size]))
                i += size
                break
        else:
            w = words[i]
            if m := re.fullmatch(r"(\d{2,3})(?:-(\d{2,3}))?", w):
                lo, hi = (int(m[1]) - 3, int(m[1]) + 3) if not m[2] else (int(m[1]), int(m[2]))
                keep &= np.array([lo <= r["bpm"] <= hi for r in results])
                understood.append(f"{lo}-{hi} BPM")
            elif KEY_RE.match(w.upper()):
                keep &= np.array([compatible(r["camelot"], w.upper()) for r in results])
                understood.append(f"mixes with {w.upper()}")
            elif w.startswith("#"):
                keep &= np.array([tag(w) in build_tags(r) for r in results])
                understood.append(tag(w))
            elif w not in _STOP:
                ignored.append(w)
            i += 1
    score = np.ones(len(results))
    for t, phrase in terms:
        # relative to the library's best, but a style nobody in the library has (0.001 at best) isn't a full match
        floor = 0.05 if phrase in style_phrases else 0
        score *= (t / (max(t.max(), floor) or 1) + 1e-6) ** (1 / len(terms))
    return np.where(keep, score, 0), understood, ignored


def search(argv):
    ap = argparse.ArgumentParser(
        prog="tagwerk search", description="Find tracks by words: styles (dub techno, acid…), moods (dark, deep, "
        "emotional…), BPM (140, 138-142), keys (8A: what mixes with it) and your #tags. Every word must fit.",
        epilog='e.g. tagwerk search "dark acid techno 140"')
    ap.add_argument("query", nargs="+")
    ap.add_argument("-n", type=int, default=15, help="how many (default: 15)")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    db = open_db(a.db)
    rows = [(Path(p), b) for p, b in db.execute("SELECT path, embedding FROM tracks WHERE embedding IS NOT NULL")]
    rows = [(p, b) for p, b in rows if p.exists()]
    if not rows:
        sys.exit("No analyzed tracks yet: run tagwerk on a folder first.")
    _add_themes(db)  # tracks from tagwerk < 0.4
    results = [r for _, r, _ in _results(db, [p for p, _ in rows])]
    with _working("Reading the library"), _quiet_native():
        from .analyze import STYLES, _head  # the style head on cached embeddings: seconds, no re-analysis
        head, names = _head(STYLES)
        styles = head(np.stack([np.frombuffer(b, np.float32) for _, b in rows]))
    score, understood, ignored = search_scores(" ".join(a.query), results, styles, names)
    if not understood:
        ap.error("no word I know: try styles (dub techno), moods (dark, deep), BPM (140), keys (8A) or #tags")
    print(f"{_c(DIM, 'looking for')} {' · '.join(_c(BOLD, u) for u in understood)}"
          + (_c(YELLOW, f"   not understood: {', '.join(ignored)}") if ignored else ""))
    order = [i for i in score.argsort()[::-1] if score[i] > 0][:a.n]
    for i in order:
        (p, _), r = rows[i], results[i]
        width = max(1, round(score[i] * 10))
        bar, key = _c(CYAN, "█" * width) + " " * (10 - width), f"{r['camelot']:>3}"
        print(f"  {bar} {r['bpm']:>5.1f} {_c(CYAN, key)}  {_fit(_style(r), 16)} {_fit(mood_of(r), 10)} {p.name}")
    if not order:
        print(_c(YELLOW, "  nothing fits all of that"))


def dupes(argv):
    ap = argparse.ArgumentParser(prog="tagwerk dupes", description="Tracks saved more than once (another format, "
                                 "bitrate or name). --organize copies only the first of each group.")
    ap.add_argument("folder")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args(argv)
    groups = find_dupes(open_db(a.db), audio_files(a.folder))
    for g in groups:
        print(_c(BOLD, g[0].name))
        for i, p in enumerate(g):
            mark = _c(GREEN, "keep") if i == 0 else _c(DIM, "skip")
            print(f"  {mark}  {p.stat().st_size / 1e6:6.1f} MB  {_home(p)}")
    n = len(groups)
    print(f"{n} track{'s' * (n > 1)} saved more than once" if n else "No duplicates among the analyzed tracks.")


def default_out(src):
    """A Tagwerk folder next to the music; inside it when the music is a drive's root (/Volumes/STICK, /mnt/d),
    whose parent nobody may write to."""
    base = src if src.is_dir() else src.parent
    if os.path.ismount(base) or not os.access(base.parent, os.W_OK):
        return base / "Tagwerk"
    return base.parent / "Tagwerk"


PRESETS = {"1": ("Style › Mood › BPM", BY), "2": ("Style › BPM", "style,bpm"), "3": ("Mood › BPM", "mood,bpm"),
           "4": ("Key › BPM", "key,bpm"), "5": ("Energy › Style", "energy,style")}


def setup(argv):
    """A few questions instead of flags. Ends with the command it built: re-run that to update."""
    argparse.ArgumentParser(prog="tagwerk setup", description=setup.__doc__).parse_args(argv)

    def ask(question, default):
        return input(f"{_c(BOLD, '?')} {question} {_c(DIM, f'[{_home(default)}]')} ").strip() or str(default)

    def ask_path(question, default):
        """A folder dragged into Terminal arrives quoted or with escaped spaces: take it as the shell would."""
        s = ask(question, default)
        try:
            s = shlex.split(s)[0] if s[:1] in "'\"" or ("\\" in s and "/" in s) else s
        except (ValueError, IndexError):
            pass
        return Path(s).expanduser().absolute()

    print(f"{_c(BOLD, f'tagwerk {VERSION}')} setup: a few questions, Enter keeps the default.\n")
    try:
        while not (files := audio_files(src := ask_path("Music folder to read", "~/Music"))):
            print(_c(YELLOW, f"  no audio files in {_home(src)}"))
        print(_c(DIM, f"  {len(files)} tracks"))
        out = ask_path("Where should the sorted copies go? Originals are never moved", default_out(src))
        print("  Folder layout:")
        for k, (name, _) in PRESETS.items():
            print(f"    {_c(CYAN, k)}  {name}")
        while True:
            by = ask(f"Pick one, or type levels like key,energy ({', '.join(LAYOUT)})", "1").replace(" ", "").lower()
            by = PRESETS[by][1] if by in PRESETS else by
            if not (unknown := [k for k in by.split(",") if k not in LAYOUT]):
                break
            print(_c(YELLOW, f"  unknown level(s): {', '.join(unknown)}"))
        compact = ask("Compact: one folder per combination (Techno - Dark - 140-144 BPM) instead of nested? y/n",
                      "n").lower().startswith("y")
        tags = ask("Also write tags into the files' Comment field (for rekordbox)? y/n", "n").lower().startswith("y")
        cmd = [str(src), "--organize", str(out), "--by", by] + ["--compact"] * compact
        cmd += ["--write-tags"] * tags
        print(f"\nYour command, run it again any time to update:\n  {_c(BOLD, 'tagwerk ' + shlex.join(cmd))}\n")
        if ask("Run it now? y/n", "y").lower().startswith("y"):
            main(cmd)
    except (KeyboardInterrupt, EOFError):
        print()


COMMANDS = {"setup": setup, "edit": edit, "similar": similar, "search": search, "dupes": dupes}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in COMMANDS and argv[0] not in os.listdir():  # exact name: 'Setup/' doesn't hide setup
        return COMMANDS[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(
        prog="tagwerk", description="Analyze, tag and organize your music (BPM, key, style, mood).",
        epilog="new here? tagwerk setup asks a few questions instead. more: tagwerk edit|similar|search|dupes --help")
    ap.add_argument("--version", action="version", version=f"tagwerk {VERSION}")
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
    ap.add_argument("--jobs", type=int, default=JOBS, help=f"tracks analyzed at once (default here: {JOBS})")
    ap.add_argument("--write-tags", action="store_true", help="write tags into the Comment field of the original files")
    a = ap.parse_args(argv)

    by = a.by.split(",")
    if unknown := [k for k in by if k not in LAYOUT]:
        ap.error(f"unknown --by level(s): {', '.join(unknown)}; choose from {', '.join(LAYOUT)}")
    src = disk_path(a.folder)
    out = None if a.analyze_only else (disk_path(a.organize) if a.organize else default_out(src))
    if out and (out == src or out in src.parents):
        ap.error(f"--organize {_home(out)} contains {a.folder}: pick a folder outside it")
    csv_out = a.csv or (out / "tagwerk-report.csv" if out else None)
    begin = time.monotonic()
    print(f"{_c(BOLD, f'tagwerk {VERSION}')}  {_home(src)}" + (f" {_c(DIM, '→')} {_home(out)}" if out else ""))
    db = open_db(a.db)
    with _working(f"Looking for music in {a.folder}"):
        made = {d for (d,) in db.execute("SELECT dest FROM copies")}
        # never re-analyze our own copies, wherever they are
        files = [p for p in audio_files(src)
                 if str(p.resolve()) not in made and not (out and out in p.resolve().parents)]
    if not files:
        ap.error(f"no audio files in {a.folder}")
    try:
        analyze_new(db, files, a.limit, a.jobs)
    except KeyboardInterrupt:
        sys.exit("\nStopped. Everything analyzed so far is saved: run the same command again to resume.")
    except urllib.error.URLError as e:
        sys.exit(f"{_c(RED, 'Could not download the models')} (offline?): {e.reason}")
    # every set sorted into this OUT before stays in its report and rekordbox.xml, not just this run's folder
    have = {str(p) for p in files}
    every = files + [Path(q) for q in dict.fromkeys(q for d, q in db.execute("SELECT dest, src FROM copies")
                                                    if out and Path(d).is_relative_to(out) and q not in have)
                     if Path(q).exists()]
    try:
        if a.write_tags:
            write_tags(db, files)
        if out:
            organize(db, every, out, by, a.compact)
        if csv_out:
            export_csv(db, every, csv_out)
    except KeyboardInterrupt:
        sys.exit("\nStopped. What's done is saved: run the same command again to finish.")
    summary(db, files)
    print(_c(DIM, f"\nDone in {_duration(time.monotonic() - begin)}"))


if __name__ == "__main__":
    main()
