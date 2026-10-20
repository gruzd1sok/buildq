import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "bin" / "buildq"


def load_cli():
    loader = importlib.machinery.SourceFileLoader("buildq_cli", str(CLI))
    spec = importlib.util.spec_from_loader("buildq_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Home:
    """Isolated BUILDQ_HOME for one test."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name)
        self.env = {**os.environ, "BUILDQ_HOME": self.dir.name, "BUILDQ_ORPHAN_GRACE": "2"}

    def run(self, *args, input=None, **kw):
        return subprocess.run([sys.executable, str(CLI), *args], env=self.env, input=input,
                              capture_output=True, text=True, **kw)

    def popen(self, *args, **kw):
        return subprocess.Popen([sys.executable, str(CLI), *args], env=self.env, **kw)

    def cleanup(self):
        self.dir.cleanup()
