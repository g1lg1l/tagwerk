"""Tests that need no Essentia (tags, folder layout, organize). Run with: python tests/test_tagwerk.py"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from tagwerk.cli import (MOODS, _copy, _learn, _location, _mtimes, _prune, _results, audio_files,  # noqa: E402
                         default_out, disk_path, edit, export_csv, find_dupes, main, open_db, organize, search_scores,
                         summary, write_tags)
from tagwerk.tags import (apply_manual, build_tags, camelot, clean_filename, clean_title, compatible,  # noqa: E402
                          energy, fold_bpm, folder_for, key_name, merge_comment, read_fields, relabel_styles, tag,
                          update_comment, win_safe, write_fields)

THEMES = dict.fromkeys(["energetic", "melodic", "deep", "dark", "emotional", "sad", "melancholic", "groovy",
                        "uplifting"], 0.01)


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
    assert fold_bpm(96.0, [("Electronic---Downtempo", 0.8), ("Electronic---Drum n Bass", 0.02)]) == 96.0


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
        "styles": [("Electronic---Dub Techno", 0.6), ("Reggae---Dub", 0.45), ("Electronic---Dub", 0.42)],
        "moods": {"aggressive": 0.1, "relaxed": 0.7, "happy": 0.2, "sad": 0.55, "voice": 0.1},
        "arousal": 4.0,
    }
    assert build_tags(r) == ["#dub-techno", "#dub", "#chill", "E4"]  # no mood/theme scores yet: no mood tag
    r["themes"] = THEMES | {"deep": 0.4}
    assert build_tags(r) == ["#dub-techno", "#dub", "#deep", "#chill", "E4"]
    # the first style is always there, the others only above threshold
    r["styles"] = [("Electronic---Techno", 0.1), ("Electronic---Minimal", 0.05), ("Electronic---Acid", 0.01)]
    assert build_tags(r)[0] == "#techno" and "#minimal" not in build_tags(r)


def test_relabel_styles():
    styles = [("Electronic---Techno", 0.8), ("Electronic---Schranz", 0.6), ("Electronic---Hard Techno", 0.5)]
    assert relabel_styles(styles, 144.0)[1] == ("Electronic---Hardgroove", 0.6)
    assert relabel_styles(styles, 155.0) == styles  # real schranz tempo
    from tagwerk.cli import _load
    r = {"bpm": 147.0, "styles": styles}
    assert _load(json.dumps(r), {"bpm": 156.0})["styles"][1][0] == "Electronic---Schranz"  # the hand-set BPM counts


def test_folder_for():
    r = {
        "styles": [("Electronic---Hard Techno", 0.5), ("Electronic---Schranz", 0.3), ("Electronic---Acid", 0.1)],
        "moods": {"aggressive": 0.8, "relaxed": 0.1, "happy": 0.2, "sad": 0.3, "voice": 0.1},
        "bpm": 144.7, "camelot": "8A", "arousal": 6.0,
    }
    assert folder_for(r, ["style", "bpm"]) == ["Hard Techno", "145-149 BPM"]
    r["themes"] = THEMES | {"energetic": 0.13, "melodic": 0.12}
    assert folder_for(r, ["energy", "key", "mood", "vocals"]) == ["Energy 7", "8A", "Energetic", "Instrumental"]
    r["themes"] |= {"melancholic": 0.2}  # Emotional covers emotional, sad and melancholic; no cutoff, the top wins
    assert folder_for(r, ["mood"]) == ["Emotional"]
    assert folder_for({**r, "themes": None}, ["mood"]) == ["Unknown"]
    r["manual"] = {"mood": "Dark"}  # tagwerk edit --mood
    assert folder_for(r, ["mood"]) == ["Dark"]
    assert folder_for({**r, "bpm": 174.0}, ["bpm"]) == ["170-174 BPM"]
    # style names with characters not allowed on Windows
    r["styles"][0] = ("Folk, World, & Country---Pop/Rock", 0.5)
    assert folder_for(r, ["style"]) == ["Pop-Rock"]


def test_organize_copies_without_overwriting():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        files = {"a/track.mp3": b"same", "b/track.mp3": b"same", "c/track.mp3": b"different!", "d/new.mp3": b"x"}
        for name, data in files.items():
            (tmp / "in" / name).parent.mkdir(parents=True)
            (tmp / "in" / name).write_bytes(data)
        paths = [tmp / "in" / name for name in files]
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 140.0, "key": "A minor", "camelot": "8A",
             "arousal": 5.0, "moods": dict.fromkeys(MOODS, 0.0)}
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


def _library(tmp, tracks):
    """tracks: {relative path: (bytes, embedding, style)} -> (db, paths), all analyzed."""
    db, paths = open_db(":memory:"), []
    for name, (data, vec, style) in tracks.items():
        p = tmp / "in" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        r = {"styles": [(f"Electronic---{style}", 0.9)], "bpm": 140.0, "duration": 300.0, "key": "A minor",
             "camelot": "8A", "arousal": 5.0, "moods": dict.fromkeys(MOODS, 0.0)}
        db.execute("INSERT INTO tracks VALUES (?, 0, ?, ?, NULL)",
                   (str(p), json.dumps(r), np.array(vec, np.float32).tobytes()))
        paths.append(p)
    return db, paths


def test_organize_follows_edits_and_updates():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        db, (p,) = _library(tmp, {"a/track.mp3": (b"v1", [1, 0], "Techno")})
        out = tmp / "out"
        organize(db, [p], out, ["style", "bpm"])
        assert (out / "Techno" / "140-144 BPM" / "track.mp3").exists()

        db.execute("INSERT INTO manual VALUES (?, ?)", (str(p), json.dumps({"style": "House"})))
        organize(db, [p], out, ["style", "bpm"])  # the copy follows the edit, the empty folders go
        assert [str(f.relative_to(out)) for f in out.rglob("*.mp3")] == ["House/140-144 BPM/track.mp3"]
        assert not (out / "Techno").exists()

        p.write_bytes(b"v2 with tags")  # e.g. --write-tags changed the original
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
        organize(db, [p], out, ["style", "bpm"])
        assert (out / "House" / "140-144 BPM" / "track.mp3").read_bytes() == b"v2 with tags"
        assert p.read_bytes() == b"v2 with tags"  # original untouched


def test_organize_clean_names_and_compact():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        db, (p,) = _library(tmp, {"a/07. Artist - Track (Free Download).mp3": (b"x", [1, 0], "Techno")})
        out = tmp / "out"
        organize(db, [p], out, ["style", "bpm"])
        assert [str(f.relative_to(out)) for f in out.rglob("*.mp3")] == ["Techno/140-144 BPM/Artist - Track.mp3"]
        organize(db, [p], out, ["style", "bpm"], compact=True)  # switching layout moves the copy, no leftovers
        assert sorted(str(f.relative_to(out)) for f in out.rglob("*")) == [
            "Techno - 140-144 BPM", "Techno - 140-144 BPM/Artist - Track.mp3", "rekordbox.xml"]
        assert p.exists() and p.name.startswith("07. ")  # original untouched


def test_rekordbox_xml():
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        db, paths = _library(tmp, {"a/one.mp3": (b"1", [1, 0], "Techno"), "a/two two.mp3": (b"22", [0, 1], "House")})
        db.execute("INSERT INTO manual VALUES (?, ?)", (str(paths[1]), json.dumps({"add": ["#warmup"]})))
        out = tmp / "out"
        organize(db, paths, out, ["style", "bpm"])
        root = ET.parse(out / "rekordbox.xml").getroot()
        tracks = {t.get("Name"): t for t in root.iter("TRACK") if t.get("Name")}
        assert tracks["two two"].get("Location").endswith("/out/House/140-144%20BPM/two%20two.mp3")
        assert tracks["one"].get("Tonality") == "Am" and tracks["one"].get("AverageBpm") == "140.00"
        tw = root.find("PLAYLISTS/NODE/NODE")
        assert tw.get("Name") == "Tagwerk"
        names = [n.get("Name") for n in tw.iter("NODE")]
        assert names == ["Tagwerk", "House", "140-144 BPM", "Techno", "140-144 BPM", "Your tags", "#warmup"]
        warmup = next(n for n in tw.iter("NODE") if n.get("Name") == "#warmup")
        from tagwerk.cli import write_rekordbox
        write_rekordbox([(paths[0], _results(db, paths[:1])[0][1], ["Bad\x0bName"])], tmp / "x.xml")
        assert ET.parse(tmp / "x.xml").find("PLAYLISTS/NODE/NODE/NODE").get("Name") == "BadName"  # still parses
        assert [t.get("Key") for t in warmup] == [tracks["two two"].get("TrackID")]


def test_location_and_key_name():
    assert _location(Path("/Users/me/My Music/a&b.mp3")) == "file://localhost/Users/me/My%20Music/a%26b.mp3"
    assert _location(Path("/mnt/c/Users/me/x.mp3")) == "file://localhost/C:/Users/me/x.mp3"  # WSL -> Windows
    assert key_name("A minor") == "Am" and key_name("F# major") == "F#" and key_name("8A") == "8A"


def test_dupes_copied_once():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        db, (mp3, flac, other) = _library(tmp, {
            "a/track.mp3": (b"x" * 10, [1, 0], "Techno"),
            "b/track.flac": (b"x" * 30, [1, 0.001], "Techno"),  # same track, better file
            "c/other.mp3": (b"y" * 10, [0, 1], "Techno"),
        })
        assert find_dupes(db, [mp3, flac, other]) == [[flac, mp3]]
        out = tmp / "out"
        organize(db, [mp3, flac, other], out, ["style"])
        assert sorted(f.name for f in (out / "Techno").iterdir()) == ["other.mp3", "track.flac"]


def test_main_defaults_to_copies_and_report():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
        db_path = str(tmp / "t.db")
        p = tmp / "lib" / "playlists" / "a" / "track.mp3"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 140.0, "duration": 300.0, "key": "A minor",
             "camelot": "8A", "arousal": 5.0, "valence": 5.0, "moods": dict.fromkeys(MOODS, 0.0)}
        db = open_db(db_path)
        db.execute("INSERT INTO tracks VALUES (?, ?, ?, NULL, NULL)", (str(p), p.stat().st_mtime, json.dumps(r)))
        db.commit()
        main([str(tmp / "lib" / "playlists"), "--db", db_path])  # no flags: copies + report next to the folder
        out = tmp / "lib" / "Tagwerk"
        assert (out / "Techno" / "Unknown" / "140-144 BPM" / "track.mp3").exists()  # no themes in this fake row
        assert "track.mp3" in (out / "tagwerk-report.csv").read_text(encoding="utf-8-sig")
        # scanning the parent now also finds our copies: they're skipped, not analyzed (no Essentia here)
        main([str(tmp / "lib"), "--db", db_path, "--analyze-only"])


def test_clean_title():
    cases = {
        "22. Artist - Track": "Artist - Track",
        "01 - Artist - Track": "Artist - Track",
        "1-02 Artist - Track": "Artist - Track",
        "07_Artist_-_Track": "Artist - Track",
        "01 Artist - Track": "Artist - Track",
        "Artist - Track (Free Download)": "Artist - Track",
        "Artist - Track [FREE DL] [320]": "Artist - Track",
        "PREMIERE: Artist - Track (Original Mix)": "Artist - Track (Original Mix)",
        "Premiere | Artist - Track [Mord]": "Artist - Track [Mord]",
        "Artist - Track | FREE DOWNLOAD": "Artist - Track",
        "Artist - Track - OUT NOW on Drumcode": "Artist - Track",
        "Artist - Track (Official Video) [HD]": "Artist - Track",
        "Artist - Track (Dax J Remix) [OUT NOW]": "Artist - Track (Dax J Remix)",
        "Artist - Track [dQw4w9WgXcQ]": "Artist - Track",
        "808 State - Pacific State": "808 State - Pacific State",  # a number that is the name stays
        "2 Unlimited - No Limit": "2 Unlimited - No Limit",
        "Freedom - Downloaded Dreams": "Freedom - Downloaded Dreams",  # words, not junk
        "(Free Download)": "(Free Download)",  # nothing left: keep it
        "5. 24-7": "24-7",  # the track number goes, the title's own numbers stay
        "1-800 Dinosaur - X": "1-800 Dinosaur - X",
        "Artist - Track (Dax J Remix | Free DL)": "Artist - Track (Dax J Remix)",  # the credit stays
        "Artist - Out Now (Original Mix)": "Artist - Out Now (Original Mix)",  # a title, not junk
        "Artist - Premiere Love": "Artist - Premiere Love",
        "Artist - Track [Acid-Techno]": "Artist - Track [Acid-Techno]",  # 11 characters, but no yt-dlp id
        "Artist - Track [a_b4c9WgXcQ]": "Artist - Track",  # a yt-dlp id with "_"
        "Mr. Oizo - Flat Beat.": "Mr. Oizo - Flat Beat.",
    }
    for dirty, clean in cases.items():
        assert clean_title(dirty) == clean, (dirty, clean_title(dirty))
    assert clean_filename(Path("03. Artist - A:B? (Free DL).mp3")) == "Artist - A-B-.mp3"


def test_search_scores():
    def track(bpm, camelot, dark):
        return {"bpm": bpm, "camelot": camelot, "themes": THEMES | {"dark": dark}, "arousal": 5.0,
                "styles": [("Electronic---Techno", 1)], "moods": dict.fromkeys(MOODS, 0.1),
                "manual": {"add": ["#peak"]} if dark > .5 else {}}
    results = [track(140, "8A", 0.9), track(141, "9A", 0.8), track(128, "8A", 0.9), track(140, "3B", 0.1)]
    names = ["Electronic---Acid", "Electronic---Techno", "Electronic---Drum n Bass"]
    styles = np.array([[0.1, 0.9, 0], [0.9, 0.8, 0], [0.9, 0.9, 0], [0.9, 0.9, 0]])
    score, understood, ignored = search_scores("dark acid techno with a rolling 140", results, styles, names)
    assert understood == ["dark", "acid", "techno", "137-143 BPM"] and ignored == ["rolling"]
    assert score.argmax() == 1 and score[2] == 0  # all three words fit best; 128 BPM is filtered out
    assert score[3] < score[1]  # not dark
    score, understood, _ = search_scores("dnb 8a #peak", results, styles, names)
    assert understood == ["dnb", "mixes with 8A", "#peak"]
    assert list(score > 0) == [True, True, True, False]  # 3B doesn't mix with 8A, and has no #peak
    score, understood, _ = search_scores("drum & bass", results, styles, names)
    assert understood == ["drum & bass"] and score.max() < 0.1  # nobody here is DnB: no full bar
    results[3]["manual"] = {"style": "Dub Techno", "add": ["#warm-up"]}  # hand-set style, a tag with a hyphen
    score, understood, _ = search_scores("dub techno 138-142 bpm #warm-up", results, styles, names)
    assert understood == ["dub techno", "138-142 BPM", "#warm-up"]
    assert list(score > 0) == [False, False, False, True]


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
        tmp = Path(tmp).resolve()  # macOS: /var is /private/var, and tagwerk stores resolved paths
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


# --- tags in real audio files (tagwerk's only writes into audio): an mp3 of silent MPEG frames, a stdlib wav
MP3 = (b"\xff\xfb\x90\x64" + b"\0" * 413) * 20


def _wav(path, seconds=1):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(b"\0\0" * 8000 * seconds)
    return path


def _id3(path, comments=(), version=3):
    from mutagen.id3 import COMM, ID3, TIT2
    t = ID3()
    t.add(TIT2(encoding=3, text=["07. Artist - Tune (Free Download)"]))
    for lang, text in comments:
        t.add(COMM(encoding=3, lang=lang, desc="", text=[text]))
    t.save(path, v2_version=version)


def test_tags_in_real_files():
    from mutagen.id3 import ID3
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        mp3, wav = tmp / "a.mp3", _wav(tmp / "b.wav")
        mp3.write_bytes(MP3)
        _id3(mp3, [("eng", "8A - Energy 7"), ("deu", "Bought at Beatport")])
        update_comment(mp3, ["#techno", "E6"])
        t = ID3(mp3)
        comments = [str(t[k]) for k in t.keys() if k.startswith("COMM")]
        assert comments == ["tw: #techno E6; 8A - Energy 7 Bought at Beatport"]  # no comment lost
        assert t.version == (2, 3, 0)  # the file's ID3 version stays
        for f in (mp3, wav):  # wav and aiff have a raw ID3, not an "easy" one: they used to get nothing
            write_fields(f, {"bpm": "140", "initialkey": "Am", "genre": "Techno", "title": "Artist - Tune"})
            t = ID3(f) if f.suffix == ".mp3" else __import__("mutagen").File(f).tags
            assert (str(t["TBPM"]), str(t["TKEY"]), str(t["TCON"])) == ("140", "Am", "Techno"), f
            assert read_fields(f) == ["", "Artist - Tune"]
        unfinished = _wav(tmp / "rec.wav")
        data = bytearray(unfinished.read_bytes())
        data[4:8] = b"\xff\xff\xff\xff"  # a recorder that never finished its header
        unfinished.write_bytes(bytes(data))
        try:
            update_comment(unfinished, ["#x"])
            raise AssertionError("should refuse")
        except ValueError:
            pass
        assert unfinished.read_bytes() == bytes(data)  # not touched


def test_copies_get_tags_originals_dont():
    from mutagen.id3 import ID3
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db, (p,) = _library(tmp, {"set/07. Artist - Tune (Free Download).mp3": (MP3, [1, 0], "Techno")})
        _id3(p, [("eng", "8A - Energy 7")])
        before = p.read_bytes()
        out = tmp / "out"
        organize(db, [p], out, ["style"])
        t = ID3(out / "Techno" / "Artist - Tune.mp3")
        assert (str(t["TBPM"]), str(t["TKEY"]), str(t["TCON"]), str(t["TIT2"])) == ("140", "Am", "Techno",
                                                                                      "Artist - Tune")
        assert [str(t[k]) for k in t.keys() if k.startswith("COMM")] == ["tw: #techno E6; 8A - Energy 7"]
        assert p.read_bytes() == before  # the original is untouched


def test_write_tags_on_originals():
    from mutagen.id3 import ID3
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db, (p,) = _library(tmp, {"set/a.mp3": (MP3, [1, 0], "Techno")})
        _id3(p, [("eng", "8A - Energy 7")])
        write_tags(db, [p])
        t = ID3(p)
        assert [str(t[k]) for k in t.keys() if k.startswith("COMM")] == ["tw: #techno E6; 8A - Energy 7"]
        assert t.version == (2, 3, 0)
        # the new mtime is stored (else the next run re-analyzes) and nothing is rewritten a second time
        assert db.execute("SELECT mtime FROM tracks").fetchone()[0] == p.stat().st_mtime
        mtime = p.stat().st_mtime_ns
        write_tags(db, [p])
        assert p.stat().st_mtime_ns == mtime


def test_copy_changed_by_a_dj_app_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db, (p,) = _library(tmp, {"set/a.mp3": (b"orig", [1, 0], "Techno")})
        out = tmp / "out"
        organize(db, [p], out, ["style"])
        copy = out / "Techno" / "a.mp3"
        copy.write_bytes(b"orig + serato cues")  # Serato/Traktor/Mixed In Key wrote into the copy: it's newer
        organize(db, [p], out, ["style"])
        assert copy.read_bytes() == b"orig + serato cues"
        p.write_bytes(b"new original")  # an original that is newer (--write-tags, a re-download) still wins
        os.utime(p, (p.stat().st_atime, copy.stat().st_mtime + 10))
        organize(db, [p], out, ["style"])
        assert copy.read_bytes() == b"new original"


def test_case_only_style_edit_keeps_the_copy():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db, (p,) = _library(tmp, {"set/a.mp3": (b"x", [1, 0], "Techno")})
        out = tmp / "out"
        organize(db, [p], out, ["style"])
        db.execute("INSERT INTO manual VALUES (?, ?)", (str(p), json.dumps({"style": "techno"})))
        for _ in range(3):  # used to delete the only copy, then crash every other run
            organize(db, [p], out, ["style"])
            assert len(list(out.rglob("*.mp3"))) == 1


def test_renamed_set_keeps_analysis_edits_and_copies():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db_path, src = str(tmp / "t.db"), tmp / "lib" / "Set A"
        p = src / "a.mp3"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 140.0, "duration": 300.0, "key": "A minor",
             "camelot": "8A", "arousal": 5.0, "valence": 5.0, "moods": dict.fromkeys(MOODS, 0.0)}
        db = open_db(db_path)
        db.execute("INSERT INTO tracks VALUES (?, ?, ?, NULL, NULL)", (str(p), p.stat().st_mtime, json.dumps(r)))
        db.execute("INSERT INTO manual VALUES (?, ?)", (str(p), json.dumps({"add": ["#peak"]})))
        db.commit()
        main([str(src), "--db", db_path])
        src.rename(src.with_name("Set A 2026"))
        main([str(src.with_name("Set A 2026")), "--db", db_path])  # no Essentia here: a re-analysis would fail
        copies = list((tmp / "lib" / "Tagwerk").rglob("*.mp3"))
        assert len(copies) == 1  # no "a (2).mp3"
        assert db.execute("SELECT path FROM manual").fetchone()[0] == str(src.with_name("Set A 2026") / "a.mp3")


def test_two_sets_into_one_output():
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db_path = str(tmp / "t.db")
        db = open_db(db_path)
        r = {"styles": [("Electronic---Techno", 0.9)], "bpm": 140.0, "duration": 300.0, "key": "A minor",
             "camelot": "8A", "arousal": 5.0, "valence": 5.0, "moods": dict.fromkeys(MOODS, 0.0)}
        for name in ("A/one.mp3", "B/two.mp3"):
            p = tmp / name
            p.parent.mkdir(parents=True)
            p.write_bytes(name.encode())
            db.execute("INSERT INTO tracks VALUES (?, ?, ?, NULL, NULL)", (str(p), p.stat().st_mtime, json.dumps(r)))
        db.commit()
        main([str(tmp / "A"), "--db", db_path])
        main([str(tmp / "B"), "--db", db_path])
        root = ET.parse(tmp / "Tagwerk" / "rekordbox.xml").getroot()
        assert sorted(t.get("Name") for t in root.find("COLLECTION")) == ["one", "two"]
        assert "one.mp3" in (tmp / "Tagwerk" / "tagwerk-report.csv").read_text(encoding="utf-8-sig")


def test_edit_a_copy_and_accented_names():
    import unicodedata
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        name = unicodedata.normalize("NFD", "Sûre.mp3")  # how macOS stores it
        db, (p,) = _library(tmp, {f"set/{name}": (b"x", [1, 0], "Techno")})
        db_path = str(tmp / "t.db")
        disk = open_db(db_path)
        db.commit()  # a backup waits for an open transaction
        db.backup(disk)
        out = tmp / "out"
        organize(disk, [p], out, ["style"])
        copy = next(out.rglob("*.mp3"))
        edit([str(copy), "--style", "Dub Techno", "--db", db_path])  # the copy stands for its original
        typed = str(p.parent / unicodedata.normalize("NFC", "Sûre.mp3"))  # how a typed or pasted path arrives
        edit([typed, "--add", "peak", "--db", db_path])
        m = json.loads(disk.execute("SELECT data FROM manual").fetchone()[0])
        assert m == {"style": "Dub Techno", "add": ["#peak"], "remove": []}
        assert disk_path(typed) == p


def test_better_file_replaces_the_old_copy():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        db, (mp3,) = _library(tmp, {"a/track.mp3": (b"x" * 10, [1, 0], "Techno")})
        out = tmp / "out"
        organize(db, [mp3], out, ["style"])
        flac = tmp / "in" / "b" / "track.flac"  # the FLAC of the same track arrives later
        flac.parent.mkdir()
        flac.write_bytes(b"x" * 30)
        r = json.loads(db.execute("SELECT data FROM tracks").fetchone()[0])
        db.execute("INSERT INTO tracks VALUES (?, 0, ?, ?, NULL)",
                   (str(flac), json.dumps(r), np.array([1, 0.001], np.float32).tobytes()))
        organize(db, [mp3, flac], out, ["style"])
        assert sorted(f.name for f in (out / "Techno").iterdir()) == ["track.flac"]
        assert mp3.exists()  # the original stays


def test_small_safety_rules():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp).resolve()
        # an interrupted copy leaves nothing under the real name
        import shutil
        real = shutil.copy2
        shutil.copy2 = lambda a, b: (Path(b).write_bytes(b"half"), (_ for _ in ()).throw(KeyboardInterrupt))
        try:
            _copy(_wav(tmp / "a.wav"), tmp / "dest.wav")
        except KeyboardInterrupt:
            pass
        finally:
            shutil.copy2 = real
        assert not (tmp / "dest.wav").exists() and not (tmp / "dest.wav.part").exists()
        # hidden twins, trash and dangling links aren't music
        (tmp / "m" / ".Trashes").mkdir(parents=True)
        for n in ("m/a.mp3", "m/._a.mp3", "m/.Trashes/old.mp3"):
            (tmp / n).write_bytes(b"x")
        (tmp / "m" / "gone.mp3").symlink_to(tmp / "nowhere.mp3")
        assert [f.name for f in audio_files(tmp / "m")] == ["a.mp3"]
        assert list(_mtimes([tmp / "m" / "a.mp3", tmp / "m" / "missing.mp3"])) == [tmp / "m" / "a.mp3"]
        # a folder holding only Finder junk is empty
        (tmp / "out" / "Old").mkdir(parents=True)
        (tmp / "out" / "Old" / ".DS_Store").write_bytes(b"")
        _prune(tmp / "out" / "Old", tmp / "out")
        assert not (tmp / "out" / "Old").exists()
        # the report's folder is created when nothing was copied
        db = open_db(":memory:")
        export_csv(db, [], tmp / "new" / "report.csv")
        assert (tmp / "new" / "report.csv").exists()
        # OUT inside the music folder is refused with a clear message
        try:
            main([str(tmp / "m"), "--organize", str(tmp), "--db", str(tmp / "t.db")])
            raise AssertionError("should refuse")
        except SystemExit as e:
            assert e.code == 2
    assert default_out(Path("/")) == Path("/Tagwerk")  # a drive's root: inside it
    assert win_safe("Aux") == "Aux_" and win_safe("Nul. Remix") == "Nul_. Remix" and win_safe("Techno.") == "Techno"


def test_learn_ignores_removed_auto_tags():
    rows = {p: ({"x": 1}, None) for p in "abc"}
    manual = {"a": {"add": ["#warmup"]}, "b": {"remove": ["#vocal"]}}
    db = open_db(":memory:")
    for p, v in zip("abc", ([1, 0], [1, 0.01], [1, 0.02])):
        db.execute("INSERT INTO tracks VALUES (?, 0, '{}', ?, NULL)", (p, np.array(v, np.float32).tobytes()))
    import tagwerk.cli as cli
    cli.LEARN_EXAMPLES, saved = 1, cli.LEARN_EXAMPLES
    try:
        _learn(db, rows, manual)
    finally:
        cli.LEARN_EXAMPLES = saved
    assert rows["b"][0]["learned"] == ["#warmup"]  # fixing a false #vocal doesn't make it a "none of these"
    assert rows["c"][0]["learned"] == ["#warmup"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
