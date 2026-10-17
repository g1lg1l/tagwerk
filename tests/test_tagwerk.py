"""Tests that need no Essentia (tags, folder layout, organize). Run with: python tests/test_tagwerk.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tagwerk.cli import open_db, organize  # noqa: E402
from tagwerk.tags import build_tags, camelot, energy, fold_bpm, folder_for, merge_comment  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
