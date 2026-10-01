"""Run the offline regression suite."""
import unittest
from pathlib import Path

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1] / 'tests'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
