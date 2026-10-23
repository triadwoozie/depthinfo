"""Tests run entirely in mock mode (AGENTS.md Rule 3): no audio, no camera, no network."""
import os
import sys
from pathlib import Path

os.environ["SONAR_MOCK_MODE"] = "true"
os.environ.pop("SONAR_REAL_CAMERA", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
