import xml.etree.ElementTree as ET

import pytest

from conftest import SAMPLE_MUSICXML, SAMPLE_MXL
from violy_export import _insert_at_measure_start, set_score_tempo, split_for_violy


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


def _with_tempo(blob, bpm, words=None, measure=0):
    """Add an "Allegro ♩ = bpm" style marking at the start of a measure of the top part."""
    root = _parse(blob)
    dt = '<direction-type><words>%s</words></direction-type>' % words if words else ''
    _insert_at_measure_start(root.find("part").findall("measure")[measure], ET.fromstring(
        '<direction>%s<direction-type><metronome><beat-unit>quarter</beat-unit>'
        '<per-minute>%g</per-minute></metronome></direction-type>'
        '<sound tempo="%g"/></direction>' % (dt, bpm, bpm)))
    return ET.tostring(root)


def _cello(files):
    return _parse(next(b for n, b in files.items() if "Violoncellos" in n))


def _sound_tempi(root):
    return [s.get("tempo") for s in root.iter("sound") if s.get("tempo")]


def _words(root):
    return [w.text for w in root.iter("words")]


def _metronomes(root):
    return [m.findtext("per-minute") for m in root.iter("metronome")]


ALLEGRO_THEN_150 = _with_tempo(_with_tempo(SAMPLE_MUSICXML.read_bytes(), 120, "Allegro"),
                               150, measure=10)


def test_score_tempo_is_copied_to_every_part():
    # MuseScore only marks the top part; split parts must not lose the tempo.
    files = split_for_violy(ALLEGRO_THEN_150)
    for blob in files.values():
        assert _sound_tempi(_parse(blob)) == ["120", "150"]
        tempo_marks = [d for d in _parse(blob).iter("direction")
                       if d.find("sound") is not None and d.find("sound").get("tempo")]
        assert all(d.find("staff") is None for d in tempo_marks)


def test_first_tempo_is_slowed_by_20_and_all_markings_stay():
    cello = _cello(split_for_violy(ALLEGRO_THEN_150, slow_by=20, unmarked_tempo=40))
    assert _sound_tempi(cello) == ["100", "150"]        # later change left as written
    assert _metronomes(cello) == ["120", "150"]         # original marks still shown
    assert _words(cello) == ["Practice tempo: 100 BPM (original 120 BPM)", "Allegro"]


def test_label_goes_just_before_the_first_tempo():
    first = _cello(split_for_violy(ALLEGRO_THEN_150, slow_by=20)).find("part/measure")
    directions = first.findall("direction")
    assert directions[0].findtext(".//words").startswith("Practice tempo")
    assert directions[0].find("sound") is None          # the label itself sets nothing
    assert directions[1].find("sound").get("tempo") == "100"


def test_slow_marked_tempo_never_drops_below_20():
    cello = _cello(split_for_violy(_with_tempo(SAMPLE_MUSICXML.read_bytes(), 30), slow_by=20))
    assert _sound_tempi(cello) == ["20"]


def test_unmarked_score_gets_the_default_tempo():
    cello = _cello(split_for_violy(SAMPLE_MUSICXML.read_bytes(), slow_by=20, unmarked_tempo=40))
    assert _sound_tempi(cello) == ["40"]
    assert _words(cello) == ["Practice tempo: 40 BPM"]


def test_slow_by_zero_keeps_the_marked_tempo():
    cello = _cello(split_for_violy(ALLEGRO_THEN_150, slow_by=0, unmarked_tempo=40))
    assert _sound_tempi(cello) == ["120", "150"]
    assert "Practice" not in " ".join(filter(None, _words(cello)))


def test_forced_tempo_changes_only_the_first_tempo():
    cello = _cello(split_for_violy(ALLEGRO_THEN_150, tempo=90, slow_by=20, unmarked_tempo=40))
    assert _sound_tempi(cello) == ["90", "150"]
    assert _metronomes(cello) == ["120", "150"]
    assert "Practice tempo: 90 BPM (original 120 BPM)" in _words(cello)


def test_forced_tempo_on_unmarked_score():
    cello = _cello(split_for_violy(SAMPLE_MUSICXML.read_bytes(), tempo=72))
    assert _sound_tempi(cello) == ["72"]


def test_no_tempo_settings_change_nothing():
    files = split_for_violy(SAMPLE_MUSICXML.read_bytes())
    assert all(_sound_tempi(_parse(b)) == [] for b in files.values())


@pytest.mark.parametrize("kwargs", [{"tempo": 5}, {"tempo": 1000}, {"slow_by": -1},
                                    {"unmarked_tempo": 0}])
def test_silly_tempo_is_refused(kwargs):
    with pytest.raises(ValueError, match="BPM"):
        split_for_violy(SAMPLE_MUSICXML.read_bytes(), **kwargs)


# Whole scores (set_score_tempo): the label goes once, in the top part.

def test_full_score_first_tempo_slowed_label_once():
    xml, changed = set_score_tempo(ALLEGRO_THEN_150, slow_by=20, unmarked_tempo=40)
    root = _parse(xml)
    assert changed and _sound_tempi(root) == ["100", "150"]
    assert sum("Practice tempo" in (w or "") for w in _words(root)) == 1
    assert len(root.findall("part")) == 20


def test_full_score_repeated_tempo_in_every_part_all_change():
    root = _parse(ALLEGRO_THEN_150)
    for part in root.findall("part")[1:]:
        _insert_at_measure_start(part.find("measure"), ET.fromstring('<sound tempo="120"/>'))
    xml, _ = set_score_tempo(ET.tostring(root), slow_by=20)
    assert _sound_tempi(_parse(xml)) == ["100", "150"] + ["100"] * 19


def test_full_score_unchanged_is_returned_as_is():
    src = _with_tempo(SAMPLE_MUSICXML.read_bytes(), 120)
    xml, changed = set_score_tempo(src, slow_by=0, unmarked_tempo=40)
    assert not changed and xml == src


def test_full_score_unmarked_gets_default():
    xml, changed = set_score_tempo(SAMPLE_MUSICXML.read_bytes(), slow_by=20, unmarked_tempo=40)
    assert changed and _sound_tempi(_parse(xml)) == ["40"]
