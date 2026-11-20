import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from uuid import uuid4

from trial.identities import require_identifier

from .probability import probability_from_parts, probability_to_parts


@dataclass(frozen=True)
class PredictionRevision:
    revision_id: str
    freeze_id: str
    specimen_id: str
    model_id: str
    probability: Fraction
    source: str


def initialize_prediction_history(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS prediction_revisions (
            revision_id TEXT NOT NULL PRIMARY KEY,
            freeze_id TEXT NOT NULL,
            specimen_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            numerator TEXT NOT NULL,
            denominator TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (freeze_id, specimen_id)
                REFERENCES frozen_specimens(freeze_id, specimen_id),
            UNIQUE (freeze_id, specimen_id, model_id, revision_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS prediction_heads (
            freeze_id TEXT NOT NULL,
            specimen_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            PRIMARY KEY (freeze_id, specimen_id, model_id),
            FOREIGN KEY (freeze_id, specimen_id, model_id, revision_id)
                REFERENCES prediction_revisions(freeze_id, specimen_id, model_id, revision_id)
        )
    ''')


def append_prediction_revision(
    connection: sqlite3.Connection,
    freeze_id: str,
    specimen_id: str,
    model_id: str,
    probability: Fraction,
    source: str,
) -> PredictionRevision:
    revision_id = f'prediction-{uuid4().hex}'
    numerator, denominator = probability_to_parts(probability)
    connection.execute(
        '''INSERT INTO prediction_revisions
               (revision_id, freeze_id, specimen_id, model_id, numerator, denominator, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (revision_id, freeze_id, specimen_id, model_id, str(numerator), str(denominator), source),
    )
    connection.execute(
        '''INSERT INTO prediction_heads (freeze_id, specimen_id, model_id, revision_id)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (freeze_id, specimen_id, model_id)
           DO UPDATE SET revision_id = excluded.revision_id''',
        (freeze_id, specimen_id, model_id, revision_id),
    )
    return PredictionRevision(revision_id, freeze_id, specimen_id, model_id, probability, source)


_REVISION_COLUMNS = '''
    r.revision_id, r.freeze_id, r.specimen_id, r.model_id, r.numerator, r.denominator, r.source
'''
_HEAD_QUERY = f'''
    SELECT {_REVISION_COLUMNS}
    FROM prediction_heads AS h
    JOIN prediction_revisions AS r ON r.revision_id = h.revision_id
'''


def _revision_from_row(row: tuple) -> PredictionRevision:
    revision_id, freeze_id, specimen_id, model_id, numerator, denominator, source = row
    probability = probability_from_parts(int(numerator), int(denominator))
    return PredictionRevision(revision_id, freeze_id, specimen_id, model_id, probability, source)


def get_prediction_revision(
    connection: sqlite3.Connection, revision_id: str,
) -> PredictionRevision:
    revision_id = require_identifier(revision_id, 'prediction revision id')
    row = connection.execute(
        f'SELECT {_REVISION_COLUMNS} FROM prediction_revisions AS r WHERE r.revision_id = ?',
        (revision_id,),
    ).fetchone()
    if row is None:
        raise KeyError(revision_id)
    return _revision_from_row(row)


def get_current_prediction(
    connection: sqlite3.Connection, freeze_id: str, specimen_id: str, model_id: str,
) -> PredictionRevision | None:
    row = connection.execute(
        _HEAD_QUERY + ' WHERE h.freeze_id = ? AND h.specimen_id = ? AND h.model_id = ?',
        (freeze_id, specimen_id, model_id),
    ).fetchone()
    return None if row is None else _revision_from_row(row)


def load_current_predictions(
    connection: sqlite3.Connection, freeze_id: str,
) -> tuple[PredictionRevision, ...]:
    rows = connection.execute(_HEAD_QUERY + ' WHERE h.freeze_id = ?', (freeze_id,))
    return tuple(_revision_from_row(row) for row in rows)
