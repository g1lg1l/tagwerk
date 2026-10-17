# Tagwerk

Analizza la tua musica in locale e la tagga da solo: BPM, key (Camelot), stile Discogs (techno, dub techno, minimal, acid, DnB…), mood, voce/strumentale ed energia. I risultati finiscono in un CSV e, se vuoi, nel campo *Comment* dei file, così rekordbox li può usare nelle intelligent playlist.

## Setup (Windows → WSL2)

Essentia gira solo su Linux/macOS, quindi su Windows si usa WSL2 (Ubuntu).

```bash
python3 -m venv ~/.venvs/tagwerk          # se manca ensurepip: --without-pip e poi get-pip.py
~/.venvs/tagwerk/bin/pip install -e /mnt/c/Users/<tu>/workspace/tagwerk
```

Al primo avvio i modelli (~25 MB) vengono scaricati in `~/.cache/tagwerk/models` (cambiabile con `TAGWERK_MODELS`).

## Uso

La musica su Windows da WSL si trova sotto `/mnt/c/...`.

```bash
# prova su 50 pezzi, solo CSV: i file non vengono toccati
tagwerk "/mnt/c/Users/<tu>/Music/techno" --limit 50 --csv tagwerk.csv

# tutta la cartella; i pezzi già analizzati non vengono rianalizzati (cache in tagwerk.db)
tagwerk "/mnt/c/Users/<tu>/Music" --csv tagwerk.csv

# scrive i tag nel Comment dei file
tagwerk "/mnt/c/Users/<tu>/Music" --write-tags
```

Il CSV usa `;` e la virgola decimale, così l'Excel italiano lo apre direttamente.

## Tag

Nel Comment viene scritto un blocco come questo, davanti al commento che c'era già (per esempio quello di Mixed In Key), che resta intatto:

```
tw: #dub-techno #techno #dark E6; 8A - Energy 7
```

- **stili**: il primo sempre, il 2° e il 3° solo sopra soglia (`STYLE_MIN` in `tagwerk/tags.py`)
- **mood**: `#aggressive #chill #happy #dark #vocal` sopra `MOOD_MIN`; il vocabolario si cambia in `MOOD_TAGS`
- **E1–E10**: energia, derivata dall'arousal

I tag vengono ricalcolati dalla cache, quindi cambiare soglie o vocabolario e rilanciare con `--write-tags` non richiede di rianalizzare.

**rekordbox**: dopo aver scritto i tag, sui pezzi già importati serve tasto destro › *Reload Tag*. Poi puoi creare intelligent playlist con la condizione *Comments contains* `#dub-techno`.

## Test

```bash
python tests/test_tags.py
```

## Modelli

[Essentia models](https://essentia.upf.edu/models.html) (MTG, Universitat Pompeu Fabra), licenza CC BY-NC-SA 4.0: uso non commerciale.
