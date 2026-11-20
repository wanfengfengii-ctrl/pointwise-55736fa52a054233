import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from trial.freeze import read_frozen_trial
from trial.identities import require_identifier
from trial.registry import write_transaction
from trial.roster import require_specimen

from .codec import require_binary_label


@dataclass(frozen=True)
class FinalLabel:
    label_id: str
    freeze_id: str
    specimen_id: str
    value: int
    source: str


def initialize_labels(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS final_labels (
            label_id TEXT NOT NULL PRIMARY KEY,
            freeze_id TEXT NOT NULL,
            specimen_id TEXT NOT NULL,
            value INTEGER NOT NULL CHECK (value IN (0, 1)),
            source TEXT NOT NULL,
            UNIQUE (freeze_id, specimen_id),
            FOREIGN KEY (freeze_id, specimen_id)
                REFERENCES frozen_specimens(freeze_id, specimen_id)
        )
    ''')


def record_label(
    connection: sqlite3.Connection,
    freeze_id: str,
    specimen_id: str,
    value: int,
    source: str,
) -> FinalLabel:
    frozen = read_frozen_trial(connection, freeze_id)
    specimen_id = require_specimen(frozen.roster, specimen_id)
    value = require_binary_label(value)
    source = require_identifier(source, 'label source')
    with write_transaction(connection):
        row = connection.execute(
            '''SELECT label_id, freeze_id, specimen_id, value, source FROM final_labels
               WHERE freeze_id = ? AND specimen_id = ?''',
            (frozen.freeze_id, specimen_id),
        ).fetchone()
        if row is not None:
            label = FinalLabel(*row)
            if (label.value, label.source) != (value, source):
                raise ValueError(f'conflicting final label for {specimen_id!r}')
            return label

        label = FinalLabel(f'label-{uuid4().hex}', frozen.freeze_id, specimen_id, value, source)
        connection.execute(
            '''INSERT INTO final_labels (label_id, freeze_id, specimen_id, value, source)
               VALUES (?, ?, ?, ?, ?)''',
            (label.label_id, label.freeze_id, label.specimen_id, label.value, label.source),
        )
        return label


def load_final_labels(
    connection: sqlite3.Connection, freeze_id: str,
) -> dict[str, FinalLabel]:
    rows = connection.execute(
        'SELECT label_id, freeze_id, specimen_id, value, source FROM final_labels WHERE freeze_id = ?',
        (freeze_id,),
    )
    labels = (FinalLabel(*row) for row in rows)
    return {label.specimen_id: label for label in labels}
