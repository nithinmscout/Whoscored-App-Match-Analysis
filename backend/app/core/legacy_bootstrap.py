from pathlib import Path
import sys

LEGACY_ROOT = Path(__file__).resolve().parents[2] / "legacy"

if str(LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_ROOT))