import sqlite3
import tempfile
import unittest
from contextlib import closing
from fractions import Fraction
from pathlib import Path

from analysis import initialize_analysis
from analysis.intake import accept_handoff, read_acceptance
from archive.issue import issue_report
from archive.readback import read_report
from bench import initialize_source
from bench.handoff import issue_handoff
from bench.labels import record_label
from predictions.intake import record_prediction
from trial.freeze import freeze_trial


SAMPLE_PREDICTIONS = (('X', 'A', '1/4'), ('X', 'B', '3/4'), ('Y', 'A', 1))
SAMPLE_LABELS = (('X', 1), ('Y', 1))


class TestReport(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.source_path = root / 'source.sqlite3'
        self.analysis_path = root / 'analysis.sqlite3'
        self.analysis = sqlite3.connect(self.analysis_path)
        self.addCleanup(lambda: self.analysis.close())
        initialize_analysis(self.analysis)

    def publish(self, specimens=('X', 'Y'), predictions=SAMPLE_PREDICTIONS, labels=SAMPLE_LABELS):
        revisions = {}
        final_labels = {}
        with closing(sqlite3.connect(self.source_path)) as source:
            initialize_source(source)
            frozen = freeze_trial(source, 'materials-17', specimens, 'A', 'B')
            for specimen_id, model_id, probability in predictions:
                revisions[specimen_id, model_id] = record_prediction(
                    source, frozen.freeze_id, specimen_id, model_id, probability,
                    f'sheet/{model_id}/{specimen_id}',
                )
            for specimen_id, value in labels:
                final_labels[specimen_id] = record_label(
                    source, frozen.freeze_id, specimen_id, value, f'instrument/{specimen_id}',
                )
            issued = issue_handoff(source, frozen.freeze_id)
            accepted_id = accept_handoff(self.analysis, issued.handoff_id, issued.body_json)
        return read_acceptance(self.analysis, accepted_id), revisions, final_labels

    def reopen_analysis(self):
        self.analysis.close()
        self.analysis = sqlite3.connect(self.analysis_path)
        initialize_analysis(self.analysis)

    def archive_counts(self):
        return self.analysis.execute('''
            SELECT (SELECT count(*) FROM reports), (SELECT count(*) FROM issue_receipts)
        ''').fetchone()

    def test_paired_loss(self):
        accepted, revisions, labels = self.publish()
        report = issue_report(self.analysis, accepted.handoff_id)
        self.assertEqual(report.body.acceptance_id, accepted.acceptance_id)
        self.assertEqual(report.body.handoff_id, accepted.handoff_id)
        self.assertEqual((report.body.alpha_model, report.body.beta_model), ('A', 'B'))
        self.assertEqual(report.body.freeze_id, revisions['X', 'A'].freeze_id)
        self.assertEqual(report.body.pair_count, 1)
        self.assertEqual(report.body.mean_difference, Fraction(1, 2))
        pair, = report.body.pairs
        self.assertEqual((pair.specimen_id, pair.label_id, pair.label, pair.label_source),
                         ('X', labels['X'].label_id, 1, 'instrument/X'))
        self.assertEqual((pair.alpha_revision_id, pair.alpha_source),
                         (revisions['X', 'A'].revision_id, 'sheet/A/X'))
        self.assertEqual((pair.beta_revision_id, pair.beta_source),
                         (revisions['X', 'B'].revision_id, 'sheet/B/X'))
        self.assertEqual((report.receipt.report_id, report.receipt.acceptance_id),
                         (report.report_id, accepted.acceptance_id))
        self.reopen_analysis()
        self.assertEqual(read_report(self.analysis, report.report_id), report)

    def test_empty_pairs(self):
        accepted, _, _ = self.publish(predictions=(('X', 'A', 0), ('Y', 'B', 1)))
        report = issue_report(self.analysis, accepted.handoff_id)
        self.assertEqual(report.body.pairs, ())
        self.assertEqual(report.body.pair_count, 0)
        self.assertIsNone(report.body.mean_difference)
        self.assertEqual(read_report(self.analysis, report.report_id), report)

    def test_misaligned_slots(self):
        accepted, revisions, _ = self.publish(
            specimens=('Y', 'X'),
            predictions=(('X', 'B', '3/4'), ('Y', 'A', 1), ('X', 'A', '1/4')),
            labels=(('X', 1), ('Y', 1)),
        )
        report = issue_report(self.analysis, accepted.handoff_id)
        self.reopen_analysis()
        body = read_report(self.analysis, report.report_id).body
        self.assertEqual((body.pair_count, body.mean_difference), (1, Fraction(1, 2)))
        pair, = body.pairs
        self.assertEqual(pair.specimen_id, 'X')
        self.assertEqual((pair.alpha_revision_id, pair.beta_revision_id),
                         (revisions['X', 'A'].revision_id, revisions['X', 'B'].revision_id))
        self.assertEqual((pair.alpha_source, pair.beta_source), ('sheet/A/X', 'sheet/B/X'))

    def test_exact_ordered_mean(self):
        accepted, _, _ = self.publish(
            specimens=('Z', 'X', 'Y'),
            predictions=SAMPLE_PREDICTIONS + (('Z', 'B', '2/3'), ('Z', 'A', '1/3')),
            labels=SAMPLE_LABELS + (('Z', 0),),
        )
        issued = issue_report(self.analysis, accepted.handoff_id)
        self.reopen_analysis()
        report = read_report(self.analysis, issued.report_id)
        self.assertEqual([pair.specimen_id for pair in report.body.pairs], ['Z', 'X'])
        self.assertEqual(report.body.pair_count, 2)
        self.assertEqual(report.body.mean_difference, Fraction(1, 12))

    def test_unknown_ids(self):
        accepted, _, _ = self.publish()
        for handoff_id in ('absent-handoff', accepted.acceptance_id):
            with self.assertRaises(KeyError):
                issue_report(self.analysis, handoff_id)
        with self.assertRaises(KeyError):
            read_report(self.analysis, 'absent-report')
        with self.assertRaises(KeyError):
            read_acceptance(self.analysis, 'absent-acceptance')
        self.assertEqual(self.archive_counts(), (0, 0))

    def test_reinitialize_analysis(self):
        accepted, _, _ = self.publish()
        report = issue_report(self.analysis, accepted.handoff_id)
        initialize_analysis(self.analysis)
        self.reopen_analysis()
        self.assertEqual(read_acceptance(self.analysis, accepted.acceptance_id), accepted)
        self.assertEqual(accept_handoff(self.analysis, accepted.handoff_id, accepted.body_json),
                         accepted.acceptance_id)
        self.assertEqual(read_report(self.analysis, report.report_id), report)
        self.assertEqual(self.archive_counts(), (1, 1))

    def test_null_round_trip(self):
        empty, _, _ = self.publish(predictions=(('X', 'A', 1),))
        equal, _, _ = self.publish(predictions=(('X', 'A', '1/4'), ('X', 'B', '1/4')))
        empty_report = issue_report(self.analysis, empty.handoff_id)
        equal_report = issue_report(self.analysis, equal.handoff_id)
        self.reopen_analysis()
        empty_body = read_report(self.analysis, empty_report.report_id).body
        equal_body = read_report(self.analysis, equal_report.report_id).body
        self.assertEqual(empty_body.pair_count, 0)
        self.assertIsNone(empty_body.mean_difference)
        self.assertEqual(equal_body.pair_count, 1)
        self.assertIsInstance(equal_body.mean_difference, Fraction)
        self.assertEqual(equal_body.mean_difference, Fraction(0))

    def install_deferred_failure(self):
        # The insert of a receipt succeeds. Its trigger makes the actual commit fail.
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

    def fail_then_retry(self, acceptance_id, pair_count, difference):
        before = self.archive_counts()
        self.install_deferred_failure()
        accepted = read_acceptance(self.analysis, acceptance_id)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            issue_report(self.analysis, accepted.handoff_id)
        self.assertFalse(self.analysis.in_transaction)
        self.assertEqual(self.archive_counts(), before)
        self.assertEqual(self.analysis.execute('SELECT count(*) FROM test_pending_permission').fetchone(), (0,))
        self.assertEqual(read_acceptance(self.analysis, acceptance_id), accepted)

        self.analysis.execute('DROP TRIGGER test_fail_commit')
        report = issue_report(self.analysis, read_acceptance(self.analysis, acceptance_id).handoff_id)
        self.reopen_analysis()
        archived = read_report(self.analysis, report.report_id)
        self.assertEqual(archived, report)
        self.assertEqual((archived.body.pair_count, archived.body.mean_difference), (pair_count, difference))
        self.assertEqual(archived.receipt.acceptance_id, acceptance_id)
        self.assertEqual(self.archive_counts(), (before[0] + 1, before[1] + 1))

    def test_issue_rollback(self):
        accepted, _, _ = self.publish()
        previous = issue_report(self.analysis, accepted.handoff_id)
        self.fail_then_retry(accepted.acceptance_id, 1, Fraction(1, 2))
        self.assertEqual(read_report(self.analysis, previous.report_id), previous)

    def test_empty_retry(self):
        accepted, _, _ = self.publish(predictions=(('Y', 'B', 1),))
        self.fail_then_retry(accepted.acceptance_id, 0, None)
