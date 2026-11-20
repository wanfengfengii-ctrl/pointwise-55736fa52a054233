import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from itertools import product
from pathlib import Path

from analysis import initialize_analysis
from analysis.intake import accept_handoff, read_acceptance
from analysis.sample_comparison import (
    VARIANCE_METHOD,
    inclusion_rates,
    joint_inclusion_rates,
    point_estimate,
    variance_estimate,
)
from archive.issue import issue_report, issue_sample_report
from archive.readback import read_report
from bench import initialize_source
from bench.handoff import (
    issue_handoff,
    issue_sample_handoff,
    lock_sample,
    read_issued_handoff,
)
from bench.labels import record_label
from bench.sample_codec import decode_sample_handoff
from bench.sampling import parse_design
from predictions.intake import record_prediction
from trial.freeze import freeze_trial

FIXTURES = json.loads(
    (Path(__file__).resolve().parent.parent / 'fixtures' / 'sampling.json').read_text()
)
MODELS = tuple(FIXTURES['models'])
BASE_PREDICTIONS = tuple(FIXTURES['base_predictions'])


class SamplingCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source_path = root / 'source.sqlite3'
        self.analysis_path = root / 'analysis.sqlite3'
        self.source = sqlite3.connect(self.source_path)
        self.addCleanup(self.source.close)
        self.analysis = sqlite3.connect(self.analysis_path)
        self.addCleanup(self.analysis.close)
        initialize_source(self.source)
        initialize_analysis(self.analysis)

    def freeze(self, specimens=('X', 'Y'), trial_id='materials-17'):
        return freeze_trial(self.source, trial_id, specimens, *MODELS)

    def predict_pair(self, freeze_id, specimen_id, alpha='1/4', beta='3/4'):
        record_prediction(self.source, freeze_id, specimen_id, MODELS[0], alpha,
                          f'alpha sheet/{specimen_id}')
        record_prediction(self.source, freeze_id, specimen_id, MODELS[1], beta,
                          f'beta sheet/{specimen_id}')

    def run_case(self, case):
        frozen = self.freeze(case['specimens'], trial_id=f"materials-{case['id']}")
        paired = case.get('paired_at_lock', case['specimens'])
        for specimen_id in paired:
            self.predict_pair(frozen.freeze_id, specimen_id, *BASE_PREDICTIONS)
        locked = lock_sample(self.source, frozen.freeze_id, case['design'], case['draw'])
        for specimen_id, model_id, probability in case.get('after_lock', []):
            record_prediction(self.source, frozen.freeze_id, specimen_id, model_id,
                              probability, f'after/{model_id}/{specimen_id}')
        for specimen_id, value in case['labels'].items():
            record_label(self.source, frozen.freeze_id, specimen_id, value,
                         f'instrument/{specimen_id}')
        handoff = issue_sample_handoff(self.source, frozen.freeze_id)
        acceptance_id = accept_handoff(self.analysis, handoff.handoff_id, handoff.body_json)
        return locked, handoff, issue_sample_report(self.analysis, acceptance_id)

    def archive_counts(self):
        return self.analysis.execute('''
            SELECT (SELECT count(*) FROM reports), (SELECT count(*) FROM issue_receipts)
        ''').fetchone()


class TestFixtures(SamplingCase):
    def test_fixture_cases(self):
        for case in FIXTURES['cases']:
            with self.subTest(case=case['id']):
                locked, handoff, report = self.run_case(case)
                expected = case['expected']
                body = report.body
                self.assertEqual(tuple(expected['sample']), locked.sample)
                self.assertEqual(body.population_count, expected['population_count'])
                self.assertEqual(list(body.sample), expected['sample'])
                self.assertEqual(body.mean_difference, Fraction(expected['mean_difference']))
                self.assertIsInstance(body.mean_difference, Fraction)
                self.assertEqual(body.variance_status, expected['variance_status'])
                self.assertEqual([pair.specimen_id for pair in body.pairs], expected['sample'])
                if 'variance_estimate' in expected:
                    wanted = expected['variance_estimate']
                    if wanted is None:
                        self.assertIsNone(body.variance_estimate)
                        self.assertIsNone(body.variance_method)
                    else:
                        self.assertEqual(body.variance_estimate, Fraction(wanted))
                        self.assertIsInstance(body.variance_estimate, Fraction)
                        self.assertEqual(body.variance_method, VARIANCE_METHOD)
                if body.variance_status == 'estimable':
                    self.assertEqual(body.variance_method, VARIANCE_METHOD)
                    self.assertIsInstance(body.variance_estimate, Fraction)
                else:
                    self.assertIsNone(body.variance_method)
                    self.assertIsNone(body.variance_estimate)
                self.assertEqual((report.receipt.report_id, report.receipt.acceptance_id),
                                 (report.report_id, body.acceptance_id))
                self.assertEqual(read_report(self.analysis, report.report_id), report)

    def test_locked_revisions_survive_post_lock_correction(self):
        case, = (case for case in FIXTURES['cases'] if case['id'] == 'unequal_pairs')
        locked, handoff, report = self.run_case(case)
        body = decode_sample_handoff(handoff.body_json)
        alpha_x, = (p for p in body.predictions
                    if p.specimen_id == 'X' and p.model_id == MODELS[0])
        self.assertEqual(alpha_x.probability, Fraction(1, 4))
        self.assertEqual(alpha_x.source, 'alpha sheet/X')
        self.assertNotIn('W', body.population)
        self.assertEqual(body.population, ('X', 'Y', 'Z'))


class TestLock(SamplingCase):
    def design(self):
        return ((('X',), '1/2'), (('Y',), '1/2'))

    def test_first_lock_rejects_registered_labels(self):
        frozen = self.freeze()
        self.predict_pair(frozen.freeze_id, 'X')
        record_label(self.source, frozen.freeze_id, 'X', 1, 'early instrument')
        with self.assertRaisesRegex(ValueError, 'first lock'):
            lock_sample(self.source, frozen.freeze_id, ((('X',), 1),), 0)
        self.assertEqual(self.source.execute('SELECT count(*) FROM sample_draws').fetchone(), (0,))

    def test_lock_requires_paired_population(self):
        frozen = self.freeze()
        with self.assertRaisesRegex(ValueError, 'outside the locked population'):
            lock_sample(self.source, frozen.freeze_id, ((('X',), 1),), 0)
        record_prediction(self.source, frozen.freeze_id, 'X', MODELS[0], '1/4', 'alpha only')
        with self.assertRaisesRegex(ValueError, 'outside the locked population'):
            lock_sample(self.source, frozen.freeze_id, ((('X',), 1),), 0)
        record_prediction(self.source, frozen.freeze_id, 'X', MODELS[1], '3/4', 'beta sheet/X')
        locked = lock_sample(self.source, frozen.freeze_id, ((('X',), 1),), 0)
        self.assertEqual(locked.population, ('X',))
        self.assertEqual(locked.sample, ('X',))

    def test_lock_requires_full_coverage(self):
        frozen = self.freeze()
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        with self.assertRaisesRegex(ValueError, 'absent from every candidate list'):
            lock_sample(self.source, frozen.freeze_id, ((('X',), 1),), 0)
        locked = lock_sample(self.source, frozen.freeze_id, ((('Y', 'X'), 1),), 0)
        self.assertEqual(locked.population, ('X', 'Y'))
        self.assertEqual(locked.sample, ('X', 'Y'))

    def test_equivalent_retry_reuses_draw(self):
        frozen = self.freeze(('X', 'Y', 'Z'))
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        original = lock_sample(self.source, frozen.freeze_id, self.design(), '1/2')
        self.assertEqual(original.sample, ('Y',))
        # Supplementary predictions, corrections and labels never move the lock.
        self.predict_pair(frozen.freeze_id, 'Z')
        record_prediction(self.source, frozen.freeze_id, 'X', MODELS[0], '3/4', 'correction')
        record_label(self.source, frozen.freeze_id, 'Y', 1, 'instrument/Y')
        equivalent = ((('X',), Fraction(2, 4)), (('Y',), '2/4'))
        retried = lock_sample(self.source, frozen.freeze_id, equivalent, Fraction(1, 2))
        self.assertEqual(retried, original)
        self.assertEqual(retried.draw_id, original.draw_id)
        self.assertEqual(self.source.execute('SELECT count(*) FROM sample_draws').fetchone(), (1,))

    def test_conflicting_retry_rejected(self):
        frozen = self.freeze()
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        original = lock_sample(self.source, frozen.freeze_id, self.design(), '1/2')
        reordered = ((('Y',), '1/2'), (('X',), '1/2'))
        unbalanced = ((('X',), '1/3'), (('Y',), '1/3'))
        for design, draw in (
            (self.design(), '1/4'),
            (reordered, '1/2'),
            (unbalanced, '1/2'),
        ):
            with self.subTest(design=design, draw=draw):
                with self.assertRaises(ValueError):
                    lock_sample(self.source, frozen.freeze_id, design, draw)
        self.assertEqual(lock_sample(self.source, frozen.freeze_id, self.design(), '1/2'), original)

    def test_invalid_design_and_draw(self):
        frozen = self.freeze()
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        bad_designs = (
            (),
            (((), 1),),
            ((('X',), '1/2'), (('Y', 'X'), '1/2')),
            ((('X', 'Y'), '1/2'), (('Y', 'X'), '1/2')),
            ((('X', 'X'), 1),),
            ((('X',), '1/2'), (('Y',), '1/4')),
            ((('X',), '1/2'), (('Y',), '-1/2'), (('X', 'Y'), 1)),
            ((('X',), 0), (('Y',), 1)),
            ((('X',), 0.5), (('Y',), 0.5)),
            ((('X',), True), (('Y',), 1)),
            ((('Z',), 1),),
            ((('X',), '1/2'),),
            'not-a-design',
        )
        for design in bad_designs:
            with self.subTest(design=design):
                with self.assertRaises(ValueError):
                    lock_sample(self.source, frozen.freeze_id, design, 0)
        for draw in (1, '3/2', '-1/4', 0.5, True, 'abc'):
            with self.subTest(draw=draw):
                with self.assertRaises(ValueError):
                    lock_sample(self.source, frozen.freeze_id, ((('X', 'Y'), 1),), draw)
        self.assertEqual(self.source.execute('SELECT count(*) FROM sample_draws').fetchone(), (0,))
        with self.assertRaises(KeyError):
            lock_sample(self.source, 'absent-freeze', ((('X', 'Y'), 1),), 0)

    def test_draw_intervals_are_left_closed(self):
        frozen = self.freeze()
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        first = lock_sample(self.source, frozen.freeze_id, self.design(), '1/2')
        self.assertEqual(first.sample, ('Y',))
        other = self.freeze(('X', 'Y'), trial_id='materials-18')
        self.predict_pair(other.freeze_id, 'X')
        self.predict_pair(other.freeze_id, 'Y')
        second = lock_sample(self.source, other.freeze_id, self.design(), 0)
        self.assertEqual(second.sample, ('X',))
        self.assertNotEqual(first.draw_id, second.draw_id)


class TestSampleHandoff(SamplingCase):
    def locked_freeze(self):
        frozen = self.freeze(('X', 'Y', 'Z'))
        self.predict_pair(frozen.freeze_id, 'X')
        self.predict_pair(frozen.freeze_id, 'Y')
        locked = lock_sample(self.source, frozen.freeze_id,
                             ((('X',), '1/2'), (('Y',), '1/2')), 0)
        return frozen, locked

    def test_issue_requires_lock_and_sampled_labels(self):
        frozen = self.freeze()
        with self.assertRaisesRegex(ValueError, 'no locked sample'):
            issue_sample_handoff(self.source, frozen.freeze_id)
        with self.assertRaises(KeyError):
            issue_sample_handoff(self.source, 'absent-freeze')
        locked_freeze, locked = self.locked_freeze()
        with self.assertRaisesRegex(ValueError, 'missing final labels'):
            issue_sample_handoff(self.source, locked_freeze.freeze_id)
        record_label(self.source, locked_freeze.freeze_id, 'X', 1, 'instrument/X')
        issued = issue_sample_handoff(self.source, locked_freeze.freeze_id)
        body = decode_sample_handoff(issued.body_json)
        self.assertEqual(body.sample, ('X',))
        self.assertEqual(tuple(label.specimen_id for label in body.labels), ('X',))
        self.assertEqual(read_issued_handoff(self.source, issued.handoff_id), issued)

    def test_body_carries_only_locked_sample(self):
        frozen, locked = self.locked_freeze()
        # Labels and predictions outside the locked sample never enter the body.
        record_label(self.source, frozen.freeze_id, 'Y', 0, 'instrument/Y')
        record_label(self.source, frozen.freeze_id, 'X', 1, 'instrument/X')
        record_prediction(self.source, frozen.freeze_id, 'X', MODELS[0], '3/4', 'late correction')
        self.predict_pair(frozen.freeze_id, 'Z', alpha='1/2', beta='1/2')
        issued = issue_sample_handoff(self.source, frozen.freeze_id)
        raw = json.loads(issued.body_json)
        self.assertEqual(raw['version'], 2)
        self.assertEqual(raw['specimens'], ['X', 'Y'])
        self.assertEqual(raw['sample'], ['X'])
        self.assertEqual([label['specimen_id'] for label in raw['labels']], ['X'])
        self.assertEqual(len(raw['predictions']), 4)
        body = decode_sample_handoff(issued.body_json)
        alpha_x, = (p for p in body.predictions
                    if p.specimen_id == 'X' and p.model_id == MODELS[0])
        self.assertEqual(alpha_x.probability, Fraction(1, 4))
        self.assertEqual(raw['draw_id'], locked.draw_id)
        self.assertEqual(raw['draw'], [0, 1])
        self.assertEqual(raw['design'], [[['X'], [1, 2]], [['Y'], [1, 2]]])

    def test_v1_issue_still_requires_all_labels(self):
        frozen, _ = self.locked_freeze()
        record_label(self.source, frozen.freeze_id, 'X', 1, 'instrument/X')
        with self.assertRaisesRegex(ValueError, 'missing final labels'):
            issue_handoff(self.source, frozen.freeze_id)


class TestSampleIntake(SamplingCase):
    def setUp(self):
        super().setUp()
        self.frozen = self.freeze(('X', 'Y', 'Z', 'W'))
        for specimen_id in self.frozen.roster.specimens:
            self.predict_pair(self.frozen.freeze_id, specimen_id)
        lock_sample(self.source, self.frozen.freeze_id,
                    ((('X', 'Y'), '1/2'), (('Z', 'W'), '1/2')), 0)
        record_label(self.source, self.frozen.freeze_id, 'X', 1, 'instrument/X')
        record_label(self.source, self.frozen.freeze_id, 'Y', 0, 'instrument/Y')
        self.issued = issue_sample_handoff(self.source, self.frozen.freeze_id)

    def accepted_count(self):
        return self.analysis.execute('SELECT count(*) FROM accepted_handoffs').fetchone()[0]

    def test_equivalent_encoding_reuses_acceptance(self):
        first = accept_handoff(self.analysis, self.issued.handoff_id, self.issued.body_json)
        payload = json.loads(self.issued.body_json)
        payload['predictions'].reverse()
        payload['labels'].reverse()
        payload['design'][0][0].reverse()
        payload['design'][0][1] = [2, 4]
        payload['draw'] = [0, 7]
        reordered = dict(reversed(tuple(payload.items())))
        self.assertEqual(
            accept_handoff(self.analysis, self.issued.handoff_id, json.dumps(reordered)), first)
        self.assertEqual(read_acceptance(self.analysis, first).body_json, self.issued.body_json)
        self.assertEqual(self.accepted_count(), 1)

    def test_conflicting_and_invalid_bodies(self):
        first = accept_handoff(self.analysis, self.issued.handoff_id, self.issued.body_json)
        changed = json.loads(self.issued.body_json)
        changed['design'][0][1] = [1, 3]
        reordered_design = json.loads(self.issued.body_json)
        reordered_design['design'].reverse()
        relabeled = json.loads(self.issued.body_json)
        relabeled['labels'][0]['value'] = 0
        for number, payload in enumerate((changed, reordered_design, relabeled)):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    accept_handoff(self.analysis, self.issued.handoff_id, json.dumps(payload))
        self.assertEqual(read_acceptance(self.analysis, first).body_json, self.issued.body_json)
        self.assertEqual(self.accepted_count(), 1)

    def test_internally_invalid_bodies(self):
        base = json.loads(self.issued.body_json)
        wrong_sample = json.loads(self.issued.body_json)
        wrong_sample['sample'] = ['Z', 'W']
        missing_label = json.loads(self.issued.body_json)
        missing_label['labels'].pop()
        extra_label = json.loads(self.issued.body_json)
        extra = dict(extra_label['labels'][0], specimen_id='Z', label_id='label-extra')
        extra_label['labels'].append(extra)
        missing_prediction = json.loads(self.issued.body_json)
        missing_prediction['predictions'].pop()
        bad_weights = json.loads(self.issued.body_json)
        bad_weights['design'][1][1] = [1, 3]
        outsider = json.loads(self.issued.body_json)
        outsider['design'][0][0] = ['X', 'Q']
        uncovered = json.loads(self.issued.body_json)
        uncovered['design'] = [[['X', 'Y'], [1, 1]]]
        wrong_version = dict(json.loads(self.issued.body_json), version=1)
        for number, payload in enumerate((
                wrong_sample, missing_label, extra_label, missing_prediction,
                bad_weights, outsider, uncovered, wrong_version)):
            with self.subTest(number=number):
                with self.assertRaises(ValueError):
                    accept_handoff(self.analysis, f'bad-sample-{number}', json.dumps(payload))
                self.assertEqual(self.accepted_count(), 0)
        self.assertEqual(base['version'], 2)


class TestSampleReport(SamplingCase):
    def publish_v1(self):
        frozen = self.freeze(('X', 'Y'), trial_id='materials-v1')
        self.predict_pair(frozen.freeze_id, 'X')
        record_label(self.source, frozen.freeze_id, 'X', 1, 'instrument/X')
        record_label(self.source, frozen.freeze_id, 'Y', 0, 'instrument/Y')
        issued = issue_handoff(self.source, frozen.freeze_id)
        return accept_handoff(self.analysis, issued.handoff_id, issued.body_json), issued

    def publish_v2(self, design=((('X', 'Y'), '1/2'), (('Z', 'W'), '1/2')), draw=0):
        frozen = self.freeze(('X', 'Y', 'Z', 'W'), trial_id='materials-v2')
        for specimen_id in frozen.roster.specimens:
            self.predict_pair(frozen.freeze_id, specimen_id)
        lock_sample(self.source, frozen.freeze_id, design, draw)
        record_label(self.source, frozen.freeze_id, 'X', 1, 'instrument/X')
        record_label(self.source, frozen.freeze_id, 'Y', 0, 'instrument/Y')
        issued = issue_sample_handoff(self.source, frozen.freeze_id)
        acceptance_id = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        return acceptance_id, issued

    def test_version_mismatch(self):
        v1_acceptance, v1_issued = self.publish_v1()
        v2_acceptance, v2_issued = self.publish_v2()
        with self.assertRaises(ValueError):
            issue_sample_report(self.analysis, v1_acceptance)
        with self.assertRaises(ValueError):
            issue_report(self.analysis, v2_issued.handoff_id)
        with self.assertRaises(KeyError):
            issue_sample_report(self.analysis, 'absent-acceptance')
        with self.assertRaises(KeyError):
            issue_sample_report(self.analysis, v2_issued.handoff_id)
        with self.assertRaises(KeyError):
            issue_report(self.analysis, v2_acceptance)
        self.assertEqual(self.archive_counts(), (0, 0))

    def test_same_sample_distinct_eligibility(self):
        case_pairs, case_blocks = FIXTURES['cases'][0], FIXTURES['cases'][1]
        _, _, pairs_report = self.run_case(case_pairs)
        _, _, blocks_report = self.run_case(case_blocks)
        self.assertEqual(pairs_report.body.sample, blocks_report.body.sample)
        self.assertEqual(pairs_report.body.mean_difference, blocks_report.body.mean_difference)
        self.assertEqual(pairs_report.body.variance_status, 'estimable')
        self.assertEqual(pairs_report.body.variance_estimate, Fraction(0))
        self.assertEqual(blocks_report.body.variance_status, 'joint_gap')
        self.assertIsNone(blocks_report.body.variance_estimate)
        self.assertIsNone(blocks_report.body.variance_method)
        self.assertEqual(self.archive_counts(), (2, 2))

    def test_census_zero_variance_kept(self):
        case, = (case for case in FIXTURES['cases'] if case['id'] == 'census_draw')
        _, _, report = self.run_case(case)
        self.assertEqual(report.body.variance_status, 'estimable')
        self.assertEqual(report.body.variance_estimate, Fraction(0))
        self.assertIsInstance(report.body.variance_estimate, Fraction)
        self.assertEqual(report.body.variance_method, VARIANCE_METHOD)

    def test_negative_variance_kept(self):
        # Contrived but valid design whose Horvitz-Thompson estimate goes negative.
        design = ((('X', 'Y'), '1/16'), (('X', 'Z'), '7/16'), (('Y', 'Z'), '8/16'))
        frozen = self.freeze(('X', 'Y', 'Z'), trial_id='materials-negative')
        for specimen_id in frozen.roster.specimens:
            self.predict_pair(frozen.freeze_id, specimen_id)
        lock_sample(self.source, frozen.freeze_id, design, 0)
        record_label(self.source, frozen.freeze_id, 'X', 1, 'instrument/X')
        record_label(self.source, frozen.freeze_id, 'Y', 1, 'instrument/Y')
        issued = issue_sample_handoff(self.source, frozen.freeze_id)
        acceptance_id = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        report = issue_sample_report(self.analysis, acceptance_id)
        self.assertEqual(report.body.variance_status, 'estimable')
        self.assertEqual(report.body.mean_difference, Fraction(17, 27))
        self.assertEqual(report.body.variance_estimate, Fraction(-871, 1458))
        self.assertLess(report.body.variance_estimate, 0)
        self.assertEqual(read_report(self.analysis, report.report_id), report)

    def test_readback_after_reopen(self):
        acceptance_id, issued = self.publish_v2()
        report = issue_sample_report(self.analysis, acceptance_id)
        self.analysis.close()
        self.analysis = sqlite3.connect(self.analysis_path)
        initialize_analysis(self.analysis)
        stored = read_report(self.analysis, report.report_id)
        self.assertEqual(stored, report)
        self.assertEqual(stored.body.handoff_id, issued.handoff_id)
        accepted = read_acceptance(self.analysis, acceptance_id)
        self.assertEqual(accepted.body_json, issued.body_json)

    def test_issue_rollback_and_retry(self):
        acceptance_id, _ = self.publish_v2()
        self.analysis.executescript('''
            CREATE TABLE test_commit_permission (id INTEGER PRIMARY KEY);
            CREATE TABLE test_pending_permission (
                id INTEGER REFERENCES test_commit_permission(id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER test_fail_commit AFTER INSERT ON issue_receipts
            BEGIN
                INSERT INTO test_pending_permission (id) VALUES (1);
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            issue_sample_report(self.analysis, acceptance_id)
        self.assertFalse(self.analysis.in_transaction)
        self.assertEqual(self.archive_counts(), (0, 0))
        self.assertEqual(read_acceptance(self.analysis, acceptance_id).acceptance_id, acceptance_id)
        self.analysis.execute('DROP TRIGGER test_fail_commit')
        report = issue_sample_report(self.analysis, acceptance_id)
        self.assertEqual(report.body.variance_status, 'joint_gap')
        self.assertEqual(read_report(self.analysis, report.report_id), report)
        self.assertEqual(self.archive_counts(), (1, 1))


class TestVarianceUnbiasedness(unittest.TestCase):
    """E[variance estimate] over the design equals the design variance of the estimate."""

    def test_documented_identity(self):
        for case in FIXTURES['cases']:
            population = tuple(case.get('paired_at_lock', case['specimens']))
            design = parse_design(case['design'])
            rates = inclusion_rates(design, population)
            joints = joint_inclusion_rates(design, population)
            if any(joint == 0 for joint in joints.values()):
                continue
            count = len(population)
            with self.subTest(case=case['id']):
                for assignment in product((Fraction(1, 2), Fraction(-1, 2)), repeat=count):
                    differences = dict(zip(population, assignment))
                    draws = []
                    for entry in design:
                        sample = tuple(s for s in population if s in entry.members)
                        draws.append((
                            entry.weight,
                            point_estimate(count, sample, rates, differences),
                            variance_estimate(count, sample, rates, joints, differences),
                        ))
                    mean = sum(weight * total for weight, total, _ in draws)
                    design_variance = sum(weight * (total - mean) ** 2
                                          for weight, total, _ in draws)
                    expected_estimate = sum(weight * estimate
                                            for weight, _, estimate in draws)
                    self.assertEqual(expected_estimate, design_variance)

    def test_joint_gap_detection(self):
        for case in FIXTURES['cases']:
            population = tuple(case.get('paired_at_lock', case['specimens']))
            design = parse_design(case['design'])
            joints = joint_inclusion_rates(design, population)
            with self.subTest(case=case['id']):
                if case['id'] == 'whole_blocks':
                    self.assertEqual(joints[frozenset(('X', 'Z'))], Fraction(0))
                else:
                    self.assertTrue(all(joint > 0 for joint in joints.values()))


if __name__ == '__main__':
    unittest.main()
