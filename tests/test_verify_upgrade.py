import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'verify_upgrade', Path(__file__).resolve().parents[1] / 'scripts/verify_upgrade.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(id='a'*32, uid=1000, spec='{"mode":"both","scale":2}', state='running')

    def test_pause_is_allowed_without_changing_job(self):
        self.assertEqual(verifier.preserved_jobs([self.row], [dict(self.row, state='paused')]), 1)

    def test_missing_or_changed_job_is_rejected(self):
        for rows in ([], [dict(self.row, uid=980)], [dict(self.row, spec='{"mode":"upscale"}')]):
            with self.assertRaises(ValueError):
                verifier.preserved_jobs([self.row], rows)

    def test_completed_job_cannot_regress(self):
        before = dict(self.row, state='completed')
        with self.assertRaises(ValueError):
            verifier.preserved_jobs([before], [dict(before, state='running')])

    def test_json_format_does_not_count_as_parameter_change(self):
        after = dict(self.row, spec='{"scale": 2, "mode": "both"}')
        self.assertEqual(verifier.preserved_jobs([self.row], [after]), 1)


if __name__ == '__main__':
    unittest.main()
