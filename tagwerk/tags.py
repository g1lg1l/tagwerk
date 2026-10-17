"""Da risultati di analisi a tag leggibili, e scrittura nel campo Comment dei file."""
import re

import mutagen
from mutagen.id3 import COMM, ID3

STYLE_MIN = 0.25  # 2° e 3° stile solo sopra questa soglia; il 1° sempre
MOOD_MIN = 0.5
# nome del modello -> tag; modifica liberamente il vocabolario
MOOD_TAGS = {"aggressive": "aggressive", "relaxed": "chill", "happy": "happy", "sad": "dark", "voice": "vocal"}

_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
       "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def camelot(key, scale):
    """('A', 'minor') -> '8A'. Ruota sul circolo delle quinte: C major = 8B, A minor = 8A."""
    pc = _PC[key]
    if scale == "major":
        return f"{(7 * pc + 7) % 12 + 1}B"
    return f"{(7 * pc + 4) % 12 + 1}A"


HALFTIME_STYLES = {"Drum n Bass", "Jungle", "Halftime", "Darkstep", "Neurofunk", "Breakcore"}


def fold_bpm(bpm, styles):
    """Il beat tracker sulla DnB spesso aggancia metà tempo (86 invece di 172): raddoppia se lo stile è DnB."""
    # ponytail: regola per stile; se compaiono altri generi dimezzati (es. schranz) aggiungerli qui
    if bpm < 110 and any(s.split("---")[-1] in HALFTIME_STYLES for s, _ in styles):
        return round(bpm * 2, 1)
    return bpm


def energy(arousal):
    """Arousal DEAM (scala 1–9) -> energia 1–10."""
    # ponytail: mappatura lineare, ricalibrare sui percentili della libreria se la techno si schiaccia in alto (#5)
    return max(1, min(10, round(1 + (arousal - 1) / 8 * 9)))


def _slug(style):
    return "#" + style.split("---")[-1].lower().replace(" ", "-")


def build_tags(result):
    styles = result["styles"]
    tags = [_slug(styles[0][0])] + [_slug(s) for s, p in styles[1:] if p >= STYLE_MIN]
    tags += ["#" + tag for name, tag in MOOD_TAGS.items() if result["moods"][name] >= MOOD_MIN]
    tags.append(f"E{energy(result['arousal'])}")
    return list(dict.fromkeys(tags))  # dedup mantenendo l'ordine (es. Reggae---Dub e Electronic---Dub)


_BLOCK = re.compile(r"(?:^|\s)tw:[^;]*;?")


def merge_comment(existing, tags):
    """Sostituisce solo il blocco 'tw: ...;' e lascia il resto del commento (es. Mixed In Key) com'è."""
    rest = _BLOCK.sub(" ", existing or "", count=1).strip()
    return f"tw: {' '.join(tags)}; {rest}".strip()


def update_comment(path, tags):
    """Scrive i tag nel campo Comment del file preservando il testo esistente. Ritorna il nuovo commento."""
    f = mutagen.File(path)
    if f is None:
        raise ValueError("formato non supportato")
    if f.tags is None:
        f.add_tags()
    t = f.tags
    if isinstance(t, ID3):  # mp3, aiff, wav
        main = [k for k in t.keys() if k.startswith("COMM::")]  # solo il commento principale, non iTunNORM & co.
        new = merge_comment(str(t[main[0]]) if main else "", tags)
        for k in main:
            del t[k]
        t.add(COMM(encoding=3, lang="eng", desc="", text=[new]))
        f.save(v2_version=3 if t.version[1] == 3 else 4)  # non cambiare versione ID3 a chi ce l'ha già
        return new
    key = "\xa9cmt" if "mp4" in type(f).__name__.lower() else "comment"  # m4a vs flac/ogg
    new = merge_comment((t.get(key) or [""])[0], tags)
    t[key] = [new]
    f.save()
    return new
