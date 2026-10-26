"""Run the integrated suite from a repository checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'suite'))
from run_suite import start

if __name__ == '__main__':
    raise SystemExit(start())
