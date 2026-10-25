"""
app.py - PDF/image to MusicXML with Audiveris, plus a Violy Creator Studio export.

    GET  /         upload page (static/index.html)
    GET  /healthz  {"status": "ok", "audiveris": bool, "mscz": bool}
    POST /convert  multipart form:
                     file      PDF or image (png, jpg, tif)
                     format    mxl (default) | musicxml | violy
                     title     violy only, optional: work title written into each part
                     composer  violy only, optional
                     slow_by   violy only: lower marked tempos by this many BPM (default 20)
                     default_tempo  violy only: tempo when the score has none (default 40)
                     tempo     violy only, optional: force this BPM for this piece (wins
                               over the two above). Violy lets players speed a piece
                               up but not slow it down, so the export starts slow.
                     mscz      violy only, "1" (default) or "0": also write .mscz files

A one-movement result comes back as one file; several movements come back as a zip.
"violy" always returns a zip: one MusicXML (part id P1) per part, a .mscz per part
when available, and READ-ME-violy.txt.
"""
import io
import os
import re
import shlex
import shutil
import tempfile
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

import mscz_export
import omr
from violy_export import _read_xml_bytes, split_for_violy

ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
FORMATS = ("mxl", "musicxml", "violy")
# Violy lets players speed a piece up, but slowing below the file's tempo needs a paid
# plan, so the Violy export starts slow unless told otherwise (quarter-note BPM).
DEFAULT_SLOW_BY = "20"
DEFAULT_UNMARKED_TEMPO = "40"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "40")) * 1024 * 1024


def audiveris_command():
    return shlex.split(os.environ.get("AUDIVERIS_CMD", "/opt/audiveris/bin/Audiveris"))


def omr_timeout_s() -> float:
    # Below gunicorn's and Cloud Run's 900 s, so a slow score gets a clear error.
    return float(os.environ.get("OMR_TIMEOUT_S", "840"))


def _stem(filename: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._")
    return stem[:80] or "score"


def _error(status: int, message: str, **extra):
    return jsonify(error=message, **extra), status


def _zip(files) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in files.items():
            z.writestr(name, blob)
    buf.seek(0)
    return buf


def _violy_readme(title, composer, tempo, slow_by, unmarked_tempo, excluded, mscz_errors, mscz_written) -> str:
    lines = [
        "Violy Creator Studio export",
        "===========================",
        "",
        "Each .musicxml here holds ONE part, with the part id P1 that Violy needs.",
        "",
        "Before uploading:",
        "* Check the notation. Optical music recognition makes mistakes; open the",
        "  files in MuseScore (or attach one in Violy and click Preview) first.",
        "* Title and composer: the files say title = %r, composer = %r." % (
            title or "Untitled", composer or "(none)"),
        "  Set them yourself: in Violy's form, pick the title, instrument and",
        "  composer from Violy's lists. Typed text alone is ignored.",
        "* Tempo: %s" % (
            "forced to %g BPM (quarter note) in every part." % tempo if tempo else
            "practice tempo, marked at the top of each part: the score's tempo minus "
            "%s BPM, or %s BPM if the score has none. Speed it up in Violy as you learn it."
            % ("%g" % slow_by if slow_by else "0", "%g" % unmarked_tempo if unmarked_tempo
               else "Violy's default of 100")),
        "* Violy wants a MusicXML file AND a MuseScore .mscz file for each part.",
    ]
    if mscz_written:
        lines += [
            "  The .mscz files here were written by webmscore (MuseScore 4 in",
            "  WebAssembly). Violy accepts them; it seems to need the file but to",
            "  take the notation and sound from the .musicxml.",
        ]
    else:
        lines += [
            "  No .mscz files were made. Open each .musicxml in MuseScore and use",
            "  File > Save As to make one.",
        ]
    if mscz_errors:
        lines += ["", ".mscz problems:"] + ["* " + e for e in mscz_errors]
    if excluded:
        lines += ["", "WARNING: Audiveris could not read page(s) %s; they are missing here."
                  % ", ".join(map(str, excluded))]
    return "\n".join(lines) + "\n"


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/healthz")
def healthz():
    cmd = audiveris_command()
    return jsonify(status="ok",
                   audiveris=bool(cmd) and shutil.which(cmd[0]) is not None,
                   mscz=mscz_export.mscz_command() is not None)


@app.post("/convert")
def convert():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error(400, "No file uploaded (form field 'file').")
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return _error(400, "Unsupported file type %r. Use PDF, PNG, JPG or TIFF." % suffix)
    fmt = (request.form.get("format") or "mxl").lower()
    if fmt not in FORMATS:
        return _error(400, "format must be one of: %s" % ", ".join(FORMATS))
    stem = _stem(upload.filename)

    with tempfile.TemporaryDirectory(prefix="convert-") as tmp:
        tmp = Path(tmp)
        input_path = tmp / (stem + suffix)
        upload.save(input_path)
        result = omr.convert_score(input_path, tmp / "out", audiveris_command(), omr_timeout_s())
        if not result.ok:
            return _error(422, result.failure.detail, kind=result.failure.kind,
                          log_tail=result.log_tail)
        movements = [(p.name, p.read_bytes()) for p in result.movements]

    headers = {}
    if result.excluded_sheets:
        headers["X-Excluded-Sheets"] = ",".join(map(str, result.excluded_sheets))

    if fmt in ("mxl", "musicxml"):
        if fmt == "musicxml":
            movements = [(Path(n).with_suffix(".musicxml").name, _read_xml_bytes(b))
                         for n, b in movements]
        if len(movements) == 1:
            name, blob = movements[0]
            mimetype = ("application/vnd.recordare.musicxml" if fmt == "mxl"
                        else "application/vnd.recordare.musicxml+xml")
            response = send_file(io.BytesIO(blob), mimetype=mimetype,
                                 as_attachment=True, download_name=name)
        else:
            response = send_file(_zip(dict(movements)), mimetype="application/zip",
                                 as_attachment=True, download_name=stem + ".zip")
        response.headers.update(headers)
        return response

    # Violy export
    title = (request.form.get("title") or "").strip() or None
    composer = (request.form.get("composer") or "").strip() or None
    tempos = {}
    for field, default, low in (("tempo", "", 10), ("slow_by", DEFAULT_SLOW_BY, 0),
                                ("default_tempo", DEFAULT_UNMARKED_TEMPO, 10)):
        text = request.form.get(field, default).strip()
        try:
            tempos[field] = float(text) if text else None
        except ValueError:
            return _error(400, "%s must be a number of beats per minute." % field)
        if tempos[field] is not None and not low <= tempos[field] <= 400:
            return _error(400, "%s must be between %d and 400 BPM." % (field, low))
    tempo, slow_by, unmarked_tempo = tempos["tempo"], tempos["slow_by"], tempos["default_tempo"]
    parts = {}
    for index, (name, blob) in enumerate(movements, start=1):
        try:
            split = split_for_violy(blob, title=title, composer=composer,
                                    tempo=tempo, slow_by=slow_by,
                                    unmarked_tempo=unmarked_tempo)
        except (ValueError, KeyError) as e:
            return _error(422, "Could not split %s into Violy parts: %s" % (name, e),
                          kind="violy-split-failed")
        prefix = "movement-%d/" % index if len(movements) > 1 else ""
        parts.update({prefix + n: b for n, b in split.items()})

    files = dict(parts)
    mscz_errors = []
    if request.form.get("mscz", "1") != "0":
        mscz_files, mscz_errors = mscz_export.to_mscz(parts)
        files.update(mscz_files)
    else:
        mscz_files = {}
    files["READ-ME-violy.txt"] = _violy_readme(
        title, composer, tempo, slow_by, unmarked_tempo, result.excluded_sheets, mscz_errors, bool(mscz_files)).encode("utf-8")
    response = send_file(_zip(files), mimetype="application/zip",
                         as_attachment=True, download_name=stem + "-violy.zip")
    response.headers.update(headers)
    return response


@app.errorhandler(413)
def too_large(_e):
    return _error(413, "File too large (limit %d MB)." % (app.config["MAX_CONTENT_LENGTH"] // 1048576))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
