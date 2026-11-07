import os
import sys
import tempfile
from pathlib import Path

# every test runs against a throwaway output folder and a fixed time zone
os.environ["OUT_DIR"] = tempfile.mkdtemp(prefix="reporter-test-")
os.environ["TZ"] = "Africa/Accra"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
