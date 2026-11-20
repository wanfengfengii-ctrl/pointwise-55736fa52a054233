import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .identities import require_identifier


@contextmanager
def write_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Own a write transaction, including rollback after a failed commit."""
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield
        connection.commit()
    except BaseException as failure:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            failure.add_note(f'SQLite rollback also failed: {rollback_error}')
        raise


def initialize_trial_registry(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS trial_registry (
            trial_id TEXT NOT NULL PRIMARY KEY
        )
    ''')


def ensure_trial(connection: sqlite3.Connection, trial_id: str) -> None:
    trial_id = require_identifier(trial_id, 'trial')
    connection.execute(
        'INSERT INTO trial_registry (trial_id) VALUES (?) ON CONFLICT (trial_id) DO NOTHING',
        (trial_id,),
    )
