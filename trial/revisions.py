import sqlite3
from dataclasses import dataclass
from uuid import uuid4

from .identities import ModelPair, build_model_pair
from .roster import OrderedRoster, build_roster


@dataclass(frozen=True)
class FrozenTrial:
    freeze_id: str
    trial_id: str
    roster: OrderedRoster
    models: ModelPair


def initialize_trial_revisions(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS frozen_trials (
            freeze_id TEXT NOT NULL PRIMARY KEY,
            trial_id TEXT NOT NULL REFERENCES trial_registry(trial_id),
            alpha_model TEXT NOT NULL,
            beta_model TEXT NOT NULL,
            CHECK (alpha_model <> beta_model)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS frozen_specimens (
            freeze_id TEXT NOT NULL REFERENCES frozen_trials(freeze_id),
            position INTEGER NOT NULL CHECK (position >= 0),
            specimen_id TEXT NOT NULL,
            PRIMARY KEY (freeze_id, specimen_id),
            UNIQUE (freeze_id, position)
        )
    ''')


def append_frozen_trial(
    connection: sqlite3.Connection,
    trial_id: str,
    roster: OrderedRoster,
    models: ModelPair,
) -> FrozenTrial:
    frozen = FrozenTrial(f'freeze-{uuid4().hex}', trial_id, roster, models)
    connection.execute(
        '''INSERT INTO frozen_trials (freeze_id, trial_id, alpha_model, beta_model)
           VALUES (?, ?, ?, ?)''',
        (frozen.freeze_id, frozen.trial_id, models.alpha, models.beta),
    )
    connection.executemany(
        '''INSERT INTO frozen_specimens (freeze_id, position, specimen_id)
           VALUES (?, ?, ?)''',
        [(frozen.freeze_id, position, specimen_id)
         for position, specimen_id in enumerate(roster.specimens)],
    )
    return frozen


def load_frozen_trial(connection: sqlite3.Connection, freeze_id: str) -> FrozenTrial:
    row = connection.execute(
        'SELECT trial_id, alpha_model, beta_model FROM frozen_trials WHERE freeze_id = ?',
        (freeze_id,),
    ).fetchone()
    if row is None:
        raise KeyError(freeze_id)
    trial_id, alpha_model, beta_model = row
    rows = connection.execute(
        'SELECT specimen_id FROM frozen_specimens WHERE freeze_id = ? ORDER BY position',
        (freeze_id,),
    )
    roster = build_roster([specimen_id for (specimen_id,) in rows])
    return FrozenTrial(freeze_id, trial_id, roster, build_model_pair(alpha_model, beta_model))
