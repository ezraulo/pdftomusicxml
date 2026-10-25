"""
omr.py - run Audiveris on a PDF or image and collect the MusicXML it produced.

Ported from solfascribe-omr's src/audiveris.ts (MIT, see NOTICE.md), which learned
these rules from running a 29-score corpus through Audiveris 5.10.2:
  * A book may export several MOVEMENT files (<name>.mvt1.mxl, ...) when Audiveris
    cannot bridge a page break. All of them are the result, never just the first.
  * Audiveris refuses to export a whole book when one sheet fails. Retrying with
    -sheets set to the healthy pages salvages the rest ("excluded sheets").
  * Some scores abort in Audiveris's own rhythm analysis ("no correct rhythm").
    No retry helps, so that failure gets its own class.
"""
import os
import re
import signal
import subprocess
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

LOG_TAIL_CHARS = 4000
# The sheet total is parsed from log text; cap it so it cannot size a huge list.
MAX_SALVAGE_SHEETS = 2000
MUSICXML_SUFFIXES = (".mxl", ".musicxml", ".xml")


@dataclass
class OmrFailure:
    # rhythm-analysis-abort | unreadable-input | timeout | omr-failed
    kind: str
    detail: str


@dataclass
class OmrResult:
    movements: List[Path] = field(default_factory=list)
    excluded_sheets: List[int] = field(default_factory=list)
    failure: Optional[OmrFailure] = None
    log_tail: str = ""
    # Filled in by the caller after tidy_score(): what was fixed in the output.
    tidy_notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.movements)


def _run(command: Sequence[str], args: Sequence[str], timeout_s: float):
    """Run one Audiveris invocation; return (combined log, timed_out)."""
    try:
        proc = subprocess.Popen(
            [*command, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # Own process group: the Audiveris launcher is a shell script that starts
            # java, so on timeout the whole group has to be killed, not just the shell.
            start_new_session=True,
        )
    except OSError as e:
        return "[spawn error] %s" % e, False
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        return out.decode("utf-8", "replace"), False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return out.decode("utf-8", "replace"), True


def _natural_key(path: Path):
    # mvt2 before mvt10
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(path))]


def collect_movements(directory: Path) -> List[Path]:
    """Every MusicXML file under directory (Audiveris nests output in a book folder)."""
    if not directory.is_dir():
        return []
    found = [p for p in directory.rglob("*")
             if p.is_file() and p.suffix.lower() in MUSICXML_SUFFIXES
             and "META-INF" not in p.parts]
    return sorted(found, key=_natural_key)


def broken_sheets_of(log: str) -> List[int]:
    """The 1-based sheets the log reports as broken.

    Audiveris 5.10.2 puts the sheet number in the book tag, e.g.
    "Sheet Name#2 flagged as invalid." or "WARN [Name#2] ... Error processing stub".
    Negated phrasings ("without error") don't count: excluding a healthy page would
    silently shrink the salvaged book.
    """
    sheets = set()
    for line in log.splitlines():
        if re.search(r"without error|no error", line, re.I):
            continue
        reports_breakage = (
            re.search(r"flagged as invalid|error processing stub", line, re.I)
            or (re.search(r"\b(?:invalid|error)\b", line, re.I) and re.search(r"sheet", line, re.I))
        )
        if not reports_breakage:
            continue
        m = re.search(r"#(\d+)", line) or re.search(r"sheet\s+(\d+)", line, re.I)
        if m:
            sheets.add(int(m.group(1)))
    return sorted(sheets)


def total_sheets_of(log: str) -> Optional[int]:
    """The book's sheet count: a stated total if the log gives one, else the highest
    stub/sheet/image ordinal mentioned."""
    m = re.search(r"(\d+)\s+sheets?\b", log, re.I) or re.search(r"sheets?\s*[:=]\s*(\d+)", log, re.I)
    if m:
        return int(m.group(1))
    ordinals = [int(n) for n in re.findall(r"(?:stub|sheet|image)\s*#(\d+)", log, re.I)]
    return max(ordinals) if ordinals else None


def classify_failure(log: str, timed_out: bool) -> OmrFailure:
    if timed_out:
        return OmrFailure("timeout", "The conversion took longer than the time limit.")
    if re.search(r"no correct rhythm|voice excess", log, re.I):
        return OmrFailure(
            "rhythm-analysis-abort",
            "Audiveris's rhythm analysis gave up on this score. Retrying will not help; "
            "a cleaner scan or a different edition might.")
    if re.search(r"could not load|cannot read|unsupported|not a valid|no such file", log, re.I):
        return OmrFailure("unreadable-input", "The file could not be read as a score.")
    return OmrFailure("omr-failed", "Audiveris produced no output. See the log tail.")


def _run_once(input_path: Path, output_dir: Path, command, timeout_s, sheets=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    # One argv entry per sheet number, as Audiveris's int-array option expects.
    sheet_args = ["-sheets", *map(str, sheets)] if sheets else []
    log, timed_out = _run(
        command,
        ["-batch", "-export", *sheet_args, "-output", str(output_dir), "--", str(input_path)],
        timeout_s)
    return log, timed_out, collect_movements(output_dir)


def convert_score(input_path: Path, output_dir: Path, command: Sequence[str],
                  timeout_s: float) -> OmrResult:
    """A full pass first; if nothing was exported because some sheets are broken,
    one retry on the remaining sheets."""
    log, timed_out, movements = _run_once(input_path, output_dir, command, timeout_s)
    if movements:
        return OmrResult(movements=movements, log_tail=log[-LOG_TAIL_CHARS:])

    broken = broken_sheets_of(log)
    total = total_sheets_of(log)
    if not timed_out and broken and total is not None and total <= MAX_SALVAGE_SHEETS:
        healthy = [n for n in range(1, total + 1) if n not in broken]
        if healthy:
            log2, timed_out2, movements2 = _run_once(
                input_path, output_dir, command, timeout_s, healthy)
            combined = "%s\n--- retry without sheets %s ---\n%s" % (
                log, ", ".join(map(str, broken)), log2)
            if movements2:
                return OmrResult(movements=movements2, excluded_sheets=broken,
                                 log_tail=combined[-LOG_TAIL_CHARS:])
            return OmrResult(failure=classify_failure(combined, timed_out2),
                             log_tail=combined[-LOG_TAIL_CHARS:])

    return OmrResult(failure=classify_failure(log, timed_out), log_tail=log[-LOG_TAIL_CHARS:])


# --- Tidying Audiveris output ------------------------------------------------------
#
# Audiveris sometimes reads a short divisi passage (two staves for one instrument) as a
# separate part that plays only where the real part rests, and marks each part hidden
# in the systems where it is "absent" (<staff-details print-object="no">). MuseScore
# cannot hide a part per system, so it hides that part everywhere: a whole page of a
# cello part came out as empty bars. tidy_score() undoes both.

# Only merge a part this small (share of measures with notes) into another.
MAX_DONOR_SHARE = 0.25


def _has_notes(measure) -> bool:
    return any(n.find("pitch") is not None or n.find("unpitched") is not None
               for n in measure.iter("note"))


def _pitched_measures(part) -> set:
    """Numbers of the measures that hold notes (not just rests)."""
    return {m.get("number") for m in part.findall("measure") if _has_notes(m)}


def _hidden_measures(part) -> set:
    """Numbers of the measures where Audiveris marks the part absent from the system
    (<staff-details print-object="no">); the marking holds until the next one."""
    hidden, state = set(), False
    for m in part.findall("measure"):
        marks = [sd.get("print-object") for sd in m.iter("staff-details") if sd.get("print-object")]
        if marks:
            state = "no" in marks
        if state:
            hidden.add(m.get("number"))
    return hidden


def _staff_count(part) -> int:
    declared = [int(s.text) for s in part.iter("staves") if (s.text or "").strip().isdigit()]
    used = [int(s.text) for s in part.iter("staff") if (s.text or "").strip().isdigit()]
    return max(declared + used + [1])


# Names Audiveris gives parts it could not read a name for.
GENERIC_PART_NAMES = {"", "piano", "voice"}


def _divisions(part):
    return {d.text.strip() for d in part.iter("divisions") if d.text}


def _merge_part_into(receiver, donor) -> None:
    """Copy donor's notes into receiver's measures (matched by measure number) where
    donor has notes and receiver rests: donor staves become voices of receiver's
    staff. A donor measure with notes that receiver lacks is inserted whole."""
    voice_map = {}
    by_number = {m.get("number"): m for m in receiver.findall("measure")}
    previous = None
    for dm in donor.findall("measure"):
        number = dm.get("number")
        rm = by_number.get(number)
        if not _has_notes(dm):
            previous = rm if rm is not None else previous
            continue
        if rm is None:                       # missing in receiver: insert an empty one
            rm = deepcopy(dm)
            for child in list(rm):
                rm.remove(child)
            index = list(receiver).index(previous) + 1 if previous is not None else 0
            receiver.insert(index, rm)
        for child in list(rm):
            if child.tag in ("note", "backup", "forward"):
                rm.remove(child)
        barline = next((i for i, c in enumerate(rm)
                        if c.tag == "barline" and c.get("location") == "right"), len(rm))
        copies = []
        for child in dm:
            if child.tag not in ("note", "backup", "forward", "direction"):
                continue
            c = deepcopy(child)
            staff = c.find("staff")
            if staff is not None:
                c.remove(staff)
            voice = c.find("voice")
            if voice is not None:
                voice.text = voice_map.setdefault(voice.text, str(len(voice_map) + 1))
            copies.append(c)
        rm[barline:barline] = copies
        previous = rm


def _bar_key(number):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", number or "")]


def tidy_score(xml: bytes):
    """Fix the two Audiveris quirks above. Returns (MusicXML bytes, list of notes on
    what was changed); the bytes are unchanged when nothing needed fixing."""
    root = ET.fromstring(xml)
    notes = []
    changed = True
    while changed:
        changed = False
        parts = root.findall("part")
        counts = [len(p.findall("measure")) for p in parts]
        pitched = [_pitched_measures(p) for p in parts]
        hidden = [_hidden_measures(p) for p in parts]
        numbers = [{m.get("number") for m in p.findall("measure")} for p in parts]
        staves = [_staff_count(p) for p in parts]
        part_names = {sp.get("id"): (sp.findtext("part-name") or "").strip()
                      for sp in root.find("part-list").findall("score-part")}
        names = [part_names.get(p.get("id"), "") for p in parts]
        for d, donor in enumerate(parts):
            for r, receiver in enumerate(parts):
                # Merge only the Audiveris pattern: the receiver is absent from the
                # systems where the donor plays, and the donor is absent elsewhere.
                # In a full score that hides empty staves, a sparse instrument (a
                # timpani) looks like that too; the staff and name checks keep it.
                if (r == d or not pitched[d] or not pitched[r]
                        or pitched[d] & pitched[r]
                        or not pitched[d] <= hidden[r]
                        or not pitched[r] & numbers[d] <= hidden[d]
                        or len(pitched[d]) > MAX_DONOR_SHARE * counts[d]
                        or len(pitched[d]) >= len(pitched[r])
                        # a divisi read as its own part has more staves than the
                        # real one and no name of its own
                        or staves[d] <= staves[r]
                        or not (names[d].lower() in GENERIC_PART_NAMES
                                or names[d] == names[r])
                        or _divisions(donor) != _divisions(receiver)):
                    continue
                _merge_part_into(receiver, donor)
                part_list = root.find("part-list")
                for sp in part_list.findall("score-part"):
                    if sp.get("id") == donor.get("id"):
                        part_list.remove(sp)
                root.remove(donor)
                notes.append("merged %s (bars %s) into %s: it only played where %s rested"
                             " (likely a divisi passage)" % (
                                 names[d] or donor.get("id"),
                                 ", ".join(sorted(pitched[d], key=_bar_key)),
                                 names[r] or receiver.get("id"), names[r] or receiver.get("id")))
                changed = True
                break
            if changed:
                break
    hidden = [sd for sd in root.iter("staff-details") if sd.get("print-object") == "no"]
    for sd in hidden:
        del sd.attrib["print-object"]
    if hidden:
        notes.append("showed staves Audiveris had hidden in some systems")
    if not notes:
        return xml, notes
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), notes
