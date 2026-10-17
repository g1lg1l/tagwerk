"""From analysis results to readable tags, folder names, and the Comment field of audio files."""
import re

import mutagen
from mutagen.id3 import COMM, ID3

STYLE_MIN = 0.25  # 2nd and 3rd style only above this; the 1st always
MOOD_MIN = 0.5
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


def energy(arousal):
    """DEAM arousal (1–9 scale) -> energy 1–10."""
    # ponytail: linear mapping, recalibrate on library percentiles if techno bunches up at the top (#5)
    return max(1, min(10, round(1 + (arousal - 1) / 8 * 9)))


def _slug(style):
    return "#" + style.split("---")[-1].lower().replace(" ", "-")


def build_tags(result):
    styles = result["styles"]
    tags = [_slug(styles[0][0])] + [_slug(s) for s, p in styles[1:] if p >= STYLE_MIN]
    tags += ["#" + tag for name, tag in MOOD_TAGS.items() if result["moods"][name] >= MOOD_MIN]
    tags.append(f"E{energy(result['arousal'])}")
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
    "energy": lambda r: f"Energy {energy(r['arousal'])}",
    "key": lambda r: r["camelot"],
    "mood": _mood,
    "vocals": lambda r: "Vocal" if r["moods"]["voice"] >= MOOD_MIN else "Instrumental",
}
_UNSAFE = re.compile(r'[<>:"/\\|?*]')  # characters not allowed in Windows folder names


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
