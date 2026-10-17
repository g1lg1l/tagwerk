"""tagwerk <cartella>: analizza i pezzi (con cache), esporta CSV, scrive i tag nei file su richiesta."""
import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

import mutagen
import numpy as np

from .tags import build_tags, energy, update_comment

AUDIO_EXT = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".ogg"}
MOODS = ("aggressive", "relaxed", "happy", "sad", "party", "danceable", "voice")  # stesse chiavi di analyze.HEADS


def open_db(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS tracks"
               " (path TEXT PRIMARY KEY, mtime REAL, data TEXT, embedding BLOB, error TEXT)")
    return db


def analyze_new(db, files, limit=None):
    """Analizza solo i file nuovi o modificati; un file rotto viene loggato e riprovato solo se cambia."""
    cached = dict(db.execute("SELECT path, mtime FROM tracks"))
    todo = [p for p in files if cached.get(str(p)) != p.stat().st_mtime][:limit]
    print(f"{len(files)} file, {len(files) - len(todo)} già analizzati, {len(todo)} da analizzare")
    if not todo:
        return
    from .analyze import Analyzer  # import pesante (TensorFlow): solo se serve
    analyzer = Analyzer()
    for i, p in enumerate(todo, 1):
        try:
            r = analyzer.analyze(p)
            emb = r.pop("embedding").astype(np.float32).tobytes()
            db.execute("REPLACE INTO tracks VALUES (?, ?, ?, ?, NULL)", (str(p), p.stat().st_mtime, json.dumps(r), emb))
            print(f"[{i}/{len(todo)}] {p.name} → {r['bpm']} {r['camelot']} {' '.join(build_tags(r))}")
        except Exception as e:
            db.execute("REPLACE INTO tracks VALUES (?, ?, NULL, NULL, ?)", (str(p), p.stat().st_mtime, str(e)))
            print(f"[{i}/{len(todo)}] {p.name} ✗ {e}", file=sys.stderr)
        db.commit()


def _results(db, files):
    rows = {p: (json.loads(d) if d else None, e) for p, d, e in db.execute("SELECT path, data, error FROM tracks")}
    return [(p, *rows.get(str(p), (None, None))) for p in files]


def write_tags(db, files):
    """Scrive i tag nel Comment. I tag si ricalcolano dai dati in cache, quindi cambiare soglie non richiede rianalisi."""
    for p, r, _ in _results(db, files):
        if not r or r.get("written") == (tags := build_tags(r)):
            continue
        try:
            update_comment(p, tags)
        except Exception as e:  # es. file aperto in un altro programma
            print(f"{p.name}: tag non scritti ({e})", file=sys.stderr)
            continue
        r["written"] = tags
        # salvo il nuovo mtime, altrimenti al prossimo giro il file sembrerebbe modificato e verrebbe rianalizzato
        db.execute("UPDATE tracks SET data = ?, mtime = ? WHERE path = ?", (json.dumps(r), p.stat().st_mtime, str(p)))
        db.commit()
        print(f"{p.name}: {' '.join(tags)}")


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
    # BOM + ';' + virgola decimale: l'Excel italiano lo apre giusto col doppio click
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols)
        for p, r, err in _results(db, files):
            head = [p.name, *_artist_title(p)]
            if not r:
                w.writerow(head + [""] * (len(cols) - 4) + [err or "non analizzato"])
                continue
            styles = [f"{s.split('---')[-1]} {round(pr * 100)}%" for s, pr in r["styles"]]
            w.writerow(head + [_n(r["bpm"]), r["key"], r["camelot"], energy(r["arousal"]), *styles,
                               *(round(r["moods"][m] * 100) for m in MOODS), _n(r["valence"]), _n(r["arousal"]),
                               " ".join(build_tags(r)), ""])
    print(f"CSV: {out}")


def main():
    ap = argparse.ArgumentParser(prog="tagwerk", description="Analizza e tagga la tua musica (BPM, key, stile, mood).")
    ap.add_argument("folder", help="cartella da analizzare (ricorsiva)")
    ap.add_argument("--db", default="tagwerk.db", help="cache dei risultati (default: tagwerk.db)")
    ap.add_argument("--csv", help="esporta i risultati in un CSV")
    ap.add_argument("--limit", type=int, help="analizza al massimo N pezzi nuovi (per provare)")
    ap.add_argument("--write-tags", action="store_true", help="scrive i tag nel campo Comment dei file")
    a = ap.parse_args()

    files = sorted(p for p in Path(a.folder).rglob("*") if p.suffix.lower() in AUDIO_EXT)
    db = open_db(a.db)
    analyze_new(db, files, a.limit)
    if a.write_tags:
        write_tags(db, files)
    if a.csv:
        export_csv(db, files, a.csv)


if __name__ == "__main__":
    main()
