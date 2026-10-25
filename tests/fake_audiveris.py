"""Stand-in for the Audiveris CLI, so the tests need no Java.

Accepts the arguments omr.py passes (-batch -export [-sheets N ...] -output DIR -- INPUT)
and behaves according to the input file's name:
  *two-movements*  exports <stem>.mvt1.mxl and <stem>.mvt2.mxl
  *broken-page*    3 sheets; sheet 2 is broken, so only a -sheets retry exports
  *rhythm*         fails the way Audiveris's rhythm analysis does
  *hang*           sleeps until killed
  anything else    exports <stem>.mxl
The exported score is the repo's sample (docs/examples/...FULLSCORE.mxl).
"""
import shutil
import sys
import time
from pathlib import Path

SAMPLE = Path(__file__).resolve().parent.parent / "docs/examples/GeneralUser_Orchestral_Example_FULLSCORE.mxl"


def main(argv):
    out = Path(argv[argv.index("-output") + 1])
    src = Path(argv[argv.index("--") + 1])
    sheets = []
    if "-sheets" in argv:
        i = argv.index("-sheets") + 1
        while argv[i].isdigit():
            sheets.append(int(argv[i]))
            i += 1
    book = out / src.stem
    book.mkdir(parents=True, exist_ok=True)
    name = src.name
    if "hang" in name:
        time.sleep(60)
    if "rhythm" in name:
        print("WARN [%s#1] no correct rhythm found" % src.stem)
        return 1
    if "broken-page" in name:
        print("INFO Book %s with 3 sheets" % src.stem)
        if not sheets:
            print("WARN Sheet %s#2 flagged as invalid." % src.stem)
            print("WARN Book export cancelled")
            return 0
        print("INFO exporting sheets %s" % sheets)
    if "two-movements" in name:
        for n in (1, 2):
            shutil.copy(SAMPLE, book / ("%s.mvt%d.mxl" % (src.stem, n)))
    else:
        shutil.copy(SAMPLE, book / (src.stem + ".mxl"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
