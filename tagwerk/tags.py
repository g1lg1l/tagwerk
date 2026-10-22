"""From analysis results to readable tags, folder names, and the Comment field of audio files."""
import re

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from mutagen.id3 import COMM, ID3

EasyID3.RegisterTextKey("initialkey", "TKEY")  # the key field DJ apps read; mutagen's easy layer lacks it
EasyMP4Tags.RegisterFreeformKey("initialkey", "initialkey")

# Calibrated on a 96-track hardgroove/techno set (#9); numbers in the comments are from that set.
# 2nd and 3rd style tags only above this (the 1st always). 0.25 put #tech-trance on 28 tracks, 7 really were
STYLE_MIN = 0.4
# extra mood flags (#aggressive #chill #happy). At 0.5, 40% of tracks sat in the 0.4-0.6 coin-flip zone
MOOD_MIN = 0.65
VOCAL_MIN = 0.5  # the voice model's own decision boundary: vocal vs instrumental
# a track inherits hand tags only from a hand-tagged track at least this similar: only 5% of random pairs reach
# 0.9, while most tracks have a neighbour above it
LEARN_MIN = 0.9
LEARN_EXAMPLES = 5  # a hand tag spreads to other tracks only once you've put it on this many
# about the same length and at least this similar = the same track saved twice. A wav and its mp3 scored 0.9996,
# the closest two different tracks 0.968
DUPE_MIN = 0.995
# model name -> tag; change the vocabulary freely
MOOD_TAGS = {"aggressive": "aggressive", "relaxed": "chill", "happy": "happy"}
# mood folder/tag -> MTG-Jamendo mood/theme labels it covers; a track gets the strongest group. Change freely:
# all 56 label scores are stored, so a new vocabulary needs no re-analysis
MOOD_GROUPS = {"Energetic": ["energetic"], "Melodic": ["melodic"], "Deep": ["deep"], "Dark": ["dark"],
               "Emotional": ["emotional", "sad", "melancholic"], "Groovy": ["groovy"], "Uplifting": ["uplifting"]}

_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
       "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def relabel_styles(styles, bpm):
    """Discogs files a lot of hardgroove under Schranz; real schranz runs 150+ BPM, so below that it's Hardgroove."""
    # ponytail: one BPM rule, add more renames here if another style keeps landing on the wrong neighbour
    return [("Electronic---Hardgroove" if s.endswith("---Schranz") and bpm < 150 else s, p) for s, p in styles]


def camelot(key, scale):
    """('A', 'minor') -> '8A'. Walks the circle of fifths: C major = 8B, A minor = 8A."""
    pc = _PC[key]
    if scale == "major":
        return f"{(7 * pc + 7) % 12 + 1}B"
    return f"{(7 * pc + 4) % 12 + 1}A"


HALFTIME_STYLES = {"Drum n Bass", "Jungle", "Halftime", "Breakcore"}
SLOW_STYLES = {"Ambient", "Downtempo", "Trip Hop", "Dub", "Abstract", "Experimental"}


def fold_bpm(bpm, styles):
    """The beat tracker often locks onto half time on fast tracks (72 for 144 techno, 86 for 172 DnB): double it.

    Idempotent, so it is safe to apply to cached values."""
    # ponytail: style-based rule; tune the two sets if a genre keeps coming out halved or doubled
    genre, _, style = styles[0][0].partition("---")
    dnb = any(s.split("---")[-1] in HALFTIME_STYLES and p >= 0.1 for s, p in styles)  # a 2% third guess isn't DnB
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
        rest = [s for s in r["styles"] if s[0].split("---")[-1].lower() != manual["style"].lower()]
        r["styles"] = [(manual["style"], 1.0)] + rest[:2]
    if "bpm" in manual:
        r["bpm"] = manual["bpm"]
    if "key" in manual:
        r["key"] = r["camelot"] = manual["key"]
    return r


def mood_of(r):
    """The strongest mood group. No cutoff, so near-identical tracks don't flip between a mood and 'Neutral'."""
    if "mood" in r.get("manual", {}):
        return r["manual"]["mood"]
    t = r.get("themes")
    if not t:
        return "Unknown"  # analyzed by tagwerk < 0.4 and not upgraded yet (any normal run does that)
    return max(MOOD_GROUPS, key=lambda g: max(t[c] for c in MOOD_GROUPS[g]))


def build_tags(result):
    styles, manual = result["styles"], result.get("manual", {})
    tags = [_slug(styles[0][0])] + [_slug(s) for s, p in styles[1:] if p >= STYLE_MIN]
    if (mood := mood_of(result)) != "Unknown":
        tags.append(tag(mood))
    tags += ["#" + t for name, t in MOOD_TAGS.items() if result["moods"][name] >= MOOD_MIN]
    if result["moods"]["voice"] >= VOCAL_MIN:
        tags.append("#vocal")
    tags += manual.get("add", []) + result.get("learned", [])
    tags = [t for t in tags if t not in manual.get("remove", [])]
    tags.append(f"E{track_energy(result)}")
    return list(dict.fromkeys(tags))  # dedupe keeping order (e.g. Reggae---Dub and Electronic---Dub)


BPM_STEP = 5  # BPM folders 5 wide: 140-144, 145-149…


def _bpm_range(bpm):
    lo = round(bpm) // BPM_STEP * BPM_STEP  # 144.7 is almost always a wobbling 145
    return f"{lo}-{lo + BPM_STEP - 1} BPM"


# folder level -> subfolder name
LAYOUT = {
    "style": lambda r: r["styles"][0][0].split("---")[-1],
    "bpm": lambda r: _bpm_range(r["bpm"]),
    "energy": lambda r: f"Energy {track_energy(r)}",
    "key": lambda r: r["camelot"],
    "mood": mood_of,
    "vocals": lambda r: "Vocal" if r["moods"]["voice"] >= VOCAL_MIN else "Instrumental",
}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # characters not allowed in Windows file and folder names
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com\d|lpt\d)")  # device names: Windows can't open a file called that


def win_safe(name):
    """A file or folder name Windows accepts: no illegal characters, no trailing dot or space, no device name."""
    name = _UNSAFE.sub("-", name).strip(" .")
    stem, dot, ext = name.partition(".")
    return f"{stem}_{dot}{ext}" if _RESERVED.fullmatch(stem) else name


# ponytail: pattern list, add to it when new dirt shows up in your library
_JUNK = (r"free\s*(?:download|dl)|free|download|premiere|out\s*now|official(?:\s*(?:music\s*)?(?:video|audio))?"
         r"|music\s*video|lyrics?|visuali[sz]er|hq|hd|4k|\d{3}\s*kbps|320|exclusive|preview|buy\s*=?\s*free")
_STRONG = r"free\s*(?:download|dl)|premiere|out\s*now|official\s*(?:video|audio)"  # junk even outside brackets
_MIX = r"(?:re)?mix|edit|dub|rework|version|bootleg|feat|ft"  # a bracket with one of these is a credit: it stays
_DIRT = [
    # one track number, followed by a space: "22. ", "01 - ", "1-02 ", "01 ". "808 State", "1-800 Dinosaur" and
    # "5. 24-7" keep their own numbers
    (re.compile(r"^\s*(?:\d{1,3}(?:[.)]\s+|\s*[-–]\s+)|\d-\d{2}\s+|0\d\s+)"), ""),
    # junk tacked onto a credit, inside its bracket: "(Dax J Remix | Free DL)" -> "(Dax J Remix)"
    (re.compile(rf"\s*[|\-–]\s*(?:{_STRONG})[^\[\]()]*(?=[\])])", re.I), ""),
    # a bracket of junk: "(Free Download)", "[OUT NOW on X]", "[HQ]"; not a credit, not nested
    (re.compile(rf"\s*[\[(](?![^\[\]()]*\b(?:{_MIX})\b)[^\[\]()]*?\b(?:{_JUNK})\b[^\[\]()]*[\])]", re.I), ""),
    (re.compile(rf"^\s*(?:{_STRONG})\s*[:|\-–]\s*", re.I), ""),  # "PREMIERE: ", "FREE DL | "
    # at the end only: " | Free Download", " - OUT NOW on Drumcode"; "Artist - Out Now (Original Mix)" stays
    (re.compile(rf"\s*[|\-–]\s*(?:{_STRONG})(?:\s+on\b.*)?[\s!|]*$", re.I), ""),
    (re.compile(r"[\[(]\s*[\])]"), ""),  # brackets left empty
    (re.compile(r"\s{2,}"), " "),
]
# yt-dlp's "[video id]": 11 characters with a digit or "_" in them, so "[Acid-Techno]" stays
_YT_ID = re.compile(r"\s*\[(?=[^\]]*[\d_])[A-Za-z0-9_-]{11}\]$")


def clean_title(s):
    """'22. Artist - Track (Free Download) [HQ]' -> 'Artist - Track'. Mix names, remixes and labels stay."""
    out = _YT_ID.sub("", s).replace("_", " ")  # the id first: it may contain "_"
    for pattern, repl in _DIRT:
        out = pattern.sub(repl, out)
    return out.strip(" -–|") or s  # nothing left: better dirty than empty


def clean_filename(p):
    """Clean title, safe on Windows, same extension."""
    return win_safe(clean_title(p.stem)) + p.suffix


def folder_for(r, by):
    """E.g. by=('style', 'bpm') -> ['Hard Techno', '145-149 BPM']."""
    return [win_safe(LAYOUT[k](r)) for k in by]


_BLOCK = re.compile(r"(?:^|\s)tw:[^;]*;?")


def merge_comment(existing, tags):
    """Replaces only the 'tw: ...;' block and leaves the rest of the comment (e.g. Mixed In Key) as is."""
    rest = _BLOCK.sub(" ", existing or "", count=1).strip()
    return f"tw: {' '.join(tags)}; {rest}".strip()


def key_name(key):
    """'A minor' -> 'Am', 'F# major' -> 'F#': how ID3's key field and rekordbox write keys. A hand-set Camelot key
    ('8A') stays as it is."""
    note, _, scale = key.partition(" ")
    return note + ("m" if scale == "minor" else "")


def _open(path):
    """The file with its tags, ready to write; refuses what mutagen would damage."""
    f = mutagen.File(path)
    if f is None:
        raise ValueError("unsupported format")
    if type(f).__name__ in ("WAVE", "AIFF"):
        with open(path, "rb") as fh:  # a recorder's unfinished header (size 0xFFFFFFFF): mutagen would grow the file
            fh.seek(4)
            if fh.read(4) == b"\xff\xff\xff\xff":
                raise ValueError("unfinished WAV/AIFF header, not touched")
    if f.tags is None:
        f.add_tags()
    return f


def _save(f):
    if isinstance(f.tags, ID3):
        f.save(v2_version=3 if f.tags.version[1] == 3 else 4)  # keep the ID3 version a file already has
    else:
        f.save()


def read_fields(path, keys=("artist", "title")):
    """Easy-tag values ('' when missing) for any format, WAV and AIFF included (their ID3 isn't 'easy')."""
    try:
        f = mutagen.File(path)
        t = f.tags if f is not None else None
        if isinstance(t, ID3):
            def get(k):
                try:
                    return EasyID3.Get[k](t, k)[0]
                except (KeyError, IndexError):
                    return ""
            return [get(k) for k in keys]
        t = mutagen.File(path, easy=True)
        return [(t.get(k) or [""])[0] for k in keys]
    except Exception:
        return [""] * len(keys)


def write_fields(path, fields):
    """Sets the easy-tag fields (title, bpm, genre, initialkey…) the file's format supports, skips the others."""
    f = _open(path)
    if isinstance(f.tags, ID3):  # mp3, wav, aiff: through EasyID3's field table
        for k, v in fields.items():
            EasyID3.Set[k](f.tags, k, [v])
        _save(f)
        return
    f = mutagen.File(path, easy=True)
    if f.tags is None:
        f.add_tags()
    for k, v in fields.items():
        try:
            f[k] = v
        except (KeyError, ValueError, TypeError):  # this format has no such field
            pass
    f.save()


def update_comment(path, tags):
    """Writes the tags into the file's Comment field, keeping every existing text. Returns the new comment."""
    f = _open(path)
    t = f.tags
    if isinstance(t, ID3):  # mp3, aiff, wav
        main = [k for k in t.keys() if k.startswith("COMM::")]  # only the main comments, not iTunNORM & co.
        new = merge_comment(" ".join(dict.fromkeys(str(t[k]) for k in main)), tags)  # all of them: none is lost
        for k in main:
            del t[k]
        t.add(COMM(encoding=3, lang="eng", desc="", text=[new]))
        _save(f)
        return new
    key = "\xa9cmt" if "mp4" in type(f).__name__.lower() else "comment"  # m4a vs flac/ogg
    new = merge_comment(" ".join(dict.fromkeys(t.get(key) or [])), tags)
    t[key] = [new]
    f.save()
    return new
