"""Analisi audio con Essentia: BPM, key, stile Discogs, mood, voce, arousal/valence."""
import json
import os
import urllib.request
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")  # zittisce TensorFlow, va settato prima dell'import

import essentia  # noqa: E402

essentia.log.infoActive = False  # prima di essentia.standard, che già all'import stampa log
essentia.log.warningActive = False

import essentia.standard as es  # noqa: E402

from .tags import camelot, fold_bpm  # noqa: E402

MODELS_URL = "https://essentia.upf.edu/models"
MODELS_DIR = Path(os.environ.get("TAGWERK_MODELS", Path.home() / ".cache" / "tagwerk" / "models"))

EFFNET = "feature-extractors/discogs-effnet/discogs-effnet-bs64-1"
MUSICNN = "feature-extractors/musicnn/msd-musicnn-1"
STYLES = "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1"
DEAM = "classification-heads/deam/deam-msd-musicnn-2"
# nome -> (modello, classe "positiva"); tutti girano sugli embedding Discogs-EffNet
HEADS = {
    "aggressive": ("classification-heads/mood_aggressive/mood_aggressive-discogs-effnet-1", "aggressive"),
    "relaxed": ("classification-heads/mood_relaxed/mood_relaxed-discogs-effnet-1", "relaxed"),
    "happy": ("classification-heads/mood_happy/mood_happy-discogs-effnet-1", "happy"),
    "sad": ("classification-heads/mood_sad/mood_sad-discogs-effnet-1", "sad"),
    "party": ("classification-heads/mood_party/mood_party-discogs-effnet-1", "party"),
    "danceable": ("classification-heads/danceability/danceability-discogs-effnet-1", "danceable"),
    "voice": ("classification-heads/voice_instrumental/voice_instrumental-discogs-effnet-1", "voice"),
}


def _fetch(name):
    """Scarica .pb e .json del modello in cache al primo uso. Ritorna (path .pb, metadati)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("json", "pb"):
        dest = MODELS_DIR / f"{Path(name).name}.{ext}"
        if not dest.exists():
            tmp = dest.with_name(dest.name + ".part")  # niente modelli mezzi scaricati se si interrompe
            urllib.request.urlretrieve(f"{MODELS_URL}/{name}.{ext}", tmp)
            tmp.rename(dest)
    meta = json.loads((MODELS_DIR / f"{Path(name).name}.json").read_text())
    return str(MODELS_DIR / f"{Path(name).name}.pb"), meta


def _output(meta, purpose):
    return next(o["name"] for o in meta["schema"]["outputs"] if o["output_purpose"] == purpose)


def _head(name):
    pb, meta = _fetch(name)
    model = es.TensorflowPredict2D(graphFilename=pb, input=meta["schema"]["inputs"][0]["name"],
                                   output=_output(meta, "predictions"))
    return model, meta["classes"]


class Analyzer:
    """Carica i modelli una volta sola, poi analizza un pezzo alla volta."""

    def __init__(self):
        pb, meta = _fetch(EFFNET)
        self.effnet = es.TensorflowPredictEffnetDiscogs(graphFilename=pb, output=_output(meta, "embeddings"))
        pb, meta = _fetch(MUSICNN)
        self.musicnn = es.TensorflowPredictMusiCNN(graphFilename=pb, output=_output(meta, "embeddings"))
        self.styles, self.style_names = _head(STYLES)
        self.deam, _ = _head(DEAM)
        self.heads = {}
        for key, (name, positive) in HEADS.items():
            model, classes = _head(name)
            self.heads[key] = (model, classes.index(positive))
        self.rhythm = es.RhythmExtractor2013(method="multifeature")
        self.key = es.KeyExtractor(profileType="edma")  # profilo pensato per la musica elettronica
        self.resample = es.Resample(inputSampleRate=44100, outputSampleRate=16000, quality=4)  # come in training

    def analyze(self, path):
        audio = es.MonoLoader(filename=str(path), sampleRate=44100)()
        bpm = self.rhythm(audio)[0]
        key, scale, strength = self.key(audio)
        audio16 = self.resample(audio)
        emb = self.effnet(audio16)  # (frame, 1280)
        styles = self.styles(emb).mean(axis=0)
        top = styles.argsort()[::-1][:3]
        valence, arousal = self.deam(self.musicnn(audio16)).mean(axis=0)
        top_styles = [(self.style_names[i], round(float(styles[i]), 3)) for i in top]
        return {
            "bpm": fold_bpm(round(float(bpm), 1), top_styles),
            "key": f"{key} {scale}",
            "camelot": camelot(key, scale),
            "key_strength": round(float(strength), 2),
            "styles": top_styles,
            "moods": {k: round(float(m(emb).mean(axis=0)[i]), 3) for k, (m, i) in self.heads.items()},
            "valence": round(float(valence), 2),
            "arousal": round(float(arousal), 2),
            "duration": round(len(audio) / 44100, 1),
            "embedding": emb.mean(axis=0),  # per categorie personali e pezzi simili, senza rianalizzare
        }
