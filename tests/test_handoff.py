import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from analysis import initialize_analysis
from analysis.intake import accept_handoff, read_acceptance
from bench import initialize_source
from bench.codec import decode_handoff
from bench.handoff import issue_handoff, read_issued_handoff
from bench.labels import load_final_labels, record_label
from predictions.intake import record_prediction
from trial.freeze import freeze_trial


class TestHandoff(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source_path = root / 'source.sqlite3'
        self.source = sqlite3.connect(self.source_path)
        self.addCleanup(self.source.close)
        self.analysis = sqlite3.connect(root / 'analysis.sqlite3')
        self.addCleanup(self.analysis.close)
        initialize_source(self.source)
        initialize_analysis(self.analysis)
        self.freeze_id = freeze_trial(self.source, 'materials-17', ('X', 'Y'), 'A', 'B').freeze_id

    def issue_sample(self):
        record_prediction(self.source, self.freeze_id, 'X', 'A', '1/4', 'alpha sheet')
        record_prediction(self.source, self.freeze_id, 'X', 'B', '3/4', 'beta sheet')
        record_label(self.source, self.freeze_id, 'X', 1, 'X instrument')
        record_label(self.source, self.freeze_id, 'Y', 0, 'Y instrument')
        return issue_handoff(self.source, self.freeze_id)

    def issued_count(self):
        return self.source.execute('SELECT count(*) FROM issued_handoffs').fetchone()[0]

    def accepted_count(self):
        return self.analysis.execute('SELECT count(*) FROM accepted_handoffs').fetchone()[0]

    def test_labels_required(self):
        record_label(self.source, self.freeze_id, 'X', 1, 'X instrument')
        with self.assertRaisesRegex(ValueError, 'missing final labels'):
            issue_handoff(self.source, self.freeze_id)
        self.assertEqual(self.issued_count(), 0)
        with self.assertRaises(KeyError):
            issue_handoff(self.source, 'absent-freeze')
        self.assertEqual(self.issued_count(), 0)

    def test_reaccept(self):
        issued = self.issue_sample()
        first = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        self.assertEqual(accept_handoff(self.analysis, issued.handoff_id, issued.body_json), first)
        self.assertEqual(read_acceptance(self.analysis, first).handoff_id, issued.handoff_id)
        self.assertEqual(self.accepted_count(), 1)

    def test_conflict(self):
        issued = self.issue_sample()
        accepted_id = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        original = read_acceptance(self.analysis, accepted_id)
        changed = json.loads(issued.body_json)
        changed['predictions'][0]['probability'] = [1, 2]
        with self.assertRaisesRegex(ValueError, 'conflicting body'):
            accept_handoff(self.analysis, issued.handoff_id, json.dumps(changed))
        self.assertEqual(read_acceptance(self.analysis, accepted_id), original)
        self.assertEqual(self.accepted_count(), 1)

    def test_label_replay_conflict(self):
        original = record_label(self.source, self.freeze_id, 'X', 1, 'X instrument')
        self.assertEqual(record_label(self.source, self.freeze_id, 'X', 1, 'X instrument'), original)
        for specimen, value, source in (
            ('X', 0, 'X instrument'), ('X', 1, 'other instrument'),
            ('Y', 2, 'Y instrument'), ('Y', True, 'Y instrument'), ('Z', 0, 'Z instrument'),
        ):
            with self.subTest(specimen=specimen, value=value, source=source):
                with self.assertRaises(ValueError):
                    record_label(self.source, self.freeze_id, specimen, value, source)
        with self.assertRaises(KeyError):
            record_label(self.source, 'absent-freeze', 'X', 1, 'X instrument')
        self.assertEqual(load_final_labels(self.source, self.freeze_id), {'X': original})

    def test_complete_retry(self):
        x = record_label(self.source, self.freeze_id, 'X', 1, 'X instrument')
        with self.assertRaises(ValueError):
            issue_handoff(self.source, self.freeze_id)
        self.assertEqual(self.issued_count(), 0)
        y = record_label(self.source, self.freeze_id, 'Y', 0, 'Y instrument')
        issued = issue_handoff(self.source, self.freeze_id)
        body = decode_handoff(issued.body_json)
        self.assertEqual(body.freeze_id, self.freeze_id)
        self.assertEqual(body.roster.specimens, ('X', 'Y'))
        self.assertEqual([(label.specimen_id, label.label_id, label.value) for label in body.labels],
                         [('X', x.label_id, 1), ('Y', y.label_id, 0)])
        self.assertEqual(body.predictions, ())
        self.assertEqual(self.issued_count(), 1)
        self.assertEqual(read_issued_handoff(self.source, issued.handoff_id), issued)

    def test_snapshot_replay(self):
        original = self.issue_sample()
        first = accept_handoff(self.analysis, original.handoff_id, original.body_json)
        first_body = decode_handoff(original.body_json)
        correction = record_prediction(self.source, self.freeze_id, 'X', 'A', '1/2', 'replacement sheet')
        self.source.close()
        with closing(sqlite3.connect(self.source_path)) as reopened:
            initialize_source(reopened)
            reread = read_issued_handoff(reopened, original.handoff_id)
            second = issue_handoff(reopened, self.freeze_id)
        self.assertEqual(reread, original)
        self.assertEqual(accept_handoff(self.analysis, reread.handoff_id, reread.body_json), first)
        self.assertNotEqual(original.handoff_id, second.handoff_id)
        self.assertEqual(first_body.predictions[0].probability, Fraction(1, 4))
        self.assertEqual(first_body.predictions[0].source, 'alpha sheet')
        self.assertNotEqual(first_body.predictions[0].revision_id, correction.revision_id)
        second_body = decode_handoff(second.body_json)
        new_alpha = second_body.predictions[0]
        self.assertEqual((new_alpha.revision_id, new_alpha.probability, new_alpha.source),
                         (correction.revision_id, Fraction(1, 2), 'replacement sheet'))
        self.assertEqual(first_body.predictions[1], second_body.predictions[1])
        self.assertNotEqual(accept_handoff(self.analysis, second.handoff_id, second.body_json), first)

    def test_equivalent_encoding(self):
        issued = self.issue_sample()
        first = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        payload = json.loads(issued.body_json)
        payload['predictions'][0]['probability'] = [2, 8]
        payload['predictions'].reverse()
        payload['labels'].reverse()
        reordered = dict(reversed(tuple(payload.items())))
        self.assertEqual(accept_handoff(self.analysis, issued.handoff_id, json.dumps(reordered)), first)
        self.assertEqual(read_acceptance(self.analysis, first).body_json, issued.body_json)
        self.assertEqual(self.accepted_count(), 1)

    def test_invalid_body(self):
        issued = self.issue_sample()
        missing_label = json.loads(issued.body_json)
        missing_label['labels'].pop()
        duplicate_slot = json.loads(issued.body_json)
        duplicate = dict(duplicate_slot['predictions'][0], revision_id='duplicate-slot-revision')
        duplicate_slot['predictions'].append(duplicate)
        duplicate_id = json.loads(issued.body_json)
        duplicate_id['predictions'][1]['revision_id'] = duplicate_id['predictions'][0]['revision_id']
        wrong_version = dict(json.loads(issued.body_json), version=2)
        for number, payload in enumerate((missing_label, duplicate_slot, duplicate_id, wrong_version)):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    accept_handoff(self.analysis, f'bad-handoff-{number}', json.dumps(payload))
                self.assertEqual(self.accepted_count(), 0)

    def test_provenance_conflict(self):
        issued = self.issue_sample()
        first = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        changed = json.loads(issued.body_json)
        changed['predictions'][0]['source'] = 'different sheet'
        with self.assertRaisesRegex(ValueError, 'conflicting body'):
            accept_handoff(self.analysis, issued.handoff_id, json.dumps(changed))
        self.assertEqual(read_acceptance(self.analysis, first).body_json, issued.body_json)
        self.assertEqual(self.accepted_count(), 1)
