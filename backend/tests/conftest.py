import pathlib
import sys

# Ensure the backend package is importable when running pytest from any directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
