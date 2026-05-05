"""Project-root conftest. Ensures `from src.X import Y` resolves under pytest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
