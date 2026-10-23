import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise RuntimeError("Live network access is blocked in unit tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
