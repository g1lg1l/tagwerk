"""Audio analysis with Essentia: BPM, key, Discogs style, mood, vocals, arousal/valence."""
import json
import os
import urllib.request
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")  # silences TensorFlow, must be set before the import

import essentia  # noqa: E402

essentia.log.infoActive = False  # before essentia.standard, which already logs on import
essentia.log.warningActive = False

import essentia.standard as es  # noqa: E402

from .tags import camelot  # noqa: E402

MODELS_URL = "https://essentia.upf.edu/models"
MODELS_DIR = Path(os.environ.get("TAGWERK_MODELS", Path.home() / ".cache" / "tagwerk" / "models"))

EFFNET = "feature-extractors/discogs-effnet/discogs-effnet-bs64-1"
MUSICNN = "feature-extractors/musicnn/msd-musicnn-1"
STYLES = "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1"
DEAM = "classification-heads/deam/deam-msd-musicnn-2"
# name -> (model, "positive" class); all of them run on Discogs-EffNet embeddings
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
    """Downloads the model's .pb and .json into the cache on first use. Returns (.pb path, metadata)."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("json", "pb"):
        dest = MODELS_DIR / f"{Path(name).name}.{ext}"
        if not dest.exists():
            tmp = dest.with_name(dest.name + ".part")  # no half-downloaded models if interrupted
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
    """Loads the models once, then analyzes one track at a time."""

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
        self.key = es.KeyExtractor(profileType="edma")  # profile tuned for electronic dance music
        self.resample = es.Resample(inputSampleRate=44100, outputSampleRate=16000, quality=4)  # as in training

    def analyze(self, path):
        audio = es.MonoLoader(filename=str(path), sampleRate=44100)()
        bpm = self.rhythm(audio)[0]
        key, scale, strength = self.key(audio)
        audio16 = self.resample(audio)
        emb = self.effnet(audio16)  # (frames, 1280)
        styles = self.styles(emb).mean(axis=0)
        top = styles.argsort()[::-1][:3]
        valence, arousal = self.deam(self.musicnn(audio16)).mean(axis=0)
        top_styles = [(self.style_names[i], round(float(styles[i]), 3)) for i in top]
        return {
            "bpm": round(float(bpm), 1),  # raw detector value; half-time folding happens on read (cli._results)
            "key": f"{key} {scale}",
            "camelot": camelot(key, scale),
            "key_strength": round(float(strength), 2),
            "styles": top_styles,
            "moods": {k: round(float(m(emb).mean(axis=0)[i]), 3) for k, (m, i) in self.heads.items()},
            "valence": round(float(valence), 2),
            "arousal": round(float(arousal), 2),
            "duration": round(len(audio) / 44100, 1),
            "embedding": emb.mean(axis=0),  # for personal categories and similar tracks, without re-analysis
        }
