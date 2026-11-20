import sqlite3
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class AcceptedHandoff:
    acceptance_id: str
    handoff_id: str
    body_json: str


def initialize_acceptances(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS accepted_handoffs (
            acceptance_id TEXT NOT NULL PRIMARY KEY,
            handoff_id TEXT NOT NULL UNIQUE,
            body_json TEXT NOT NULL
        )
    ''')


def insert_first_acceptance(
    connection: sqlite3.Connection, handoff_id: str, body_json: str,
) -> AcceptedHandoff:
    accepted = AcceptedHandoff(f'acceptance-{uuid4().hex}', handoff_id, body_json)
    connection.execute(
        'INSERT INTO accepted_handoffs (acceptance_id, handoff_id, body_json) VALUES (?, ?, ?)',
        (accepted.acceptance_id, accepted.handoff_id, accepted.body_json),
    )
    return accepted


def get_by_handoff_id(
    connection: sqlite3.Connection, handoff_id: str,
) -> AcceptedHandoff | None:
    row = connection.execute(
        'SELECT acceptance_id, handoff_id, body_json FROM accepted_handoffs WHERE handoff_id = ?',
        (handoff_id,),
    ).fetchone()
    return None if row is None else AcceptedHandoff(*row)


def get_by_acceptance_id(
    connection: sqlite3.Connection, acceptance_id: str,
) -> AcceptedHandoff | None:
    row = connection.execute(
        'SELECT acceptance_id, handoff_id, body_json FROM accepted_handoffs WHERE acceptance_id = ?',
        (acceptance_id,),
    ).fetchone()
    return None if row is None else AcceptedHandoff(*row)
