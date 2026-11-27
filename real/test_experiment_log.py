"""The optional experiment writer bounds backlog and reports omitted rows."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest

from real.experiment_log import ExperimentLog


class ExperimentLogTests(unittest.TestCase):
    def test_bounded_queue_flushes_count_and_first_record(self):
        gate = Event()

        class PausedWriter(ExperimentLog):
            def _write(self):
                gate.wait(2.)
                super()._write()

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.jsonl'
            with PausedWriter(path, capacity=1) as trace:
                try:
                    for sequence in range(3):
                        trace.record(dict(type='telemetry', seq=sequence))
                    self.assertEqual(trace.dropped, 2)
                finally:
                    gate.set()
            self.assertIsNone(trace.error)
            self.assertEqual([json.loads(line) for line in path.read_text().splitlines()],
                             [dict(type='telemetry', seq=0), dict(type='trace_summary', dropped=2)])


if __name__ == '__main__':
    unittest.main()
