"""Test della logica pura (niente Essentia). Lancia con: python tests/test_tags.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tagwerk.tags import build_tags, camelot, energy, fold_bpm, merge_comment  # noqa: E402


def test_camelot():
    assert camelot("A", "minor") == "8A"
    assert camelot("C", "major") == "8B"
    assert camelot("Ab", "minor") == camelot("G#", "minor") == "1A"
    assert camelot("B", "major") == "1B"
    assert camelot("E", "major") == "12B"
    assert camelot("F#", "minor") == "11A"
    assert camelot("Bb", "minor") == "3A"
    # 24 tonalità -> 24 codici diversi
    keys = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
    assert len({camelot(k, s) for k in keys for s in ("major", "minor")}) == 24


def test_fold_bpm():
    dnb = [("Electronic---Halftime", 0.4), ("Electronic---Drum n Bass", 0.3), ("Electronic---Dubstep", 0.1)]
    techno = [("Electronic---Techno", 0.6), ("Electronic---Hard Techno", 0.3), ("Electronic---Acid", 0.1)]
    assert fold_bpm(86.8, dnb) == 173.6
    assert fold_bpm(172.3, dnb) == 172.3  # già giusto
    assert fold_bpm(95.0, techno) == 95.0  # niente raddoppi fuori dalla DnB


def test_energy():
    assert energy(1) == 1 and energy(9) == 10 and energy(5) == 6
    assert energy(-3) == 1 and energy(12) == 10


def test_merge_comment_preserves_existing():
    assert merge_comment("", ["#techno"]) == "tw: #techno;"
    assert merge_comment(None, ["#techno"]) == "tw: #techno;"
    # il testo di Mixed In Key resta
    assert merge_comment("8A - Energy 7", ["#techno", "E7"]) == "tw: #techno E7; 8A - Energy 7"
    # rilanciare sostituisce il blocco, non lo duplica
    once = merge_comment("8A - Energy 7", ["#techno"])
    assert merge_comment(once, ["#minimal"]) == "tw: #minimal; 8A - Energy 7"
    # 'tw:' dentro una parola non è il nostro blocco
    assert merge_comment("network:x", ["#acid"]) == "tw: #acid; network:x"


def test_build_tags():
    r = {
        "styles": [("Electronic---Dub Techno", 0.6), ("Reggae---Dub", 0.3), ("Electronic---Dub", 0.28)],
        "moods": {"aggressive": 0.1, "relaxed": 0.7, "happy": 0.2, "sad": 0.55, "voice": 0.1},
        "arousal": 4.0,
    }
    assert build_tags(r) == ["#dub-techno", "#dub", "#chill", "#dark", "E4"]
    # il primo stile c'è sempre, gli altri solo sopra soglia
    r["styles"] = [("Electronic---Techno", 0.1), ("Electronic---Minimal", 0.05), ("Electronic---Acid", 0.01)]
    assert build_tags(r)[0] == "#techno" and "#minimal" not in build_tags(r)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
