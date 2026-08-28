import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "dpm-finder.py"
SPEC = importlib.util.spec_from_file_location("dpm_finder_tested", MODULE_PATH)
dpm_finder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dpm_finder)
