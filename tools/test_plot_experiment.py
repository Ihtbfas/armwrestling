"""Offline log export checks using isolated JSONL and ZIP fixtures."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from tools.plot_experiment import analyze


def telemetry(*, session='A', peer='A', sequence, sample, pc_time, **fields):
    return dict(dict(type='telemetry', pc_monotonic_s=pc_time, session_id=session,
                     reported_session_id=peer, seq=sequence, mcu_sample_ms=sample,
                     angle_mrad=-350, velocity_mrad_s=-750, sensor_force_mn=1300,
                     angle_valid=True, velocity_valid=False, force_valid=False,
                     applied_target_mnm=0), **fields)


def jsonl(*records):
    return ''.join(json.dumps(record) + '\n' for record in records)


def csv_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


class PlotExperimentTests(unittest.TestCase):
    def test_clocks_missing_channels_and_verified_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'experiment.jsonl'
            source.write_text(jsonl(
                dict(type='experiment_config', pc_monotonic_s=100),
                dict(type='command_written', session_id='A', seq=0, mode='STOP',
                     pc_monotonic_s=100.1, target_mnm=0),
                telemetry(sequence=65535, sample=0xFFFFFFFE, pc_time=100.101,
                          ack_valid=True, last_command_seq=0, verified_ack_seq=0,
                          applied_target_mnm=400),
                telemetry(sequence=0, sample=2, pc_time=100.111, force_valid=True),
                telemetry(sequence=1, sample=2, pc_time=100.122),
                telemetry(sequence=3, sample=1, pc_time=100.137, velocity_valid=True),
                telemetry(sequence=3, sample=100, pc_time=100.139),
                telemetry(sequence=4, sample=3, pc_time=100.145, disposition='sample_stale'),
                telemetry(sequence=0, sample=75, pc_time=100.155, session='B', peer='B',
                          ack_valid=True, last_command_seq=0, verified_ack_seq=0),
                dict(type='trace_summary', dropped=2),
            ), encoding='utf-8')
            analysis = analyze(source, root / 'output')
            rows = [row for row in csv_rows(root / 'output/experiment.csv')
                    if row['type'] == 'telemetry']
            self.assertEqual(rows[0]['pc_elapsed_s'], '0.101')
            self.assertEqual(rows[0]['stop_ack_nonzero'], 'True')
            self.assertEqual(rows[0]['ack_target_diff_nm'], '0.4')
            self.assertEqual(rows[0]['velocity_rad_s'], '')
            self.assertEqual(rows[0]['force_n'], '')
            self.assertEqual(rows[1]['sample_state'], 'wrap')
            self.assertEqual(rows[1]['mcu_since_first_ms'], '4')
            self.assertEqual(rows[1]['mcu_sample_interval_ms'], '4')
            self.assertEqual(rows[1]['pc_rx_interval_ms'], '10.0')
            self.assertEqual(rows[1]['force_n'], '1.3')
            self.assertEqual(rows[2]['sample_state'], 'repeated')
            self.assertEqual(rows[2]['mcu_sample_interval_ms'], '')
            self.assertEqual(rows[3]['sample_state'], 'backwards')
            self.assertEqual(rows[3]['angle_rad'], '')
            self.assertEqual(rows[3]['velocity_rad_s'], '-0.75')
            self.assertEqual(rows[4]['sample_state'], 'old_sequence')
            self.assertEqual(rows[5]['angle_rad'], '')
            self.assertEqual(rows[5]['mcu_since_first_ms'], '5')
            self.assertEqual(rows[6]['sample_state'], 'first')
            self.assertEqual(rows[6]['ack_written_nm'], '')
            self.assertEqual(analysis.counts['seq_gap'], 1)
            self.assertEqual(analysis.dropped, 2)

    def test_zip_reads_exact_members_and_separates_calculated_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'capture.zip'
            with ZipFile(source, 'w') as archive:
                archive.writestr('experiment.jsonl', '{truncated\n' + jsonl(dict(
                    type='command_written', session_id='A', seq=1, mode='ENABLE',
                    pc_monotonic_s=101, target_mnm=500)))
                archive.writestr('pc.jsonl', jsonl(dict(
                    type='status', mode='enabled', elapsed_s=1.2,
                    target_torque_nm=.9, serial_applied_target_nm=.48)))
                archive.writestr('../not-extracted.txt', 'artifact')
            analysis = analyze(source, root / 'output')
            wire = csv_rows(root / 'output/experiment.csv')[0]
            snapshot = csv_rows(root / 'output/pc_snapshots.csv')[0]
            self.assertEqual(wire['command_written_nm'], '0.5')
            self.assertEqual(snapshot['pc_calculated_target_nm'], '0.9')
            self.assertEqual(snapshot['mcu_target_reported_nm'], '0.48')
            self.assertEqual(snapshot['force_n'], '')
            self.assertIsNone(analysis.dropped)
            self.assertEqual(analysis.counts['bad_lines'], 1)
            self.assertFalse((root / 'not-extracted.txt').exists())
            self.assertIn('低频快照', (root / 'output/summary.txt').read_text(encoding='utf-8'))

    def test_zip_requires_experiment_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'capture.zip'
            with ZipFile(source, 'w') as archive:
                archive.writestr('nested/experiment.jsonl', jsonl(dict(type='trace_summary')))
            with self.assertRaisesRegex(ValueError, 'experiment.jsonl'):
                analyze(source, root / 'output')


if __name__ == '__main__':
    unittest.main()
