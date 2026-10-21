"""From analysis results to readable tags, folder names, and the Comment field of audio files."""
import re

import mutagen
from mutagen.id3 import COMM, ID3

STYLE_MIN = 0.25  # 2nd and 3rd style only above this; the 1st always
MOOD_MIN = 0.5
# ponytail: absolute cosine floor for learned tags, calibrate on a real library (#9)
LEARN_MIN = 0.9  # a track inherits hand tags only from a hand-tagged track at least this similar
LEARN_EXAMPLES = 5  # a hand tag spreads to other tracks only once you've put it on this many
# ponytail: guessed, calibrate on real duplicates (#9)
DUPE_MIN = 0.995  # about the same length and at least this similar = the same track saved twice
# model name -> tag; change the vocabulary freely
MOOD_TAGS = {"aggressive": "aggressive", "relaxed": "chill", "happy": "happy", "sad": "dark", "voice": "vocal"}

_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
       "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def camelot(key, scale):
    """('A', 'minor') -> '8A'. Walks the circle of fifths: C major = 8B, A minor = 8A."""
    pc = _PC[key]
    if scale == "major":
        return f"{(7 * pc + 7) % 12 + 1}B"
    return f"{(7 * pc + 4) % 12 + 1}A"


HALFTIME_STYLES = {"Drum n Bass", "Jungle", "Halftime", "Darkstep", "Neurofunk", "Breakcore"}
SLOW_STYLES = {"Ambient", "Downtempo", "Trip Hop", "Chillout", "Dub", "Abstract", "Experimental"}


def fold_bpm(bpm, styles):
    """The beat tracker often locks onto half time on fast tracks (72 for 144 techno, 86 for 172 DnB): double it.

    Idempotent, so it is safe to apply to cached values."""
    # ponytail: style-based rule; tune the two sets if a genre keeps coming out halved or doubled
    genre, _, style = styles[0][0].partition("---")
    dnb = any(s.split("---")[-1] in HALFTIME_STYLES for s, _ in styles)
    if (dnb and bpm < 110) or (genre == "Electronic" and style not in SLOW_STYLES and bpm < 90):
        return round(bpm * 2, 1)
    return bpm


def compatible(a, b):
    """Harmonic mixing on the Camelot wheel: same number, or same letter one step away (8A ~ 8B, 7A, 9A)."""
    (na, la), (nb, lb) = (int(a[:-1]), a[-1]), (int(b[:-1]), b[-1])
    return na == nb or (la == lb and (na - nb) % 12 in (1, 11))


def energy(arousal):
    """DEAM arousal (1–9 scale) -> energy 1–10."""
    # ponytail: linear mapping, recalibrate on library percentiles if techno bunches up at the top (#5)
    return max(1, min(10, round(1 + (arousal - 1) / 8 * 9)))


def track_energy(r):
    return r.get("manual", {}).get("energy") or energy(r["arousal"])


def tag(text):
    """'Warm Up' / '#warm-up' -> '#warm-up'. No ';', it ends our block in the Comment field."""
    return "#" + text.strip().lstrip("#").lower().replace(" ", "-").replace(";", "")


def _slug(style):
    return tag(style.split("---")[-1])


KEY_RE = re.compile(r"^(1[0-2]|[1-9])[AB]$")


def apply_manual(r, manual):
    """Hand corrections win over the analysis. Applied on read: they survive re-analysis, and --reset undoes them."""
    r["manual"] = manual
    if "style" in manual:
        rest = [s for s in r["styles"] if s[0].split("---")[-1] != manual["style"]]
        r["styles"] = [(manual["style"], 1.0)] + rest[:2]
    if "bpm" in manual:
        r["bpm"] = manual["bpm"]
    if "key" in manual:
        r["key"] = r["camelot"] = manual["key"]
    return r


def build_tags(result):
    styles, manual = result["styles"], result.get("manual", {})
    tags = [_slug(styles[0][0])] + [_slug(s) for s, p in styles[1:] if p >= STYLE_MIN]
    tags += ["#" + t for name, t in MOOD_TAGS.items() if result["moods"][name] >= MOOD_MIN]
    tags += manual.get("add", []) + result.get("learned", [])
    tags = [t for t in tags if t not in manual.get("remove", [])]
    tags.append(f"E{track_energy(result)}")
    return list(dict.fromkeys(tags))  # dedupe keeping order (e.g. Reggae---Dub and Electronic---Dub)


BPM_STEP = 5  # BPM folders 5 wide: 140-144, 145-149…


def _bpm_range(bpm):
    lo = round(bpm) // BPM_STEP * BPM_STEP  # 144.7 is almost always a wobbling 145
    return f"{lo}-{lo + BPM_STEP - 1} BPM"


def _mood(r):
    best = max(("aggressive", "relaxed", "happy", "sad"), key=lambda m: r["moods"][m])
    return MOOD_TAGS[best].capitalize() if r["moods"][best] >= MOOD_MIN else "Neutral"


# folder level -> subfolder name
LAYOUT = {
    "style": lambda r: r["styles"][0][0].split("---")[-1],
    "bpm": lambda r: _bpm_range(r["bpm"]),
    "energy": lambda r: f"Energy {track_energy(r)}",
    "key": lambda r: r["camelot"],
    "mood": _mood,
    "vocals": lambda r: "Vocal" if r["moods"]["voice"] >= MOOD_MIN else "Instrumental",
}
_UNSAFE = re.compile(r'[<>:"/\\|?*]')  # characters not allowed in Windows folder names


# ponytail: pattern list, add to it when new dirt shows up in your library
_JUNK = (r"free\s*(?:download|dl)|free|download|premiere|out\s*now|official(?:\s*(?:music\s*)?(?:video|audio))?"
         r"|music\s*video|lyrics?|visuali[sz]er|hq|hd|4k|\d{3}\s*kbps|320|exclusive|preview|buy\s*=?\s*free")
_STRONG = r"free\s*(?:download|dl)|premiere|out\s*now|official\s*(?:video|audio)"  # junk even outside brackets
_DIRT = [
    # track numbers: "22. ", "01 - ", "1-02 ", "01 "; a number that's part of the name ("808 State") stays
    (re.compile(r"^\s*(?:\d{1,3}\s*[.)_\-–]+\s*)*(?:0\d\s+)?"), ""),
    (re.compile(rf"[\[(][^\])]*?\b(?:{_JUNK})\b[^\])]*[\])]", re.I), ""),  # "(Free Download)", "[OUT NOW on X]", "[HQ]"
    (re.compile(rf"^\s*(?:{_STRONG})\s*[:|\-–]\s*", re.I), ""),  # "PREMIERE: ", "FREE DL | "
    (re.compile(rf"\s*[|\-–]\s*(?:{_STRONG})\b.*$", re.I), ""),  # " | Free Download", " - OUT NOW on X"
    (re.compile(r"\s*\[[A-Za-z0-9_-]{11}\]$"), ""),  # yt-dlp's "[video id]"
    (re.compile(r"[\[(]\s*[\])]"), ""),  # brackets left empty
    (re.compile(r"\s{2,}"), " "),
]


def clean_title(s):
    """'22. Artist - Track (Free Download) [HQ]' -> 'Artist - Track'. Mix names, remixes and labels stay."""
    out = s.replace("_", " ")
    for pattern, repl in _DIRT:
        out = pattern.sub(repl, out)
    return out.strip(" -–|.") or s  # nothing left: better dirty than empty


def clean_filename(p):
    """Clean title, safe on Windows, same extension."""
    return _UNSAFE.sub("-", clean_title(p.stem)).strip(" .") + p.suffix


def folder_for(r, by):
    """E.g. by=('style', 'bpm') -> ['Hard Techno', '145-149 BPM']."""
    return [_UNSAFE.sub("-", LAYOUT[k](r)).strip(" .") for k in by]


_BLOCK = re.compile(r"(?:^|\s)tw:[^;]*;?")


def merge_comment(existing, tags):
    """Replaces only the 'tw: ...;' block and leaves the rest of the comment (e.g. Mixed In Key) as is."""
    rest = _BLOCK.sub(" ", existing or "", count=1).strip()
    return f"tw: {' '.join(tags)}; {rest}".strip()


def update_comment(path, tags):
    """Writes the tags into the file's Comment field, keeping existing text. Returns the new comment."""
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported format")
    if f.tags is None:
        f.add_tags()
    t = f.tags
    if isinstance(t, ID3):  # mp3, aiff, wav
        main = [k for k in t.keys() if k.startswith("COMM::")]  # only the main comment, not iTunNORM & co.
        new = merge_comment(str(t[main[0]]) if main else "", tags)
        for k in main:
            del t[k]
        t.add(COMM(encoding=3, lang="eng", desc="", text=[new]))
        f.save(v2_version=3 if t.version[1] == 3 else 4)  # keep the ID3 version a file already has
        return new
    key = "\xa9cmt" if "mp4" in type(f).__name__.lower() else "comment"  # m4a vs flac/ogg
    new = merge_comment((t.get(key) or [""])[0], tags)
    t[key] = [new]
    f.save()
    return new
