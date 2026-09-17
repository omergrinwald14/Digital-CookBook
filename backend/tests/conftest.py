"""Make `import app.*` work when pytest is run from the backend/ directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
