import pathlib
import sys
from unittest.mock import MagicMock

# Ensure the backend package is importable when running pytest from any directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# qbittorrentapi is not installed in the test environment.  Stub it out so that
# modules that import it at module-level (e.g. app.routers.downloads) can be
# collected by pytest without errors.  Individual tests that exercise qBittorrent
# behaviour mock the relevant functions directly.
if "qbittorrentapi" not in sys.modules:
    sys.modules["qbittorrentapi"] = MagicMock()
