import io
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pytest

import app as app_module
from conftest import EXAMPLES, FAKE_AUDIVERIS, SAMPLE_MUSICXML, SAMPLE_MXL, WEBMSCORE_HELPER

SAMPLE_MSCZ = EXAMPLES / "GeneralUser Orchestral Example.mscz"
HAVE_WEBMSCORE = (shutil.which("node") is not None
                  and (WEBMSCORE_HELPER.parent / "node_modules" / "webmscore").is_dir())
needs_webmscore = pytest.mark.skipif(not HAVE_WEBMSCORE, reason="needs node and `npm ci` in mscz/")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUDIVERIS_CMD", " ".join(FAKE_AUDIVERIS))
    monkeypatch.setenv("OMR_TIMEOUT_S", "30")
    monkeypatch.delenv("WEBMSCORE_CMD", raising=False)
    return app_module.app.test_client()


@pytest.fixture
def webmscore(monkeypatch):
    monkeypatch.setenv("WEBMSCORE_CMD", "node %s" % WEBMSCORE_HELPER)


def _post(client, name="score.pdf", data=b"%PDF-1.4 fake", **form):
    body = {"file": (io.BytesIO(data), name), **form}
    return client.post("/convert", data=body, content_type="multipart/form-data")


def _zip(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    return zipfile.ZipFile(io.BytesIO(response.data))


def _sound_tempi(xml):
    return [s.get("tempo") for s in ET.fromstring(xml).iter("sound") if s.get("tempo")]


def test_index_and_healthz(client):
    assert b"Sheet music converter" in client.get("/").data
    body = client.get("/healthz").get_json()
    assert body == {"status": "ok", "audiveris": True, "webmscore": False}


# --- format=all (default) ---------------------------------------------------

def test_all_is_the_default_and_says_when_webmscore_is_missing(client):
    z = _zip(_post(client))
    assert sorted(z.namelist()) == ["READ-ME.txt", "score.musicxml"]
    readme = z.read("READ-ME.txt").decode()
    assert "WEBMSCORE_CMD is not set" in readme and "optical music recognition" in readme


@needs_webmscore
def test_all_formats_from_a_pdf(client, webmscore):
    z = _zip(_post(client))
    assert sorted(z.namelist()) == ["READ-ME.txt", "score.mid", "score.mscz",
                                    "score.musicxml", "score.pdf"]
    assert z.read("score.pdf")[:4] == b"%PDF" and z.read("score.mid")[:4] == b"MThd"
    assert _sound_tempi(z.read("score.musicxml")) == ["40"]   # unmarked -> practice 40
    assert "Problems" not in z.read("READ-ME.txt").decode()


@needs_webmscore
def test_all_formats_from_a_musescore_file(client, webmscore):
    z = _zip(_post(client, "My Song.mscz", SAMPLE_MSCZ.read_bytes()))
    assert sorted(z.namelist()) == ["My_Song.mid", "My_Song.mscz", "My_Song.musicxml",
                                    "My_Song.pdf", "READ-ME.txt"]
    assert "optical music recognition" not in z.read("READ-ME.txt").decode()


@needs_webmscore
def test_musescore_file_with_tempo_untouched_converts_from_the_original(client, webmscore):
    z = _zip(_post(client, "song.mscz", SAMPLE_MSCZ.read_bytes(), slow_by="0", default_tempo=""))
    assert _sound_tempi(z.read("song.musicxml")) == []
    assert "song.mscz" in z.namelist() and "song.pdf" in z.namelist()


def test_musescore_file_needs_webmscore(client):
    r = _post(client, "song.mscz", SAMPLE_MSCZ.read_bytes())
    assert r.status_code == 503 and "MuseScore" in r.get_json()["error"]


# --- musicxml / mxl ---------------------------------------------------------

def test_musicxml_is_uncompressed_and_gets_the_practice_tempo(client):
    r = _post(client, format="musicxml")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith("score.musicxml")
    assert _sound_tempi(r.data) == ["40"]


def test_mxl_is_a_valid_container(client):
    r = _post(client, format="mxl")
    assert r.headers["Content-Disposition"].endswith("score.mxl")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    root_file = ET.fromstring(z.read("META-INF/container.xml")).find(".//rootfile").get("full-path")
    assert ET.fromstring(z.read(root_file)).tag == "score-partwise"


def test_multiple_movements_come_back_as_zip(client):
    names = _zip(_post(client, "two-movements.pdf", format="musicxml")).namelist()
    assert names == ["two-movements.mvt1.musicxml", "two-movements.mvt2.musicxml"]


@pytest.mark.parametrize("sample", [SAMPLE_MUSICXML, SAMPLE_MXL])
def test_musicxml_upload_skips_audiveris(client, monkeypatch, sample):
    monkeypatch.setenv("AUDIVERIS_CMD", "/nonexistent/Audiveris")
    r = _post(client, "piece" + sample.suffix, sample.read_bytes(), format="musicxml",
              slow_by="0", default_tempo="")
    assert r.status_code == 200 and ET.fromstring(r.data).tag == "score-partwise"
    assert "X-Excluded-Sheets" not in r.headers


# --- format=violy -----------------------------------------------------------

def test_violy_zip_without_mscz(client):
    z = _zip(_post(client, format="violy", title="Sample Piece", composer="A. Composer"))
    parts = [n for n in z.namelist() if n.endswith(".musicxml")]
    assert len(parts) == 20 and parts[0] == "Sample_Piece-01-Piccolo.musicxml"
    readme = z.read("READ-ME-violy.txt").decode()
    assert "'Sample Piece'" in readme and "Violy's lists" in readme
    assert "No .mscz files were made" in readme and "WEBMSCORE_CMD is not set" in readme
    assert ET.fromstring(z.read(parts[0])).find("part").get("id") == "P1"


def test_violy_from_a_musicxml_upload(client):
    z = _zip(_post(client, "piece.musicxml", SAMPLE_MUSICXML.read_bytes(), format="violy", mscz="0"))
    assert len([n for n in z.namelist() if n.endswith(".musicxml")]) == 20


def test_violy_with_movements_uses_folders(client):
    names = _zip(_post(client, "two-movements.pdf", format="violy", mscz="0")).namelist()
    assert "movement-1/Untitled-01-Piccolo.musicxml" in names
    assert "movement-2/Untitled-20-Contrabasses.musicxml" in names


@needs_webmscore
def test_violy_zip_with_mscz(client, webmscore):
    z = _zip(_post(client, format="violy"))
    mscz = sorted(n for n in z.namelist() if n.endswith(".mscz"))
    assert len(mscz) == 20 and mscz[0] == "Untitled-01-Piccolo.mscz"
    inner = zipfile.ZipFile(io.BytesIO(z.read(mscz[0])))
    assert any(n.endswith(".mscx") for n in inner.namelist())
    assert "Violy accepts them" in z.read("READ-ME-violy.txt").decode()


def test_violy_defaults_to_40_bpm_for_an_unmarked_score(client):
    z = _zip(_post(client, format="violy", mscz="0"))
    assert _sound_tempi(z.read("Untitled-19-Violoncellos.musicxml")) == ["40"]
    assert "minus 20 BPM, or 40 BPM if the score has none" in z.read("READ-ME-violy.txt").decode()


def test_violy_forced_tempo(client):
    z = _zip(_post(client, format="violy", mscz="0", tempo="66"))
    assert _sound_tempi(z.read("Untitled-19-Violoncellos.musicxml")) == ["66"]
    assert "forced to 66 BPM" in z.read("READ-ME-violy.txt").decode()


def test_violy_empty_default_tempo_leaves_unmarked_score_alone(client):
    z = _zip(_post(client, format="violy", mscz="0", default_tempo=""))
    assert _sound_tempi(z.read("Untitled-19-Violoncellos.musicxml")) == []


# --- reporting and errors ---------------------------------------------------

@pytest.mark.parametrize("fmt", ["all", "violy"])
def test_excluded_pages_are_reported(client, fmt):
    r = _post(client, "broken-page.pdf", format=fmt, mscz="0")
    assert r.headers["X-Excluded-Sheets"] == "2"
    readme = next(n for n in _zip(r).namelist() if n.startswith("READ-ME"))
    assert "page(s) 2" in _zip(r).read(readme).decode()


@pytest.mark.parametrize("kwargs, status, fragment", [
    ({"name": "score.docx"}, 400, "Unsupported file type"),
    ({"format": "midi"}, 400, "format must be"),
    ({"name": "rhythm.pdf"}, 422, "rhythm analysis"),
    ({"name": "bad.mxl", "data": b"not a zip"}, 422, "Could not read"),
])
def test_errors(client, kwargs, status, fragment):
    r = _post(client, **kwargs)
    assert r.status_code == status and fragment in r.get_json()["error"]


@pytest.mark.parametrize("field", ["tempo", "slow_by", "default_tempo"])
@pytest.mark.parametrize("value", ["fast", "-5", "999"])
def test_bad_tempo(client, field, value):
    r = _post(client, **{field: value})
    assert r.status_code == 400 and field in r.get_json()["error"]


def test_missing_file(client):
    r = client.post("/convert", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
