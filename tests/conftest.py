import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adtone import config  # noqa: E402


@pytest.fixture
def tmp_data(monkeypatch, tmp_path):
    """Point every data path at a temporary directory."""
    data = tmp_path / "data"
    paths = {
        "DATA": data, "ADS_DIR": data / "ads", "MEDIA_DIR": data / "media", "OBS_DIR": data / "obs",
        "VEC_DIR": data / "vectors", "STATE_DIR": data / "state", "PROV_DIR": data / "provenance",
        "RESULTS_DIR": data / "results", "CANDIDATES_FILE": data / "registry" / "page_candidates.json",
        "HUMAN_DIR": data / "human_check",
    }
    for k, v in paths.items():
        monkeypatch.setattr(config, k, v)
    for k in ("ADS_DIR", "MEDIA_DIR", "OBS_DIR", "VEC_DIR", "STATE_DIR", "PROV_DIR", "RESULTS_DIR"):
        paths[k].mkdir(parents=True, exist_ok=True)
    return data
