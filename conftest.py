"""Lets pytest import the package from src/ without installing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
