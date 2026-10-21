import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def hub():
    from reviewpilot.mcp_client import get_hub
    h = get_hub()
    yield h
    h.set_faults(None)
