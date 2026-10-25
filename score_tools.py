"""
score_tools.py - read and write MuseScore, MusicXML, MIDI and PDF with webmscore.

webmscore (MuseScore 4 compiled to WebAssembly) runs in a Node helper,
mscz/convert.cjs. Set WEBMSCORE_CMD to the command that runs it, e.g.
"node /opt/mscz/convert.cjs" (the Docker image does this). Without it, these
conversions are reported as unavailable instead of failing the request.
"""
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

TIMEOUT_S = 300
SUFFIXES = {"mscz": ".mscz", "musicxml": ".musicxml", "midi": ".mid", "pdf": ".pdf"}
NOT_CONFIGURED = "webmscore is not configured (WEBMSCORE_CMD is not set)."


def command() -> Optional[List[str]]:
    cmd = os.environ.get("WEBMSCORE_CMD", "").strip()
    return shlex.split(cmd) if cmd else None


def convert(items: Dict[str, Tuple[bytes, str]], kinds: Sequence[str]):
    """{name: (score bytes, webmscore input format)} -> ({output name: bytes}, [errors]).

    Each item is written as every kind in `kinds` (mscz, musicxml, midi, pdf), named
    after the item with the kind's suffix: "Song.musicxml" -> "Song.pdf"."""
    cmd = command()
    if not cmd:
        return {}, [NOT_CONFIGURED]
    out, errors = {}, []
    with tempfile.TemporaryDirectory(prefix="webmscore-") as tmp:
        tmp = Path(tmp)
        jobs, targets = [], {}
        for i, (name, (blob, fmt)) in enumerate(items.items()):
            src = tmp / ("%03d.%s" % (i, fmt))
            src.write_bytes(blob)
            outputs = {}
            for kind in kinds:
                path = tmp / ("%03d-out%s" % (i, SUFFIXES[kind]))
                outputs[kind] = str(path)
                targets[(str(src), kind)] = (path, Path(name).with_suffix(SUFFIXES[kind]).as_posix())
            jobs.append({"input": str(src), "format": fmt, "outputs": outputs})
        jobs_file = tmp / "jobs.json"
        jobs_file.write_text(json.dumps(jobs))
        try:
            proc = subprocess.run([*cmd, str(jobs_file)], capture_output=True,
                                  timeout=TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {}, ["webmscore failed: %s" % e]
        reported = set()
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            try:
                report = json.loads(line)
            except ValueError:
                continue
            src = report.get("input")
            reported.add(src)
            for kind in report.get("written", []):
                path, name = targets[(src, kind)]
                out[name] = path.read_bytes()
            for kind, message in report.get("errors", {}).items():
                label = targets[(src, kind)][1] if (src, kind) in targets else \
                    "%s (could not be read)" % _item_name(items, src)
                errors.append("%s: %s" % (label, message))
        for job in jobs:
            if job["input"] not in reported:
                detail = proc.stderr.decode("utf-8", "replace").strip()[-500:]
                errors.append("%s: not converted (%s)" % (
                    _item_name(items, job["input"]), detail or "exit %d" % proc.returncode))
    return out, errors


def _item_name(items, src):
    return list(items)[int(Path(src).stem)]        # inputs are written as 000.<fmt>, 001...


def to_mscz(files: Dict[str, bytes]):
    """{name.musicxml: bytes} -> ({name.mscz: bytes}, [errors])."""
    return convert({n: (b, "musicxml") for n, b in files.items()}, ["mscz"])


def to_musicxml(name: str, data: bytes, fmt: str) -> bytes:
    """Read a MuseScore file (fmt "mscz" or "mscx") and return its MusicXML."""
    out, errors = convert({name: (data, fmt)}, ["musicxml"])
    if not out:
        raise ValueError("; ".join(errors) or "webmscore could not read the file.")
    return next(iter(out.values()))
