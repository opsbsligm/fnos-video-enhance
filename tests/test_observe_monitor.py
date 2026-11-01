import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'observe_monitor', Path(__file__).resolve().parents[1] / 'scripts/observe_monitor.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class ObservationTests(unittest.TestCase):
    def test_progress_is_allowlisted_and_has_event_time(self):
        with tempfile.TemporaryDirectory() as directory:
            var = Path(directory)
            (var / 'jobs').mkdir()
            job_id = 'a' * 32
            (var / 'jobs' / (job_id + '.progress.json')).write_text(json.dumps(
                dict(frames_done=12, yield_count=3, reason='过期', output_path='/private/file')))
            row = observer.observe(var, job_id=job_id)
            self.assertEqual(row['progress']['frames_done'], 12)
            self.assertEqual(row['progress']['yield_count'], 3)
            self.assertNotIn('output_path', row['progress'])
            self.assertGreater(row['progress_mtime'], 0)

    def test_job_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                observer.observe(Path(directory), job_id='../private')

    def test_age_is_real_and_observation_does_not_refresh_state(self):
        with tempfile.TemporaryDirectory() as directory:
            var = Path(directory)
            state = var / 'monitor.json'
            state.write_text(json.dumps(dict(updated_at=97, available=True)))
            before = state.read_bytes()
            with patch.object(observer.time, 'time', return_value=100):
                row = observer.observe(var)
            self.assertEqual(row['sample_age_seconds'], 3)
            self.assertEqual(state.read_bytes(), before)

    def test_missing_state_is_not_reported_as_available(self):
        with tempfile.TemporaryDirectory() as directory:
            row = observer.observe(Path(directory))
            self.assertEqual(row['monitor_error'], 'FileNotFoundError')
            self.assertNotIn('available', row)

    def test_invalid_state_is_not_reported_as_available(self):
        with tempfile.TemporaryDirectory() as directory:
            var = Path(directory)
            (var / 'monitor.json').write_text('bad')
            self.assertEqual(observer.observe(var)['monitor_error'], 'JSONDecodeError')


if __name__ == '__main__':
    unittest.main()
