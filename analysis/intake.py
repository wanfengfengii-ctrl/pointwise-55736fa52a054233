import sqlite3

from bench.codec import HandoffBody, decode_handoff, encode_handoff
from trial.identities import require_identifier
from trial.registry import write_transaction

from .accepted import AcceptedHandoff, get_by_acceptance_id, get_by_handoff_id, insert_first_acceptance


def accept_handoff(connection: sqlite3.Connection, handoff_id: str, body_json: str) -> str:
    handoff_id = require_identifier(handoff_id, 'handoff id')
    canonical_body = encode_handoff(decode_handoff(body_json))
    with write_transaction(connection):
        first = get_by_handoff_id(connection, handoff_id)
        if first is None:
            return insert_first_acceptance(connection, handoff_id, canonical_body).acceptance_id
        if first.body_json != canonical_body:
            raise ValueError(f'conflicting body for handoff {handoff_id!r}')
        return first.acceptance_id


def read_acceptance(connection: sqlite3.Connection, acceptance_id: str) -> AcceptedHandoff:
    acceptance_id = require_identifier(acceptance_id, 'acceptance id')
    accepted = get_by_acceptance_id(connection, acceptance_id)
    if accepted is None:
        raise KeyError(acceptance_id)
    return accepted


def load_accepted_body(
    connection: sqlite3.Connection, handoff_id: str,
) -> tuple[AcceptedHandoff, HandoffBody]:
    handoff_id = require_identifier(handoff_id, 'handoff id')
    accepted = get_by_handoff_id(connection, handoff_id)
    if accepted is None:
        raise KeyError(handoff_id)
    return accepted, decode_handoff(accepted.body_json)
