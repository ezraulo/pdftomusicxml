import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXAMPLES = ROOT / "docs" / "examples"
SAMPLE_MUSICXML = EXAMPLES / "GeneralUser_Orchestral_Example_FULLSCORE.musicxml"
SAMPLE_MXL = EXAMPLES / "GeneralUser_Orchestral_Example_FULLSCORE.mxl"
SAMPLE_PDF = EXAMPLES / "GeneralUser_Orchestral_Example_FULLSCORE.pdf"
FAKE_AUDIVERIS = [sys.executable, str(ROOT / "tests" / "fake_audiveris.py")]
WEBMSCORE_HELPER = ROOT / "mscz" / "convert.cjs"
