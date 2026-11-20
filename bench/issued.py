import sqlite3
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class IssuedHandoff:
    handoff_id: str
    freeze_id: str
    body_json: str


def initialize_issued_handoffs(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issued_handoffs (
            handoff_id TEXT NOT NULL PRIMARY KEY,
            freeze_id TEXT NOT NULL REFERENCES frozen_trials(freeze_id),
            body_json TEXT NOT NULL
        )
    ''')


def save_issued_handoff(
    connection: sqlite3.Connection, freeze_id: str, body_json: str,
) -> IssuedHandoff:
    issued = IssuedHandoff(f'handoff-{uuid4().hex}', freeze_id, body_json)
    connection.execute(
        'INSERT INTO issued_handoffs (handoff_id, freeze_id, body_json) VALUES (?, ?, ?)',
        (issued.handoff_id, issued.freeze_id, issued.body_json),
    )
    return issued


def load_issued_handoff(connection: sqlite3.Connection, handoff_id: str) -> IssuedHandoff:
    row = connection.execute(
        'SELECT handoff_id, freeze_id, body_json FROM issued_handoffs WHERE handoff_id = ?',
        (handoff_id,),
    ).fetchone()
    if row is None:
        raise KeyError(handoff_id)
    return IssuedHandoff(*row)
