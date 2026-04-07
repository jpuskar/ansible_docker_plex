"""Add src/ to the import path so tests can import project modules.

Stubs out heavy dependencies (ultralytics/torch) that aren't needed for
unit-testing the pure-Python logic.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# Stub ultralytics before anything tries to import it — avoids pulling in
# torch/torchvision/onnxruntime just to test Detection, BaselineTracker, etc.
_ul = types.ModuleType("ultralytics")
_ul.YOLO = MagicMock()  # type: ignore[attr-defined]
sys.modules["ultralytics"] = _ul

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
