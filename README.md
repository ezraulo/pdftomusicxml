# pdftomusicxml

Upload sheet music as a PDF or image and get MusicXML back. The conversion is done by
[Audiveris](https://github.com/Audiveris/audiveris) 5.10.2 behind a small Flask app.
There is also an export for **Violy Creator Studio**, which needs one file per part.

## Endpoints

| Route | What it does |
| ----- | ------------ |
| `GET /` | Upload page |
| `GET /healthz` | `{"status": "ok", "audiveris": true, "mscz": true}` |
| `POST /convert` | Multipart form, described below |

`POST /convert` fields:

| Field | Values |
| ----- | ------ |
| `file` | PDF, PNG, JPG or TIFF (default limit 40 MB) |
| `format` | `mxl` (default), `musicxml` (uncompressed) or `violy` |
| `title`, `composer` | `violy` only, optional: written into every part |
| `slow_by` | `violy` only: lower marked tempos by this many BPM (default `20`) |
| `default_tempo` | `violy` only: tempo when the score has none (default `40`) |
| `tempo` | `violy` only, optional: force this BPM for the piece (wins over both above) |
| `mscz` | `violy` only: `1` (default) also writes a `.mscz` per part, `0` skips it |

- A one-movement score comes back as a single file. Audiveris sometimes splits a score
  into several movements at a page break it can't bridge; you then get a zip with all of
  them.
- If Audiveris can't read some pages, it retries without them. The missing page numbers
  are in the `X-Excluded-Sheets` response header (and the Violy read-me).
- Failures return JSON `{"error", "kind", "log_tail"}` with HTTP 422. `kind` is one of
  `rhythm-analysis-abort` (retrying won't help), `unreadable-input`, `timeout`,
  `omr-failed` or `violy-split-failed`.

```bash
curl -F file=@score.pdf -F format=musicxml -OJ http://localhost:8080/convert
```

## Violy Creator Studio export

`format=violy` returns a zip that's ready for Violy's upload form
([creator.violy.app](https://creator.violy.app)). The requirements below were found by
testing (October 2026), not from Violy's documentation:

- **One MusicXML per part.** The score is split with `violy_export.py`.
- **The part id must be `P1`.** Any other id (for example `P4`, the fourth part of a
  score) crashes Violy's renderer ("xml file error"), even for a single measure.
- **Real notes.** A part with no measures is refused by the splitter.
- **Uncompressed** `.musicxml`, not `.mxl`.
- **Title and composer:** the files carry whatever you typed (or "Untitled"). You still
  have to **pick the title, instrument and composer from Violy's lists** in the form;
  typed text alone is ignored.
- **A `.mscz` per part** is also required. The zip includes one, written by
  [webmscore](https://github.com/LibreScore/webmscore) (MuseScore 4 compiled to
  WebAssembly). Violy accepts them (tested October 2026).
- **Tempo:** Creator Studio has no tempo field. In the app, players can speed a piece up
  but (without a paid plan) not slow it down, and a file with no tempo starts at 100 BPM.
  So the Violy export starts every part at a **practice tempo**: the marked tempo
  **minus 20 BPM** (never below 20), or **40 BPM** if the score has no tempo. It's marked
  at the top, e.g. "Practice tempo: 100 BPM (original 120 BPM)". Tempo words such as
  "Allegro" are kept, and later tempo changes are also lowered by 20. Both numbers can
  be changed in the form, and "Force a tempo for this piece" overrides them.
- Violy plays one part at a time: a toggle chooses which uploaded part (the main part
  or the accompaniment) plays, each as its own instrument; they don't play together.
- A part with three staves (two instruments combined in one part) doesn't render in
  Violy at all, so parts can't be combined that way. A two-staff piano part works but
  is shown on one staff.
- The `.mscz` is required, but Violy doesn't appear to use its content: any `.mscz`
  is accepted, and the notation and sound come from the MusicXML. The export still
  makes a matching one per part.
- There is no MIDI upload field. "Add accompaniment" is a second block with the same
  fields.

Not causes of upload errors (all tested): MusicXML version, identification block, MIDI
instrument entries, lyrics, layout/print tags, measure widths, tempo and Pizz markings,
key without mode, bass clef, ties, two-staff piano part.

**Check a file without submitting:** in Violy's Create Music form, attach a MusicXML and
click *Preview*. Good files show notation; bad ones show a blank dialog.

The splitter also works on its own, with no Audiveris (standard library only):

```bash
python violy_export.py score.musicxml outdir --title "My Piece" --composer "Someone" --slow-by 20 --unmarked-tempo 40
python violy_export.py score.mxl outdir
```

Always check the notation before uploading: optical music recognition makes mistakes.

## Running

```bash
docker compose up --build        # then open http://localhost:8080
```

The first build compiles Audiveris and takes about 10–15 minutes.

### Cloud Run

```bash
gcloud run deploy pdftomusicxml --source . --region europe-west2 \
  --memory 4Gi --cpu 2 --timeout 900 --concurrency 1
```

Concurrency 1 matches the single gunicorn worker: Audiveris is memory-hungry, so each
container converts one score at a time.

### Configuration (environment variables)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `PORT` | `8080` | |
| `AUDIVERIS_CMD` | `/opt/audiveris/bin/Audiveris` | |
| `OMR_TIMEOUT_S` | `840` | Seconds before an Audiveris run is killed |
| `MAX_UPLOAD_MB` | `40` | |
| `MSCZ_CMD` | `node /opt/mscz/to_mscz.cjs` in the image | Unset means no `.mscz` files |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
npm ci --prefix mscz            # optional: enables the .mscz test
python -m pytest tests
```

The tests use a stand-in for Audiveris (`tests/fake_audiveris.py`) and the sample score in
`docs/examples/`, so they need no Java. The **Docker smoke test** in GitHub Actions
builds the real image and converts `docs/examples/...FULLSCORE.pdf` with real Audiveris.

| File | Role |
| ---- | ---- |
| `app.py` | Flask app |
| `omr.py` | Runs Audiveris: movements, retry without broken pages, failure kinds |
| `violy_export.py` | Splits a score into Violy-ready single-part files |
| `mscz_export.py`, `mscz/to_mscz.cjs` | `.mscz` writing via webmscore in Node |

## Licence

AGPL-3.0 (see [LICENSE](./LICENSE)), matching Audiveris. Third-party parts and their
licences are listed in [NOTICE.md](./NOTICE.md).

This repository started as a copy of LibreScore's mscz-api; that code has been replaced
and remains in the git history.
