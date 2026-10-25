"""
app.py - convert sheet music between PDF, MuseScore, MusicXML and MIDI, with a
practice-tempo option and a Violy Creator Studio export.

    GET  /         upload page (static/index.html)
    GET  /healthz  {"status": "ok", "audiveris": bool, "webmscore": bool}
    POST /convert  multipart form:
                     file      PDF or image (read by Audiveris), MuseScore .mscz/.mscx,
                               or MusicXML .musicxml/.xml/.mxl
                     format    all (default): zip of .mscz, .musicxml, .mid and .pdf
                               | musicxml | mxl | violy
                     slow_by   lower marked tempos by this many BPM (default 20)
                     default_tempo  tempo when the score has none (default 40)
                     tempo     optional: force this BPM for this piece (wins over the
                               two above). Practice apps such as Violy let players speed
                               a piece up but not slow it down, so scores start slow.
                     title     violy only, optional: work title written into each part
                     composer  violy only, optional
                     mscz      violy only, "1" (default) or "0": also write .mscz files

musicxml and mxl return one file for a one-movement score, otherwise a zip. "all" and
"violy" always return a zip with a READ-ME.
"""
import io
import os
import re
import shlex
import shutil
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Flask, jsonify, request, send_file

import omr
import score_tools
from violy_export import _read_xml_bytes, set_score_tempo, split_for_violy

OMR_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MUSESCORE_SUFFIXES = {".mscz": "mscz", ".mscx": "mscx"}
MUSICXML_SUFFIXES = {".musicxml", ".xml", ".mxl"}
FORMATS = ("all", "musicxml", "mxl", "violy")
# Practice apps let players speed a piece up, but slowing below the file's tempo
# needs a paid plan, so scores start slow unless told otherwise (quarter-note BPM).
DEFAULT_SLOW_BY = "20"
DEFAULT_UNMARKED_TEMPO = "40"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "40")) * 1024 * 1024


class ConvertError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


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


def _mxl(name: str, xml: bytes) -> bytes:
    """Compressed MusicXML: a zip with META-INF/container.xml pointing at the score."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<container><rootfiles>'
                   '<rootfile full-path="%s" media-type="application/vnd.recordare.musicxml+xml"/>'
                   '</rootfiles></container>\n' % name)
        z.writestr(name, xml)
    return buf.getvalue()


def _tempo_settings():
    tempos = {}
    for field, default, low in (("tempo", "", 10), ("slow_by", DEFAULT_SLOW_BY, 0),
                                ("default_tempo", DEFAULT_UNMARKED_TEMPO, 10)):
        text = request.form.get(field, default).strip()
        try:
            tempos[field] = float(text) if text else None
        except ValueError:
            raise ConvertError(400, "%s must be a number of beats per minute." % field)
        if tempos[field] is not None and not low <= tempos[field] <= 400:
            raise ConvertError(400, "%s must be between %d and 400 BPM." % (field, low))
    return tempos["tempo"], tempos["slow_by"], tempos["default_tempo"]


def _tempo_note(tempo, slow_by, unmarked_tempo, where="each part") -> str:
    if tempo:
        return "forced to %g BPM (quarter note)." % tempo
    if not slow_by and not unmarked_tempo:
        return "as in the score."
    return ("practice tempo, marked at the top of %s: the score's tempo minus %s BPM, "
            "or %s if the score has none. Speed it up in your practice app as you learn it."
            % (where, "%g" % slow_by if slow_by else "0",
               "%g BPM" % unmarked_tempo if unmarked_tempo else "no tempo"))


def _read_upload(upload, tmp: Path):
    """The uploaded score as [(name.musicxml, MusicXML bytes)], one per movement,
    plus the OMR result (None unless Audiveris ran) and the original MuseScore file."""
    suffix = Path(upload.filename).suffix.lower()
    stem = _stem(upload.filename)
    data_path = tmp / (stem + suffix)
    upload.save(data_path)
    if suffix in OMR_SUFFIXES:
        result = omr.convert_score(data_path, tmp / "out", audiveris_command(), omr_timeout_s())
        # Show what Audiveris said in the server log (the docker run window), so
        # problems with a score can be diagnosed.
        print("--- Audiveris log for %s (last part) ---\n%s\n--- end of Audiveris log ---"
              % (upload.filename, result.log_tail), file=sys.stderr, flush=True)
        if not result.ok:
            raise ConvertError(422, result.failure.detail, kind=result.failure.kind,
                               log_tail=result.log_tail)
        movements = []
        for p in result.movements:
            xml, notes = omr.tidy_score(_read_xml_bytes(p.read_bytes()))
            movements.append((p.stem + ".musicxml", xml))
            result.tidy_notes += ["%s: %s" % (p.stem, n) for n in notes]
        return movements, result, None
    if suffix in MUSESCORE_SUFFIXES:
        if score_tools.command() is None:
            raise ConvertError(503, "This server cannot read MuseScore files: "
                               + score_tools.NOT_CONFIGURED)
        original = (data_path.read_bytes(), MUSESCORE_SUFFIXES[suffix])
        try:
            xml = score_tools.to_musicxml(stem + suffix, *original)
        except ValueError as e:
            raise ConvertError(422, "Could not read the MuseScore file: %s" % e,
                               kind="unreadable-input")
        return [(stem + ".musicxml", xml)], None, original
    if suffix in MUSICXML_SUFFIXES:
        try:
            xml = _read_xml_bytes(data_path.read_bytes())
            ET.fromstring(xml)
        except (zipfile.BadZipFile, StopIteration, KeyError, ET.ParseError) as e:
            raise ConvertError(422, "Could not read the MusicXML file: %s" % e,
                               kind="unreadable-input")
        return [(stem + ".musicxml", xml)], None, None
    raise ConvertError(400, "Unsupported file type %r. Use PDF, PNG, JPG, TIFF, MuseScore "
                       "(.mscz, .mscx) or MusicXML (.musicxml, .xml, .mxl)." % suffix)


def _score_tempo(name, xml, tempo, slow_by, unmarked_tempo):
    try:
        return set_score_tempo(xml, tempo, slow_by, unmarked_tempo)
    except ValueError as e:
        raise ConvertError(422, "Could not set the tempo of %s: %s" % (name, e),
                           kind="unsupported-score")


def _violy_files(movements, tempo, slow_by, unmarked_tempo, excluded, tidy_notes=()):
    title = (request.form.get("title") or "").strip() or None
    composer = (request.form.get("composer") or "").strip() or None
    parts = {}
    for index, (name, xml) in enumerate(movements, start=1):
        try:
            split = split_for_violy(xml, title=title, composer=composer, tempo=tempo,
                                    slow_by=slow_by, unmarked_tempo=unmarked_tempo)
        except (ValueError, KeyError) as e:
            raise ConvertError(422, "Could not split %s into Violy parts: %s" % (name, e),
                               kind="violy-split-failed")
        prefix = "movement-%d/" % index if len(movements) > 1 else ""
        parts.update({prefix + n: b for n, b in split.items()})

    files = dict(parts)
    mscz_files, mscz_errors = {}, []
    if request.form.get("mscz", "1") != "0":
        mscz_files, mscz_errors = score_tools.to_mscz(parts)
        files.update(mscz_files)
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
        "* Tempo: " + _tempo_note(tempo, slow_by, unmarked_tempo),
        "* Violy wants a MusicXML file AND a MuseScore .mscz file for each part.",
    ]
    if mscz_files:
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
    files["READ-ME-violy.txt"] = _readme(lines, mscz_errors, excluded, tidy_notes)
    return files


def _all_files(movements, original, tempo, slow_by, unmarked_tempo, excluded, tidy_notes=()):
    files, sources = {}, {}
    for name, xml in movements:
        xml, changed = _score_tempo(name, xml, tempo, slow_by, unmarked_tempo)
        files[name] = xml
        # Convert from the uploaded MuseScore file when the tempo is untouched, so
        # nothing is lost in a round trip through MusicXML.
        sources[name] = original if (original and not changed) else (xml, "musicxml")
    converted, errors = score_tools.convert(sources, ["mscz", "midi", "pdf"])
    files.update(converted)
    lines = [
        "Converted score",
        "===============",
        "",
        "* .mscz: MuseScore. .musicxml: MusicXML for other notation apps.",
        "  .mid: MIDI for playback. .pdf: printable score, engraved by MuseScore.",
        "* Tempo: " + _tempo_note(tempo, slow_by, unmarked_tempo, "the score"),
    ]
    if excluded is not None:
        lines += ["* This was read from a scan by optical music recognition, which makes",
                  "  mistakes: compare the .pdf with the original before relying on it."]
    files["READ-ME.txt"] = _readme(lines, errors, excluded, tidy_notes)
    return files


def _readme(lines, errors, excluded, tidy_notes=()) -> bytes:
    if tidy_notes:
        lines = lines + ["", "Fixed after reading (check these bars against the original):"] + [
            "* " + n for n in tidy_notes]
    if errors:
        lines = lines + ["", "Problems:"] + ["* " + e for e in errors]
    if excluded:
        lines = lines + ["", "WARNING: Audiveris could not read page(s) %s; they are missing "
                         "here." % ", ".join(map(str, excluded))]
    return ("\n".join(lines) + "\n").encode("utf-8")


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/healthz")
def healthz():
    cmd = audiveris_command()
    return jsonify(status="ok",
                   audiveris=bool(cmd) and shutil.which(cmd[0]) is not None,
                   webmscore=score_tools.command() is not None)


@app.post("/convert")
def convert():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error(400, "No file uploaded (form field 'file').")
    fmt = (request.form.get("format") or "all").lower()
    if fmt not in FORMATS:
        return _error(400, "format must be one of: %s" % ", ".join(FORMATS))
    stem = _stem(upload.filename)
    try:
        tempo, slow_by, unmarked_tempo = _tempo_settings()
        with tempfile.TemporaryDirectory(prefix="convert-") as tmp:
            movements, result, original = _read_upload(upload, Path(tmp))
        excluded = result.excluded_sheets if result else None
        tidy_notes = result.tidy_notes if result else []

        if fmt == "violy":
            files = _violy_files(movements, tempo, slow_by, unmarked_tempo, excluded, tidy_notes)
            response = send_file(_zip(files), mimetype="application/zip",
                                 as_attachment=True, download_name=stem + "-violy.zip")
        elif fmt == "all":
            files = _all_files(movements, original, tempo, slow_by, unmarked_tempo, excluded,
                               tidy_notes)
            response = send_file(_zip(files), mimetype="application/zip",
                                 as_attachment=True, download_name=stem + ".zip")
        else:
            files = {}
            for name, xml in movements:
                xml, _ = _score_tempo(name, xml, tempo, slow_by, unmarked_tempo)
                if fmt == "mxl":
                    files[Path(name).with_suffix(".mxl").name] = _mxl(name, xml)
                else:
                    files[name] = xml
            if len(files) == 1:
                name, blob = next(iter(files.items()))
                mimetype = ("application/vnd.recordare.musicxml" if fmt == "mxl"
                            else "application/vnd.recordare.musicxml+xml")
                response = send_file(io.BytesIO(blob), mimetype=mimetype,
                                     as_attachment=True, download_name=name)
            else:
                response = send_file(_zip(files), mimetype="application/zip",
                                     as_attachment=True, download_name=stem + ".zip")
    except ConvertError as e:
        return _error(e.status, e.message, **e.extra)

    if excluded:
        response.headers["X-Excluded-Sheets"] = ",".join(map(str, excluded))
    return response


@app.errorhandler(413)
def too_large(_e):
    return _error(413, "File too large (limit %d MB)." % (app.config["MAX_CONTENT_LENGTH"] // 1048576))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
