import xml.etree.ElementTree as ET

import pytest

from conftest import SAMPLE_MUSICXML, SAMPLE_MXL
from violy_export import split_for_violy


def _parse(blob):
    return ET.fromstring(blob)


def test_one_file_per_part_all_named_p1():
    files = split_for_violy(SAMPLE_MUSICXML.read_bytes(), title="Sample", composer="Someone")
    assert len(files) == 20
    for name, blob in files.items():
        assert name.startswith("Sample-") and name.endswith(".musicxml")
        assert blob.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
        root = _parse(blob)
        assert [p.get("id") for p in root.iter("part")] == ["P1"]
        assert [sp.get("id") for sp in root.iter("score-part")] == ["P1"]
        ids = {e.get(a) for e in root.iter() for a in ("id", "idref") if e.get(a)}
        assert all(i == "P1" or i.startswith("P1-") for i in ids), ids
        assert len(root.find("part").findall("measure")) == 23
        assert root.findtext("work/work-title") == "Sample"
        assert root.findtext("identification/creator[@type='composer']") == "Someone"


def test_fourth_part_is_renamed_from_p4():
    # The P4 case is the one that crashed Violy's renderer.
    source = _parse(SAMPLE_MUSICXML.read_bytes())
    assert source.findall("part")[3].get("id") == "P4"
    fourth = sorted(split_for_violy(SAMPLE_MUSICXML.read_bytes()).items())[3][1]
    assert b'"P4' not in fourth


def test_mxl_and_musicxml_input_give_the_same_parts():
    assert split_for_violy(SAMPLE_MXL.read_bytes()).keys() == \
        split_for_violy(SAMPLE_MUSICXML.read_bytes()).keys()


def test_untitled_when_score_has_no_title():
    files = split_for_violy(SAMPLE_MUSICXML.read_bytes())
    assert all(n.startswith("Untitled-") for n in files)


def test_part_without_measures_is_refused():
    root = _parse(SAMPLE_MUSICXML.read_bytes())
    part = root.findall("part")[1]
    for m in part.findall("measure"):
        part.remove(m)
    with pytest.raises(ValueError, match="no measures"):
        split_for_violy(ET.tostring(root))


def test_timewise_score_is_refused():
    with pytest.raises(ValueError, match="score-partwise"):
        split_for_violy(b"<score-timewise/>")
