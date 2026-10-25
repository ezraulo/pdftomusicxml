# pdftomusicxml

A small web app for practising from sheet music. Upload a **PDF or photo** of a score
(read by [Audiveris](https://github.com/Audiveris/audiveris) 5.10.2), a **MuseScore** file
or a **MusicXML** file, and get it back as **MuseScore (.mscz), MusicXML, MIDI and PDF**,
at a slower **practice tempo**. There is also an export for **Violy Creator Studio**,
which needs one file per part.

## Getting started

The app is a Docker image. Pick whichever way of running it suits you; once it's running,
open the page, upload a score, and the converted files download.

### A. On your computer (Docker)

Needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker
Engine) running.

```bash
docker run --rm -p 8080:8080 ghcr.io/ezraulo/pdftomusicxml:latest
```

Then open <http://localhost:8080>. Stop it with Ctrl+C. The image is published from
`main` by the *Publish image* workflow (built natively for both architectures); `docker pull ghcr.io/ezraulo/pdftomusicxml:latest`
fetches a newer one.

Or, from a copy of this repository, with Docker Compose (keeps running in the background):

```bash
git clone https://github.com/ezraulo/pdftomusicxml.git
cd pdftomusicxml
docker compose pull && docker compose up -d     # published image
docker compose up -d --build                    # or build it yourself (10-15 min)
docker compose down                             # stop
```

The published image works on Intel/AMD machines and on ARM ones such as Apple-silicon
Macs: Docker picks the right version by itself.

### B. In GitHub Codespaces (from any browser, including iPad)

A codespace is a cloud computer GitHub runs for you; this repository is set up so the
app starts in it by itself.

1. On the repository page, choose **Code > Codespaces > Create codespace on main**
   (the default 2-core machine is enough).
2. Wait for it to start: it fetches the image and starts the app (a minute or two; if
   the image can't be fetched it builds it, 10-15 minutes).
3. Open the **Ports** tab and open **Sheet music converter (8080)**. The address only
   works for you while you're signed in to GitHub (the port is private by default;
   keep it that way).
4. When you're done, stop the codespace (**Codespaces > ... > Stop codespace**) or let
   it stop itself after 30 idle minutes. Next time, reopen the same codespace: it starts
   faster.

Personal GitHub accounts include a monthly allowance of free Codespaces use (hours on
the 2-core machine, plus storage for stopped codespaces); see *Settings > Billing* for
what's left. Delete codespaces you no longer need, because stored ones count too.

### C. Other hosts

Anything that runs a Docker image works, but reading PDFs with Audiveris needs about
**3-4 GB of memory**, which most free tiers (often 512 MB) don't offer. Two that fit:

- **Google Cloud Run** (pay per use):

  ```bash
  gcloud run deploy pdftomusicxml --image ghcr.io/ezraulo/pdftomusicxml:latest \
    --region europe-west2 --memory 4Gi --cpu 2 --timeout 900 --concurrency 1
  ```

  Concurrency 1 matches the single worker: each instance converts one score at a time.
  (Cloud Run may need the image copied to Google's Artifact Registry first.)
- **Hugging Face Spaces**, free CPU tier (2 vCPU, 16 GB), as a Docker Space.

The app has no login: anyone who can reach its address can use it. Keep a hosted copy
private, or put it behind the host's own sign-in.

## Endpoints

| Route | What it does |
| ----- | ------------ |
| `GET /` | Upload page |
| `GET /healthz` | `{"status": "ok", "audiveris": true, "webmscore": true}` |
| `POST /convert` | Multipart form, described below |

`POST /convert` fields:

| Field | Values |
| ----- | ------ |
| `file` | PDF, PNG, JPG or TIFF (read by Audiveris); `.mscz`/`.mscx`; `.musicxml`/`.xml`/`.mxl` (default limit 40 MB) |
| `format` | `all` (default): zip of `.mscz`, `.musicxml`, `.mid`, `.pdf` · `musicxml` · `mxl` · `violy` |
| `slow_by` | play the first marked tempo this many BPM slower (default `20`) |
| `default_tempo` | tempo when the score has none (default `40`) |
| `tempo` | optional: force the first tempo to this BPM (wins over both above) |
| `title`, `composer` | `violy` only, optional: written into every part |
| `mscz` | `violy` only: `1` (default) also writes a `.mscz` per part, `0` skips it |

- `all` and `violy` always return a zip with a READ-ME. `musicxml` and `mxl` return one
  file, or a zip when Audiveris split the score into several movements at a page break
  it couldn't bridge.
- The `.pdf` is engraved by MuseScore from what was read, so for a scan it shows what
  Audiveris understood: compare it with the original.
- Audiveris sometimes reads a short divisi passage (one instrument on two staves) as a
  separate part that plays only where the real part rests, and hides each part in the
  systems where the other plays; MuseScore then hides the real part everywhere. The app
  merges such a passage back into the real part (as two voices) and shows all staves;
  the READ-ME lists the bars, so they can be checked. Audiveris's log is printed in the
  server's output (the `docker run` window).
- If Audiveris can't read some pages, it retries without them. The missing page numbers
  are in the `X-Excluded-Sheets` response header and the READ-ME.
- Failures return JSON `{"error", "kind", "log_tail"}` with HTTP 422. `kind` is one of
  `rhythm-analysis-abort` (retrying won't help), `unreadable-input`, `timeout`,
  `omr-failed`, `unsupported-score` or `violy-split-failed`.

```bash
curl -F file=@score.pdf -OJ http://localhost:8080/convert                     # all formats
curl -F file=@song.mscz -F format=violy -F tempo=60 -OJ http://localhost:8080/convert
```

## Practice tempo

Practice apps often let you speed a piece up but not slow it down without a paid plan
(Violy, which does start a piece at the tempo written in the file), or don't let you
change the tempo at all on the free plan (PlayScore 2). So every output starts slower
by default:

- The **first tempo** plays at the marked tempo **minus 20 BPM** (never below 20), or at
  **40 BPM** if the score has no tempo. "Force a tempo" sets it to an exact number.
- **Every original marking stays** on the score, and later tempo changes are left as
  written. A label goes above the first marking: "Practice tempo: 100 BPM (original
  120 BPM)", or "Practice tempo: 40 BPM" for an unmarked score.
- Tempos are counted in quarter notes, as MusicXML does (a dotted-quarter = 60 in 6/8 is
  90 quarter beats).
- Tick "Keep the score's own tempo" (or send `slow_by=0`, `default_tempo=`) to leave it.

A MuseScore file with no visible tempo marking counts as unmarked, even if MuseScore
plays it at a hidden default tempo.

**PlayScore 2:** the free plan only opens photos taken in the app, so it can't import
these files. Its own score folders do hold an ordinary MusicXML file (`doc.xml`, read by
PlayScore's scanner), which can be uploaded here. PlayScore drops printed tempos, so set
"Force a tempo" for those.

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
- **Tempo:** Creator Studio has no tempo field, and in the app players can speed a piece
  up but not slow it down (a file with no tempo starts at 100 BPM). The practice tempo
  (above) is copied into every part, because MuseScore and Audiveris only mark the top one.
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

The splitter also works on its own, without the app (standard library only):

```bash
python violy_export.py score.musicxml outdir --title "My Piece" --composer "Someone" --slow-by 20 --unmarked-tempo 40
python violy_export.py score.mxl outdir
```

Always check the notation before uploading: optical music recognition makes mistakes.

## Configuration (environment variables)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `PORT` | `8080` | |
| `AUDIVERIS_CMD` | `/opt/audiveris/bin/Audiveris` | |
| `OMR_TIMEOUT_S` | `840` | Seconds before an Audiveris run is killed |
| `MAX_UPLOAD_MB` | `40` | |
| `WEBMSCORE_CMD` | `node /opt/mscz/convert.cjs` in the image | Unset means no `.mscz`, MIDI or PDF output and no `.mscz` input |

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
npm ci --prefix mscz            # optional: enables the webmscore tests
python -m pytest tests
```

The tests use a stand-in for Audiveris (`tests/fake_audiveris.py`) and the sample score in
`docs/examples/`, so they need no Java. The **Docker smoke test** in GitHub Actions
builds the real image and converts the sample PDF (with real Audiveris) and the sample
`.mscz` to all formats and to Violy parts.

| File | Role |
| ---- | ---- |
| `app.py` | Flask app |
| `omr.py` | Runs Audiveris: movements, retry without broken pages, failure kinds |
| `violy_export.py` | Splits a score into Violy-ready single-part files; the practice-tempo rule |
| `score_tools.py`, `mscz/convert.cjs` | `.mscz`/MusicXML/MIDI/PDF conversion via webmscore in Node |

## Licence

AGPL-3.0 (see [LICENSE](./LICENSE)), matching Audiveris. Third-party parts and their
licences are listed in [NOTICE.md](./NOTICE.md).

This repository started as a copy of LibreScore's mscz-api; that code has been replaced
and remains in the git history.
