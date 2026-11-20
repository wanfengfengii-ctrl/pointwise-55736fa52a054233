import sqlite3
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from bench import initialize_source
from predictions.history import get_current_prediction, get_prediction_revision
from predictions.intake import record_prediction
from trial.freeze import freeze_trial, read_frozen_trial


class TestTrial(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'source.sqlite3'
        self.source = sqlite3.connect(self.path)
        self.addCleanup(lambda: self.source.close())
        initialize_source(self.source)

    def freeze(self, specimens=('X', 'Y')):
        return freeze_trial(self.source, 'materials-17', specimens, 'A', 'B')

    def freeze_count(self):
        return self.source.execute('SELECT count(*) FROM frozen_trials').fetchone()[0]

    def revision_count(self):
        return self.source.execute('SELECT count(*) FROM prediction_revisions').fetchone()[0]

    def test_revise_prediction(self):
        frozen = self.freeze()
        first = record_prediction(self.source, frozen.freeze_id, 'X', 'A', 0, 'first sheet')
        revised = record_prediction(self.source, frozen.freeze_id, 'X', 'A', '1/4', 'corrected sheet')
        self.assertNotEqual(first.revision_id, revised.revision_id)
        current = get_current_prediction(self.source, frozen.freeze_id, 'X', 'A')
        self.assertEqual(current, revised)
        self.assertEqual((current.probability, current.source), (Fraction(1, 4), 'corrected sheet'))
        previous = get_prediction_revision(self.source, first.revision_id)
        self.assertEqual((previous.probability, previous.source), (Fraction(0), 'first sheet'))
        self.assertEqual(get_prediction_revision(self.source, revised.revision_id), revised)
        self.assertEqual(self.revision_count(), 2)

    def test_duplicate_slot(self):
        with self.assertRaises(ValueError):
            self.freeze(('X', 'X'))
        self.assertEqual(self.freeze_count(), 0)
        self.assertEqual(self.source.execute('SELECT count(*) FROM trial_registry').fetchone()[0], 0)

    def test_invalid_prediction(self):
        frozen = self.freeze()
        original = record_prediction(self.source, frozen.freeze_id, 'X', 'A', '1/4', 'valid sheet')
        for invalid in ('5/4', '-1/4', '1/0', True, 0.25):
            with self.subTest(probability=invalid):
                with self.assertRaises(ValueError):
                    record_prediction(self.source, frozen.freeze_id, 'X', 'A', invalid, 'invalid sheet')
                self.assertEqual(get_current_prediction(self.source, frozen.freeze_id, 'X', 'A'), original)
                recovered = get_prediction_revision(self.source, original.revision_id)
                self.assertEqual((recovered.probability, recovered.source), (Fraction(1, 4), 'valid sheet'))
                self.assertEqual(self.revision_count(), 1)

    def test_freeze_isolation(self):
        old = self.freeze(('X', 'Y'))
        newer = self.freeze(('Y', 'X'))
        self.assertNotEqual(old.freeze_id, newer.freeze_id)
        original = record_prediction(self.source, old.freeze_id, 'X', 'A', 0, 'old original')
        old_alpha = record_prediction(self.source, old.freeze_id, 'X', 'A', '1/4', 'old correction')
        old_beta = record_prediction(self.source, old.freeze_id, 'X', 'B', '1/2', 'old beta')
        new_initial = record_prediction(self.source, newer.freeze_id, 'X', 'A', '1/2', 'new original')
        new_beta = record_prediction(self.source, newer.freeze_id, 'X', 'B', 1, 'new beta')
        new_alpha = record_prediction(self.source, newer.freeze_id, 'X', 'A', '3/4', 'new correction')
        for frozen, model, expected in (
            (old, 'A', old_alpha), (old, 'B', old_beta),
            (newer, 'A', new_alpha), (newer, 'B', new_beta),
        ):
            with self.subTest(freeze_id=frozen.freeze_id, model=model):
                self.assertEqual(get_current_prediction(self.source, frozen.freeze_id, 'X', model), expected)
        self.assertEqual(get_prediction_revision(self.source, original.revision_id), original)
        self.assertEqual(get_prediction_revision(self.source, new_initial.revision_id), new_initial)
        self.assertEqual(read_frozen_trial(self.source, old.freeze_id).roster.specimens, ('X', 'Y'))
        self.assertEqual(read_frozen_trial(self.source, newer.freeze_id).roster.specimens, ('Y', 'X'))

    def test_reinitialize_source(self):
        frozen = self.freeze()
        first = record_prediction(self.source, frozen.freeze_id, 'X', 'A', 0, 'first')
        current = record_prediction(self.source, frozen.freeze_id, 'X', 'A', '2/3', 'second')
        initialize_source(self.source)
        self.source.close()
        self.source = sqlite3.connect(self.path)
        initialize_source(self.source)
        self.assertEqual(read_frozen_trial(self.source, frozen.freeze_id), frozen)
        self.assertEqual(get_prediction_revision(self.source, first.revision_id), first)
        self.assertEqual(get_prediction_revision(self.source, current.revision_id), current)
        self.assertEqual(get_current_prediction(self.source, frozen.freeze_id, 'X', 'A'), current)
        self.assertEqual((self.freeze_count(), self.revision_count()), (1, 2))

    def test_revise_preserves_frozen(self):
        original = self.freeze(('Y', 'X'))
        with self.assertRaises(ValueError):
            self.freeze(('Y', 'X', 'X'))
        self.assertEqual(self.freeze_count(), 1)
        self.assertEqual(read_frozen_trial(self.source, original.freeze_id), original)
        self.assertEqual(original.roster.specimens, ('Y', 'X'))
        alternate = freeze_trial(self.source, original.trial_id, ('X', 'Y'), 'B', 'A')
        self.assertEqual(read_frozen_trial(self.source, original.freeze_id).models.ordered, ('A', 'B'))
        self.assertEqual(read_frozen_trial(self.source, alternate.freeze_id).models.ordered, ('B', 'A'))

    def test_invalid_slot(self):
        frozen = self.freeze()
        original = record_prediction(self.source, frozen.freeze_id, 'X', 'A', '1/4', 'valid')
        for freeze_id, specimen, model, error in (
            (frozen.freeze_id, 'Z', 'A', ValueError),
            (frozen.freeze_id, 'X', 'C', ValueError),
            ('absent-freeze', 'X', 'A', KeyError),
        ):
            with self.subTest(freeze_id=freeze_id, specimen=specimen, model=model):
                with self.assertRaises(error):
                    record_prediction(self.source, freeze_id, specimen, model, '3/4', 'rejected')
        self.assertEqual(self.revision_count(), 1)
        self.assertEqual(get_current_prediction(self.source, frozen.freeze_id, 'X', 'A'), original)
        self.assertIsNone(get_current_prediction(self.source, frozen.freeze_id, 'X', 'B'))
