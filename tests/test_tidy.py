"""omr.tidy_score: merging a divisi passage Audiveris read as a separate part, and
showing staves it marked hidden per system."""
import xml.etree.ElementTree as ET

import omr

NOTE = ('<note><pitch><step>%s</step><octave>3</octave></pitch><duration>4</duration>'
        '<voice>%s</voice><type>whole</type>%s</note>')
REST = '<note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>'


def _measure(number, body, hidden=False):
    details = ('<attributes><staff-details print-object="%s"/></attributes>'
               % ("no" if hidden else "yes"))
    return '<measure number="%s">%s%s</measure>' % (number, details, body)


def _score(cello, divisi, cello_numbers=None):
    """cello / divisi: {bar number: body}; bars missing from the dict are rests."""
    numbers = cello_numbers or [str(n) for n in range(1, 9)]
    cello_part = "".join(_measure(n, cello.get(n, REST), hidden=n in divisi)
                         for n in numbers)
    divisi_part = "".join(_measure(str(n), divisi.get(str(n), REST), hidden=str(n) not in divisi)
                          for n in range(1, 9))
    return ('<score-partwise version="4.0"><part-list>'
            '<score-part id="P1"><part-name>Piano</part-name></score-part>'
            '<score-part id="P2"><part-name>Voice</part-name></score-part></part-list>'
            '<part id="P1">%s</part><part id="P2">%s</part></score-partwise>'
            % (divisi_part, cello_part)).encode()


TWO_STAVES = (NOTE % ("E", "1", "<staff>1</staff>") + '<backup><duration>4</duration></backup>'
              + NOTE % ("C", "5", "<staff>2</staff>"))
CELLO = {str(n): NOTE % ("D", "1", "") for n in (1, 2, 3, 4, 7, 8)}


def _parts(xml):
    return ET.fromstring(xml).findall("part")


def test_divisi_part_is_merged_into_the_cello_part_as_two_voices():
    xml, notes = omr.tidy_score(_score(CELLO, {"5": TWO_STAVES, "6": TWO_STAVES}))
    parts = _parts(xml)
    assert [p.get("id") for p in parts] == ["P2"]
    assert [sp.get("id") for sp in ET.fromstring(xml).iter("score-part")] == ["P2"]
    bar5 = parts[0].findall("measure")[4]
    assert [(n.findtext("pitch/step"), n.findtext("voice")) for n in bar5.iter("note")] == [
        ("E", "1"), ("C", "2")]
    assert bar5.find(".//staff") is None and bar5.find(".//rest") is None
    assert omr._pitched_measures(parts[0]) == {str(n) for n in range(1, 9)}
    assert "merged Piano (bars 5, 6) into Voice" in notes[0]


def test_bar_missing_from_the_cello_part_is_inserted_in_order():
    numbers = ["1", "2", "3", "4", "6", "7", "8"]            # Audiveris skipped bar 5
    xml, _ = omr.tidy_score(_score(CELLO, {"5": TWO_STAVES}, cello_numbers=numbers))
    assert [m.get("number") for m in _parts(xml)[0].findall("measure")] == [
        str(n) for n in range(1, 9)]


def test_parts_that_play_together_are_not_merged():
    xml, notes = omr.tidy_score(_score(CELLO, {"1": TWO_STAVES}))   # bar 1 overlaps
    assert len(_parts(xml)) == 2 and not any("merged" in n for n in notes)


def test_a_large_part_is_not_merged():
    duet = {str(n): TWO_STAVES for n in (5, 6, 7)}                  # 3 of 8 bars > 25%
    cello = {str(n): NOTE % ("D", "1", "") for n in (1, 2, 3, 4)}
    xml, _ = omr.tidy_score(_score(cello, duet))
    assert len(_parts(xml)) == 2


def test_hidden_staves_are_shown():
    xml, notes = omr.tidy_score(_score(CELLO, {"1": TWO_STAVES}))
    assert not [sd for sd in ET.fromstring(xml).iter("staff-details")
                if sd.get("print-object") == "no"]
    assert "showed staves Audiveris had hidden in some systems" in notes


def test_clean_score_is_returned_unchanged():
    from conftest import SAMPLE_MUSICXML
    data = SAMPLE_MUSICXML.read_bytes()
    assert omr.tidy_score(data) == (data, [])
