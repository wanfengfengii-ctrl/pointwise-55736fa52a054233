import sqlite3

from trial.freeze import read_frozen_trial
from trial.identities import require_identifier, require_model
from trial.registry import write_transaction
from trial.roster import require_specimen

from .history import PredictionRevision, append_prediction_revision
from .probability import ProbabilityInput, parse_probability


def record_prediction(
    connection: sqlite3.Connection,
    freeze_id: str,
    specimen_id: str,
    model_id: str,
    probability: ProbabilityInput,
    source: str,
) -> PredictionRevision:
    frozen = read_frozen_trial(connection, freeze_id)
    specimen_id = require_specimen(frozen.roster, specimen_id)
    model_id = require_model(frozen.models, model_id)
    probability = parse_probability(probability)
    source = require_identifier(source, 'prediction source')
    with write_transaction(connection):
        return append_prediction_revision(
            connection, frozen.freeze_id, specimen_id, model_id, probability, source,
        )
