import sqlite3
from collections.abc import Sequence

from .identities import build_model_pair, require_identifier
from .registry import ensure_trial, write_transaction
from .revisions import FrozenTrial, append_frozen_trial, load_frozen_trial
from .roster import build_roster


def freeze_trial(
    connection: sqlite3.Connection,
    trial_id: str,
    specimens: Sequence[str],
    alpha_model: str,
    beta_model: str,
) -> FrozenTrial:
    """Append an independent roster and model pair for one trial."""
    trial_id = require_identifier(trial_id, 'trial')
    roster = build_roster(specimens)
    models = build_model_pair(alpha_model, beta_model)
    with write_transaction(connection):
        ensure_trial(connection, trial_id)
        return append_frozen_trial(connection, trial_id, roster, models)


def read_frozen_trial(connection: sqlite3.Connection, freeze_id: str) -> FrozenTrial:
    freeze_id = require_identifier(freeze_id, 'freeze id')
    return load_frozen_trial(connection, freeze_id)
