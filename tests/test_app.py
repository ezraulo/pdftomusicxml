import io
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pytest

import app as app_module
from conftest import FAKE_AUDIVERIS, MSCZ_HELPER

HAVE_MSCZ = shutil.which("node") is not None and (MSCZ_HELPER.parent / "node_modules" / "webmscore").is_dir()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUDIVERIS_CMD", " ".join(FAKE_AUDIVERIS))
    monkeypatch.setenv("OMR_TIMEOUT_S", "30")
    monkeypatch.delenv("MSCZ_CMD", raising=False)
    return app_module.app.test_client()


def _post(client, name="score.pdf", **form):
    data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), name), **form}
    return client.post("/convert", data=data, content_type="multipart/form-data")


def test_index_and_healthz(client):
    assert b"PDF to MusicXML" in client.get("/").data
    body = client.get("/healthz").get_json()
    assert body["status"] == "ok" and body["audiveris"] is True and body["mscz"] is False


def test_mxl_default(client):
    r = _post(client)
    assert r.status_code == 200
    assert r.headers["Content-Disposition"].endswith("score.mxl")
    assert r.data[:2] == b"PK"


def test_musicxml_is_uncompressed(client):
    r = _post(client, format="musicxml")
    assert r.status_code == 200 and r.headers["Content-Disposition"].endswith("score.musicxml")
    assert ET.fromstring(r.data).tag == "score-partwise"


def test_multiple_movements_come_back_as_zip(client):
    r = _post(client, "two-movements.pdf", format="musicxml")
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert names == ["two-movements.mvt1.musicxml", "two-movements.mvt2.musicxml"]


def test_violy_zip_without_mscz(client):
    r = _post(client, format="violy", title="Sample Piece", composer="A. Composer")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    parts = [n for n in z.namelist() if n.endswith(".musicxml")]
    assert len(parts) == 20 and parts[0] == "Sample_Piece-01-Piccolo.musicxml"
    readme = z.read("READ-ME-violy.txt").decode()
    assert "'Sample Piece'" in readme and "Violy's lists" in readme
    assert "No .mscz files were made" in readme and "MSCZ_CMD is not set" in readme
    root = ET.fromstring(z.read(parts[0]))
    assert root.find("part").get("id") == "P1"


def test_violy_with_movements_uses_folders(client):
    r = _post(client, "two-movements.pdf", format="violy", mscz="0")
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert "movement-1/Untitled-01-Piccolo.musicxml" in names
    assert "movement-2/Untitled-20-Contrabasses.musicxml" in names


def test_excluded_pages_are_reported(client):
    r = _post(client, "broken-page.pdf", format="violy", mscz="0")
    assert r.headers["X-Excluded-Sheets"] == "2"
    readme = zipfile.ZipFile(io.BytesIO(r.data)).read("READ-ME-violy.txt").decode()
    assert "page(s) 2" in readme


@pytest.mark.skipif(not HAVE_MSCZ, reason="needs node and `npm ci` in mscz/")
def test_violy_zip_with_mscz(client, monkeypatch):
    monkeypatch.setenv("MSCZ_CMD", "node %s" % MSCZ_HELPER)
    r = _post(client, format="violy")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    mscz = sorted(n for n in z.namelist() if n.endswith(".mscz"))
    assert len(mscz) == 20 and mscz[0] == "Untitled-01-Piccolo.mscz"
    inner = zipfile.ZipFile(io.BytesIO(z.read(mscz[0])))
    assert any(n.endswith(".mscx") for n in inner.namelist())
    assert "NOT yet confirmed" in z.read("READ-ME-violy.txt").decode()


@pytest.mark.parametrize("kwargs, status, fragment", [
    ({"name": "score.docx"}, 400, "Unsupported file type"),
    ({"format": "midi"}, 400, "format must be"),
    ({"name": "rhythm.pdf"}, 422, "rhythm analysis"),
])
def test_errors(client, kwargs, status, fragment):
    r = _post(client, **kwargs)
    assert r.status_code == status and fragment in r.get_json()["error"]


def test_missing_file(client):
    r = client.post("/convert", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_violy_tempo(client):
    r = _post(client, format="violy", mscz="0", tempo="66")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    root = ET.fromstring(z.read("Untitled-19-Violoncellos.musicxml"))
    assert root.find(".//sound").get("tempo") == "66"
    assert "66 BPM" in z.read("READ-ME-violy.txt").decode()


@pytest.mark.parametrize("tempo", ["fast", "0", "999"])
def test_violy_bad_tempo(client, tempo):
    r = _post(client, format="violy", tempo=tempo)
    assert r.status_code == 400 and "tempo" in r.get_json()["error"]
