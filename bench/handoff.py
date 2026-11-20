import sqlite3

from trial.freeze import read_frozen_trial
from trial.identities import require_identifier
from trial.registry import write_transaction

from .codec import encode_handoff
from .issued import IssuedHandoff, load_issued_handoff, save_issued_handoff
from .settlement import build_settled_snapshot


def issue_handoff(connection: sqlite3.Connection, freeze_id: str) -> IssuedHandoff:
    with write_transaction(connection):
        frozen = read_frozen_trial(connection, freeze_id)
        body = build_settled_snapshot(connection, frozen)
        return save_issued_handoff(connection, frozen.freeze_id, encode_handoff(body))


def read_issued_handoff(connection: sqlite3.Connection, handoff_id: str) -> IssuedHandoff:
    handoff_id = require_identifier(handoff_id, 'handoff id')
    return load_issued_handoff(connection, handoff_id)
