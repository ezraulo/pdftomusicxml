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
