"""
mscz_export.py - write MuseScore .mscz files from MusicXML via webmscore.

webmscore (MuseScore 4 compiled to WebAssembly) runs in a Node helper,
mscz/to_mscz.cjs. Set MSCZ_CMD to the command that runs it, e.g.
"node /opt/mscz/to_mscz.cjs" (the Docker image does this). Without MSCZ_CMD,
.mscz export is reported as unavailable instead of failing the request.

Caveat: Violy has not yet been shown to accept these files. They are MuseScore
4.00-format files written by webmscore 1.2.1, not by the MuseScore app.
"""
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

TIMEOUT_S = 300


def mscz_command() -> Optional[List[str]]:
    cmd = os.environ.get("MSCZ_CMD", "").strip()
    return shlex.split(cmd) if cmd else None


def to_mscz(files: Dict[str, bytes]) -> Tuple[Dict[str, bytes], List[str]]:
    """{name.musicxml: bytes} -> ({name.mscz: bytes}, [error messages])."""
    command = mscz_command()
    if not command:
        return {}, [".mscz export is not configured (MSCZ_CMD is not set)."]
    out, errors = {}, []
    with tempfile.TemporaryDirectory(prefix="mscz-") as tmp:
        tmp = Path(tmp)
        pairs, names = [], {}
        for i, (name, blob) in enumerate(files.items()):
            src, dst = tmp / ("%03d.musicxml" % i), tmp / ("%03d.mscz" % i)
            src.write_bytes(blob)
            pairs += [str(src), str(dst)]
            names[str(dst)] = Path(name).with_suffix(".mscz").as_posix()
        try:
            proc = subprocess.run([*command, *pairs], capture_output=True,
                                  timeout=TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {}, [".mscz export failed: %s" % e]
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            try:
                result = json.loads(line)
            except ValueError:
                continue
            name = names.get(result.get("output"))
            if name is None:
                continue
            if result.get("ok"):
                out[name] = Path(result["output"]).read_bytes()
            else:
                errors.append("%s: %s" % (name, result.get("error", "unknown error")))
        missing = [n for n in names.values() if n not in out
                   and not any(e.startswith(n + ":") for e in errors)]
        if missing:
            detail = proc.stderr.decode("utf-8", "replace").strip()[-500:]
            errors += ["%s: not written (%s)" % (n, detail or "exit %d" % proc.returncode)
                       for n in missing]
    return out, errors
