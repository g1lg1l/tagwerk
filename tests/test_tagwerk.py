"""Tests that need no Essentia (tags, folder layout, organize). Run with: python tests/test_tagwerk.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from tagwerk.cli import _results, edit, open_db, organize, summary  # noqa: E402
from tagwerk.tags import (apply_manual, build_tags, camelot, compatible, energy, fold_bpm, folder_for,  # noqa: E402
                          merge_comment, tag)


def test_camelot():
    assert camelot("A", "minor") == "8A"
    assert camelot("C", "major") == "8B"
    assert camelot("Ab", "minor") == camelot("G#", "minor") == "1A"
    assert camelot("B", "major") == "1B"
    assert camelot("E", "major") == "12B"
    assert camelot("F#", "minor") == "11A"
    assert camelot("Bb", "minor") == "3A"
    # 24 keys -> 24 distinct codes
    keys = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    assert len({camelot(k, s) for k in keys for s in ("major", "minor")}) == 24


def test_fold_bpm():
    dnb = [("Electronic---Halftime", 0.4), ("Electronic---Drum n Bass", 0.3), ("Electronic---Dubstep", 0.1)]
    techno = [("Electronic---Techno", 0.6), ("Electronic---Hard Techno", 0.3), ("Electronic---Acid", 0.1)]
    assert fold_bpm(86.8, dnb) == 173.6
    assert fold_bpm(172.3, dnb) == 172.3  # already right
    assert fold_bpm(fold_bpm(86.8, dnb), dnb) == 173.6  # idempotent: safe on cached values
    # real cases: 144 techno / 147 house detected at half time
    assert fold_bpm(72.0, techno) == 144.0
    assert fold_bpm(73.6, [("Electronic---House", 0.5)]) == 147.2
    assert fold_bpm(95.0, techno) == 95.0  # 90+ outside DnB is left alone
    assert fold_bpm(75.0, [("Electronic---Downtempo", 0.5)]) == 75.0  # slow styles really are slow
    assert fold_bpm(80.0, [("Hip Hop---Boom Bap", 0.5)]) == 80.0  # non-electronic left alone


def test_energy():
    assert energy(1) == 1 and energy(9) == 10 and energy(5) == 6
    assert energy(-3) == 1 and energy(12) == 10


def test_merge_comment_preserves_existing():
    assert merge_comment("", ["#techno"]) == "tw: #techno;"
    assert merge_comment(None, ["#techno"]) == "tw: #techno;"
    # Mixed In Key's text stays
    assert merge_comment("8A - Energy 7", ["#techno", "E7"]) == "tw: #techno E7; 8A - Energy 7"
    # running again replaces the block instead of duplicating it
    once = merge_comment("8A - Energy 7", ["#techno"])
    assert merge_comment(once, ["#minimal"]) == "tw: #minimal; 8A - Energy 7"
    # 'tw:' inside a word is not our block
    assert merge_comment("network:x", ["#acid"]) == "tw: #acid; network:x"


def test_build_tags():
    r = {
        "styles": [("Electronic---Dub Techno", 0.6), ("Reggae---Dub", 0.3), ("Electronic---Dub", 0.28)],
        "moods": {"aggressive": 0.1, "relaxed": 0.7, "happy": 0.2, "sad": 0.55, "voice": 0.1},
        "arousal": 4.0,
    }
    assert build_tags(r) == ["#dub-techno", "#dub", "#chill", "#dark", "E4"]
    # the first style is always there, the others only above threshold
    r["styles"] = [("Electronic---Techno", 0.1), ("Electronic---Minimal", 0.05), ("Electronic---Acid", 0.01)]
    assert build_tags(r)[0] == "#techno" and "#minimal" not in build_tags(r)


def test_folder_for():
    r = {
        "styles": [("Electronic---Hard Techno", 0.5), ("Electronic---Schranz", 0.3), ("Electronic---Acid", 0.1)],
        "moods": {"aggressive": 0.8, "relaxed": 0.1, "happy": 0.2, "sad": 0.3, "voice": 0.1},
        "bpm": 144.7, "camelot": "8A", "arousal": 6.0,
    }
    assert folder_for(r, ["style", "bpm"]) == ["Hard Techno", "145-149 BPM"]
    assert folder_for(r, ["energy", "key", "mood", "vocals"]) == ["Energy 7", "8A", "Aggressive", "Instrumental"]
    r["moods"]["aggressive"] = 0.2
    assert folder_for(r, ["mood"]) == ["Neutral"]  # no mood above threshold
    assert folder_for({**r, "bpm": 174.0}, ["bpm"]) == ["170-174 BPM"]
    # style names with characters not allowed on Windows
    r["styles"][0] = ("Folk, World, & Country---Pop/Rock", 0.5)
    assert folder_for(r, ["style"]) == ["Pop-Rock"]


def test_organize_copies_without_overwriting():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        files = {"a/track.mp3": b"same", "b/track.mp3": b"same", "c/track.mp3": b"different!", "d/new.mp3": b"x"}
        for name, data in files.items():
            (tmp / "in" / name).parent.mkdir(parents=True)
            (tmp / "in" / name).write_bytes(data)
        paths = [tmp / "in" / name for name in files]
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 140.0}
        db = open_db(":memory:")
        for p in paths[:3]:  # d/new.mp3 is not analyzed yet
            db.execute("INSERT INTO tracks VALUES (?, 0, ?, NULL, NULL)", (str(p), json.dumps(r)))
        out = tmp / "out"
        dest = out / "Techno" / "140-144 BPM"
        for _ in range(2):  # running again changes nothing
            organize(db, paths, out, ["style", "bpm"])
            # same track in two playlists -> once; same name but different file -> "(2)"; unanalyzed -> skipped
            assert sorted(p.name for p in dest.iterdir()) == ["track (2).mp3", "track.mp3"]
            assert (dest / "track.mp3").read_bytes() == b"same"
            assert (dest / "track (2).mp3").read_bytes() == b"different!"
        assert all(p.exists() for p in paths)  # originals untouched


def test_compatible():
    assert compatible("8A", "8A") and compatible("8A", "8B")
    assert compatible("8A", "9A") and compatible("8A", "7A")
    assert compatible("12A", "1A") and compatible("1B", "12B")  # the wheel wraps
    assert not compatible("8A", "9B") and not compatible("8A", "10A")


def test_tag():
    assert tag("warmup") == tag("#Warmup") == "#warmup"
    assert tag(" Peak Time ") == "#peak-time"
    assert tag("a;b") == "#ab"  # ';' would end our Comment block


def test_manual_overrides():
    r = {
        "styles": [("Electronic---Techno", 0.6), ("Electronic---Dub Techno", 0.3), ("Electronic---Minimal", 0.1)],
        "moods": {"aggressive": 0.1, "relaxed": 0.1, "happy": 0.1, "sad": 0.7, "voice": 0.1},
        "bpm": 128.0, "key": "A minor", "camelot": "8A", "arousal": 5.0,
    }
    apply_manual(r, {"style": "Dub Techno", "bpm": 126.0, "key": "5A", "energy": 3,
                     "add": ["#warmup"], "remove": ["#dark"]})
    assert r["styles"][:2] == [("Dub Techno", 1.0), ("Electronic---Techno", 0.6)]  # no duplicate Dub Techno
    assert r["bpm"] == 126.0 and r["camelot"] == "5A"
    assert build_tags(r) == ["#dub-techno", "#techno", "#warmup", "E3"]
    assert folder_for(r, ["style", "bpm", "energy", "key"]) == ["Dub Techno", "125-129 BPM", "Energy 3", "5A"]
    r["learned"] = ["#closing", "#dark"]
    assert build_tags(r) == ["#dub-techno", "#techno", "#warmup", "#closing", "E3"]  # your remove beats learning


def test_edit_learn_summary():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        vecs = {f"warm{i}": [1, i * .02, 0] for i in range(6)}  # 6 examples: enough for #warm-up to spread
        vecs |= {"like_warm0": [1, -.01, 0], "like_warm5": [1, .11, 0], "peak": [0, 1, 0], "like_peak": [.1, 1, 0],
                 "odd": [0, 0, 1]}
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 130.0, "key": "A minor", "camelot": "8A",
             "arousal": 5.0, "moods": dict.fromkeys(("aggressive", "relaxed", "happy", "sad", "voice"), 0.0)}
        db_path = str(tmp / "t.db")
        db = open_db(db_path)
        (tmp / "warmup").mkdir()
        paths = {}
        for n, v in vecs.items():
            p = paths[n] = tmp / ("warmup" if n.startswith("warm") else "") / f"{n}.mp3"
            p.write_bytes(b"x")
            db.execute("INSERT INTO tracks VALUES (?, 0, ?, ?, NULL)",
                       (str(p), json.dumps(r), np.array(v, np.float32).tobytes()))
        db.commit()
        tags = lambda: {p.stem: build_tags(r) for p, r, _ in _results(db, list(paths.values()))}  # noqa: E731
        plain, warm = ["#techno", "E6"], ["#techno", "#warm-up", "E6"]

        edit([str(tmp / "warmup"), "--add", "Warm Up", "--db", db_path])  # a whole folder at once
        edit([str(paths["peak"]), "--add", "peak", "--bpm", "140", "--db", db_path])
        t = tags()
        assert t["warm0"] == t["like_warm0"] == t["like_warm5"] == warm  # learned from the closest example
        assert t["peak"] == ["#techno", "#peak", "E6"] and t["like_peak"] == plain  # 1 example: doesn't spread
        assert t["odd"] == plain  # sounds like nothing you tagged
        assert _results(db, [paths["peak"]])[0][1]["bpm"] == 140.0

        edit([str(paths["warm5"]), "--remove", "warm up", "--db", db_path])  # a correction is a "not this" example
        t = tags()
        assert t["warm5"] == t["like_warm5"] == plain and t["like_warm0"] == warm

        edit([str(paths["warm5"]), str(paths["peak"]), "--reset", "--db", db_path])
        assert db.execute("SELECT COUNT(*) FROM manual").fetchone()[0] == 5  # warm0-4 still edited
        summary(db, list(paths.values()))  # smoke test: must not crash


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
