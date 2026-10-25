import os
import shutil

import pytest

import omr
from conftest import FAKE_AUDIVERIS


def _input(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def test_single_movement(tmp_path):
    r = omr.convert_score(_input(tmp_path, "score.pdf"), tmp_path / "out", FAKE_AUDIVERIS, 30)
    assert r.ok and [p.name for p in r.movements] == ["score.mxl"]
    assert r.excluded_sheets == []


def test_every_movement_is_returned_in_order(tmp_path):
    r = omr.convert_score(_input(tmp_path, "two-movements.pdf"), tmp_path / "out", FAKE_AUDIVERIS, 30)
    assert [p.name for p in r.movements] == ["two-movements.mvt1.mxl", "two-movements.mvt2.mxl"]


def test_broken_page_is_salvaged(tmp_path):
    r = omr.convert_score(_input(tmp_path, "broken-page.pdf"), tmp_path / "out", FAKE_AUDIVERIS, 30)
    assert r.ok and r.excluded_sheets == [2]


def test_rhythm_abort_is_classified(tmp_path):
    r = omr.convert_score(_input(tmp_path, "rhythm.pdf"), tmp_path / "out", FAKE_AUDIVERIS, 30)
    assert not r.ok and r.failure.kind == "rhythm-analysis-abort"
    assert "no correct rhythm" in r.log_tail


def test_timeout_kills_the_run(tmp_path):
    r = omr.convert_score(_input(tmp_path, "hang.pdf"), tmp_path / "out", FAKE_AUDIVERIS, 1)
    assert not r.ok and r.failure.kind == "timeout"


def test_missing_binary(tmp_path):
    r = omr.convert_score(_input(tmp_path, "score.pdf"), tmp_path / "out", ["/nonexistent/Audiveris"], 5)
    assert not r.ok and "spawn error" in r.log_tail


@pytest.mark.parametrize("log, expected", [
    ("WARN Sheet Bue_m'ani#2 flagged as invalid.", [2]),
    ("WARN [Book#3] Error processing stub", [3]),
    ("INFO Sheet #4 processed without error", []),
    ("INFO all good", []),
])
def test_broken_sheets_of(log, expected):
    assert omr.broken_sheets_of(log) == expected


def test_total_sheets_of():
    assert omr.total_sheets_of("Book X with 12 sheets") == 12
    assert omr.total_sheets_of("Stub#1 ... Stub#7 ... image #3") == 7
    assert omr.total_sheets_of("nothing here") is None
