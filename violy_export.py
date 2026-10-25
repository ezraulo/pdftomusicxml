"""
violy_export.py - make MusicXML acceptable to Violy Creator Studio.

What Violy's upload form needs (found by testing, Oct 2026):
  * ONE MusicXML file per part (a multi-part score has to be split);
  * the part inside each file must be called "P1" (anything else, e.g. the
    "P4" MuseScore gives the fourth part of a score, crashes Violy's renderer);
  * real notes in the file (empty-part exports are rejected);
  * a work title and, ideally, a composer (the form fills them from the file);
  * uncompressed .musicxml / .xml (not .mxl).
Violy ALSO wants a MuseScore .mscz per part. That cannot be produced from
MusicXML without MuseScore itself, so it is out of scope here.

Usage (library):
    from violy_export import split_for_violy
    files = split_for_violy(open("score.musicxml", "rb").read(),
                            title="Beauty and the Beast", composer="Alan Menken")
    # -> {"Beauty_and_the_Beast-01-Violin.musicxml": b"...", ...}

Usage (CLI):
    python violy_export.py score.musicxml outdir [--title T] [--composer C] [--tempo BPM]
    python violy_export.py score.mxl outdir          # compressed input is fine
"""
import argparse
import io
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

VIOLY_PART_ID = "P1"
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


def _strip_tempo(part: ET.Element) -> None:
    for m in part.findall("measure"):
        for d in m.findall("direction"):
            if _is_tempo_direction(d):
                m.remove(d)
        for s in m.findall("sound"):          # measure-level <sound tempo=...>
            s.attrib.pop("tempo", None)


def _insert_at_measure_start(measure: ET.Element, elem: ET.Element) -> None:
    """After any <print>/<attributes> that open the measure, before the music."""
    i = 0
    for i, child in enumerate(list(measure)):
        if child.tag not in ("print", "attributes"):
            break
    else:
        i = len(measure)
    measure.insert(i, elem)


def _metronome_direction(bpm: float) -> ET.Element:
    shown = ("%g" % bpm)
    d = ET.Element("direction", {"placement": "above"})
    dt = ET.SubElement(d, "direction-type")
    met = ET.SubElement(dt, "metronome")
    ET.SubElement(met, "beat-unit").text = "quarter"
    ET.SubElement(met, "per-minute").text = shown
    ET.SubElement(d, "sound", {"tempo": shown})
    return d


def _apply_tempo(part: ET.Element, bpm=None, score_tempi=()) -> None:
    """Give a split part a tempo.

    bpm set: replace every tempo marking with one quarter-note tempo at the start.
    bpm unset: if the part has no tempo of its own, copy the score's tempo markings
    (MuseScore only writes them into the top part) to the start of the same measures.
    """
    measures = {m.get("number"): m for m in part.findall("measure")}
    if bpm:
        _strip_tempo(part)
        _insert_at_measure_start(part.find("measure"), _metronome_direction(bpm))
    elif score_tempi and not _tempo_directions(part):
        for number, direction in reversed(score_tempi):
            if number in measures:
                d = _deepcopy(direction)
                d.set("placement", "above")
                for s in d.findall("staff"):   # staff numbers belong to the source part
                    d.remove(s)
                _insert_at_measure_start(measures[number], d)


def split_for_violy(data: bytes, title: str = None, composer: str = None, tempo: float = None):
    """Return {filename: bytes}, one single-part MusicXML per part, part id P1.

    tempo: optional quarter-note BPM written into every part. Without it, tempo
    markings found in any part are copied to parts that have none.
    """
    if tempo is not None and not (10 <= tempo <= 400):
        raise ValueError("Tempo must be between 10 and 400 BPM.")
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
        _apply_tempo(new_part, tempo, score_tempi)
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
    ap.add_argument("--tempo", type=float, help="quarter-note BPM for every part")
    a = ap.parse_args(argv)
    import os
    os.makedirs(a.outdir, exist_ok=True)
    files = split_for_violy(open(a.input, "rb").read(), a.title, a.composer, a.tempo)
    for name, blob in files.items():
        with open(os.path.join(a.outdir, name), "wb") as f:
            f.write(blob)
        print("wrote", name, len(blob), "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
