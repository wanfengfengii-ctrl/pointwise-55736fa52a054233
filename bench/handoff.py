import sqlite3

from predictions.history import get_prediction_revision
from trial.freeze import read_frozen_trial
from trial.identities import require_identifier
from trial.registry import write_transaction

from .codec import HandoffLabel, HandoffPrediction, encode_handoff
from .issued import IssuedHandoff, load_issued_handoff, save_issued_handoff
from .labels import load_final_labels
from .sample_codec import SampleHandoffBody, encode_sample_handoff
from .sampling import lock_sample, load_sample_lock
from .settlement import build_settled_snapshot

__all__ = ['issue_handoff', 'issue_sample_handoff', 'lock_sample', 'read_issued_handoff']


def issue_handoff(connection: sqlite3.Connection, freeze_id: str) -> IssuedHandoff:
    with write_transaction(connection):
        frozen = read_frozen_trial(connection, freeze_id)
        body = build_settled_snapshot(connection, frozen)
        return save_issued_handoff(connection, frozen.freeze_id, encode_handoff(body))


def issue_sample_handoff(connection: sqlite3.Connection, freeze_id: str) -> IssuedHandoff:
    """Issue a V2 handoff carrying only the locked sample's final labels."""
    with write_transaction(connection):
        frozen = read_frozen_trial(connection, freeze_id)
        lock = load_sample_lock(connection, frozen.freeze_id)
        if lock is None:
            raise ValueError('freeze has no locked sample draw')
        final_labels = load_final_labels(connection, frozen.freeze_id)
        missing = [specimen_id for specimen_id in lock.sample if specimen_id not in final_labels]
        if missing:
            raise ValueError(f'missing final labels for {missing!r}')
        labels = tuple(
            HandoffLabel(label.specimen_id, label.label_id, label.value, label.source)
            for label in (final_labels[specimen_id] for specimen_id in lock.sample)
        )
        predictions = []
        for member in lock.members:
            for revision_id in (member.alpha_revision_id, member.beta_revision_id):
                revision = get_prediction_revision(connection, revision_id)
                predictions.append(HandoffPrediction(
                    revision.specimen_id, revision.model_id, revision.revision_id,
                    revision.probability, revision.source,
                ))
        body = SampleHandoffBody(
            frozen.trial_id, frozen.freeze_id, lock.population, frozen.models,
            labels, tuple(predictions), lock.draw_id, lock.design, lock.draw, lock.sample,
        )
        return save_issued_handoff(connection, frozen.freeze_id, encode_sample_handoff(body))


def read_issued_handoff(connection: sqlite3.Connection, handoff_id: str) -> IssuedHandoff:
    handoff_id = require_identifier(handoff_id, 'handoff id')
    return load_issued_handoff(connection, handoff_id)
