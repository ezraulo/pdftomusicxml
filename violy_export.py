"""
violy_export.py - make MusicXML acceptable to Violy Creator Studio.

What Violy's upload form needs (found by testing, Oct 2026):
  * ONE MusicXML file per part (a multi-part score has to be split);
  * the part inside each file must be called "P1" (anything else, e.g. the
    "P4" MuseScore gives the fourth part of a score, crashes Violy's renderer);
  * real notes in the file (empty-part exports are rejected);
  * a work title and, ideally, a composer (the form fills them from the file);
  * uncompressed .musicxml / .xml (not .mxl).
Violy ALSO wants a MuseScore .mscz per part; the app writes those with webmscore.
Violy lets players speed a piece up but not slow it down, so the first tempo can be
set to a slower, labelled practice tempo (set_score_tempo() does the same for a
whole score).

Usage (library):
    from violy_export import split_for_violy
    files = split_for_violy(open("score.musicxml", "rb").read(),
                            title="Beauty and the Beast", composer="Alan Menken")
    # -> {"Beauty_and_the_Beast-01-Violin.musicxml": b"...", ...}

Usage (CLI):
    python violy_export.py score.musicxml outdir [--title T] [--composer C]
                           [--tempo BPM | --slow-by 20 --unmarked-tempo 40]
    python violy_export.py score.mxl outdir          # compressed input is fine
"""
import argparse
import io
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

VIOLY_PART_ID = "P1"
MIN_PRACTICE_TEMPO = 20
_DOCTYPE = ('<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 '
            'Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">')


def _read_xml_bytes(data: bytes) -> bytes:
    """Accept raw MusicXML or a compressed .mxl container."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            root_name = None
            if "META-INF/container.xml" in z.namelist():
                c = ET.fromstring(z.read("META-INF/container.xml"))
                rf = c.find(".//rootfile")
                if rf is not None:
                    root_name = rf.get("full-path")
            if not root_name:
                root_name = next(n for n in z.namelist()
                                 if n.lower().endswith((".xml", ".musicxml"))
                                 and not n.startswith("META-INF"))
            return z.read(root_name)
    return data


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_") or "part"


def _rename_ids(elem: ET.Element, old: str, new: str) -> None:
    """Rename old -> new in id/idref attributes, including 'old-I1' style ids."""
    for e in elem.iter():
        for attr in ("id", "idref"):
            v = e.get(attr)
            if v == old:
                e.set(attr, new)
            elif v and v.startswith(old + "-"):
                e.set(attr, new + v[len(old):])


def _text(e):
    return (e.text or "").strip() if e is not None else ""


def _is_tempo_direction(d: ET.Element) -> bool:
    s = d.find("sound")
    return (s is not None and s.get("tempo")) or d.find("./direction-type/metronome") is not None


def _tempo_directions(part: ET.Element):
    """[(measure number, <direction>)] for every tempo marking in a part."""
    return [(m.get("number"), d) for m in part.findall("measure")
            for d in m.findall("direction") if _is_tempo_direction(d)]


def _insert_at_measure_start(measure: ET.Element, elem: ET.Element) -> None:
    """After any <print>/<attributes> that open the measure, before the music."""
    i = 0
    for i, child in enumerate(list(measure)):
        if child.tag not in ("print", "attributes"):
            break
    else:
        i = len(measure)
    measure.insert(i, elem)


def _words_direction(text: str, bpm: float = None) -> ET.Element:
    """A bold text marking, optionally also setting the playback tempo. (A metronome
    mark is never added next to the words: MuseScore drops one that shares a
    direction with words.)"""
    d = ET.Element("direction", {"placement": "above"})
    ET.SubElement(ET.SubElement(d, "direction-type"), "words", {"font-weight": "bold"}).text = text
    if bpm:
        ET.SubElement(d, "sound", {"tempo": "%g" % round(bpm)})
    return d


def _tempo_sounds(part: ET.Element):
    """[(measure index, measure, <sound>)] for every playback tempo in a part."""
    return [(i, m, s) for i, m in enumerate(part.findall("measure"))
            for s in m.iter("sound") if s.get("tempo")]


def _set_first_tempo(parts, tempo=None, slow_by=None, unmarked_tempo=None) -> bool:
    """Give the score a slower practice tempo, because practice apps such as Violy let
    players speed a piece up but (without a paid plan) not slow it down.

    Only the FIRST tempo changes; every original marking stays on the page, and
    later tempo changes are left as written. The first tempo plays at `tempo` if
    given, else `slow_by` BPM slower (never below MIN_PRACTICE_TEMPO), and a label
    "Practice tempo: 100 BPM (original 120 BPM)" goes above it in the top part. A
    score with no tempo gets `tempo` or `unmarked_tempo`, labelled "Practice tempo:
    40 BPM". Returns whether anything changed."""
    found = [(i, n, m, s) for n, p in enumerate(parts) for i, m, s in _tempo_sounds(p)]
    if not found:
        target = tempo or unmarked_tempo
        if not target:
            return False
        _insert_at_measure_start(parts[0].find("measure"), _words_direction(
            "Practice tempo: %g BPM" % round(target), target))
        return True
    first_index = min(i for i, _, _, _ in found)
    original = float(next(s for i, _, _, s in sorted(found, key=lambda f: f[:2])
                          if i == first_index).get("tempo"))
    if tempo:
        target = round(tempo)
    elif slow_by:
        target = max(MIN_PRACTICE_TEMPO, round(original - slow_by))
    else:
        return False
    if target == round(original):
        return False
    # The same first tempo may be repeated in several parts; change every copy.
    for i, _, _, s in found:
        if i == first_index and float(s.get("tempo")) == original:
            s.set("tempo", "%g" % target)
    label = _words_direction("Practice tempo: %g BPM (original %g BPM)" % (target, round(original)))
    measure = parts[0].findall("measure")[first_index]
    tempo_direction = next((d for d in measure.findall("direction")
                            if d.find("sound") is not None and d.find("sound").get("tempo")), None)
    if tempo_direction is not None:
        measure.insert(list(measure).index(tempo_direction), label)
    else:
        _insert_at_measure_start(measure, label)
    return True


def _apply_tempo(part: ET.Element, score_tempi=(), tempo=None, slow_by=None,
                 unmarked_tempo=None) -> bool:
    """Give a split part its tempo: if the part has no tempo of its own, copy the
    score's tempo markings (MuseScore only writes them into the top part) to the start
    of the same measures, then set the practice tempo (see _set_first_tempo)."""
    measures = {m.get("number"): m for m in part.findall("measure")}
    if score_tempi and not _tempo_directions(part):
        for number, direction in reversed(score_tempi):
            if number in measures:
                d = _deepcopy(direction)
                d.set("placement", "above")
                for s in d.findall("staff"):   # staff numbers belong to the source part
                    d.remove(s)
                _insert_at_measure_start(measures[number], d)
    return _set_first_tempo([part], tempo, slow_by, unmarked_tempo)


def _check_tempo_args(tempo, slow_by, unmarked_tempo):
    for name, value, low in (("Tempo", tempo, 10), ("Default tempo", unmarked_tempo, 10),
                             ("Slow-down", slow_by, 0)):
        if value is not None and not (low <= value <= 400):
            raise ValueError("%s must be between %d and 400 BPM." % (name, low))


def set_score_tempo(data: bytes, tempo: float = None, slow_by: float = None,
                    unmarked_tempo: float = None):
    """Apply the practice-tempo rule (see _set_first_tempo) to a whole score: only the
    first tempo changes, and the label goes in the top part. Returns (MusicXML bytes,
    changed); the bytes are re-serialised only when something changed."""
    _check_tempo_args(tempo, slow_by, unmarked_tempo)
    xml = _read_xml_bytes(data)
    root = ET.fromstring(xml)
    if root.tag != "score-partwise":
        raise ValueError("Only score-partwise MusicXML is supported "
                         "(root element was <%s>)." % root.tag)
    parts = root.findall("part")
    if not parts:
        raise ValueError("No <part> elements found.")
    changed = _set_first_tempo(parts, tempo, slow_by, unmarked_tempo)
    return (_serialise(root) if changed else xml), changed


def split_for_violy(data: bytes, title: str = None, composer: str = None,
                    tempo: float = None, slow_by: float = None, unmarked_tempo: float = None):
    """Return {filename: bytes}, one single-part MusicXML per part, part id P1.

    Tempos are quarter-note BPM. Tempo markings found in any part are first copied
    to parts that have none (MuseScore and Audiveris only mark the top part).
    Then only the first tempo is changed (see _set_first_tempo); every original
    marking stays.
    tempo: play the first tempo at exactly this (overrides the other two).
    slow_by: play the first tempo this much slower than marked.
    unmarked_tempo: practice tempo for a part with no tempo marking.
    """
    _check_tempo_args(tempo, slow_by, unmarked_tempo)
    root = ET.fromstring(_read_xml_bytes(data))
    if root.tag != "score-partwise":
        raise ValueError("Only score-partwise MusicXML is supported "
                         "(root element was <%s>)." % root.tag)

    work_title = _text(root.find("./work/work-title")) or _text(root.find("movement-title"))
    title = title or work_title or "Untitled"
    existing_composer = ""
    for c in root.findall("./identification/creator"):
        if c.get("type") == "composer":
            existing_composer = _text(c)
    composer = composer or existing_composer

    part_list = root.find("part-list")
    score_parts = {sp.get("id"): sp for sp in part_list.findall("score-part")}
    parts = root.findall("part")
    if not parts:
        raise ValueError("No <part> elements found.")

    score_tempi = next((t for t in map(_tempo_directions, parts) if t), [])

    out = {}
    for index, part in enumerate(parts, start=1):
        pid = part.get("id")
        if len(part.findall("measure")) == 0:
            raise ValueError("Part %s has no measures - Violy rejects empty parts." % pid)
        if not any(n.find("pitch") is not None or n.find("rest") is not None
                   for n in part.iter("note")):
            raise ValueError("Part %s contains no notes or rests." % pid)

        sp = score_parts[pid]
        part_name = _text(sp.find("part-name")) or "Part %d" % index

        # Build a fresh single-part score.
        new_root = ET.Element("score-partwise", {"version": "3.1"})
        work = ET.SubElement(new_root, "work")
        ET.SubElement(work, "work-title").text = title
        ident = ET.SubElement(new_root, "identification")
        if composer:
            ET.SubElement(ident, "creator", {"type": "composer"}).text = composer
        enc = ET.SubElement(ident, "encoding")
        ET.SubElement(enc, "software").text = "violy_export.py"
        # keep page/scale defaults and credits from the source if present
        for tag in ("defaults",):
            src = root.find(tag)
            if src is not None:
                new_root.append(_deepcopy(src))
        pl = ET.SubElement(new_root, "part-list")
        pl.append(_deepcopy(sp))
        new_part = _deepcopy(part)
        _apply_tempo(new_part, score_tempi, tempo, slow_by, unmarked_tempo)
        new_root.append(new_part)

        # Violy needs the part to be called P1.
        if pid != VIOLY_PART_ID:
            _rename_ids(new_root, pid, VIOLY_PART_ID)

        fname = "%s-%02d-%s.musicxml" % (_slug(title), index, _slug(part_name))
        out[fname] = _serialise(new_root)
    return out


def _deepcopy(e):
    import copy
    return copy.deepcopy(e)


def _serialise(root: ET.Element) -> bytes:
    try:
        ET.indent(root, space="  ")          # Python 3.9+
    except AttributeError:
        pass
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n%s\n%s\n' % (_DOCTYPE, body)).encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("input")
    ap.add_argument("outdir")
    ap.add_argument("--title")
    ap.add_argument("--composer")
    ap.add_argument("--tempo", type=float, help="play the first tempo at exactly this quarter-note BPM")
    ap.add_argument("--slow-by", type=float,
                    help="play the first tempo this many BPM slower (practice tempo)")
    ap.add_argument("--unmarked-tempo", type=float,
                    help="practice tempo for parts with no tempo marking")
    a = ap.parse_args(argv)
    import os
    os.makedirs(a.outdir, exist_ok=True)
    files = split_for_violy(open(a.input, "rb").read(), a.title, a.composer,
                            a.tempo, a.slow_by, a.unmarked_tempo)
    for name, blob in files.items():
        with open(os.path.join(a.outdir, name), "wb") as f:
            f.write(blob)
        print("wrote", name, len(blob), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
