import sqlite3

from trial.revisions import FrozenTrial

from .history import PredictionRevision, load_current_predictions


def select_current_predictions(
    connection: sqlite3.Connection, frozen: FrozenTrial,
) -> tuple[PredictionRevision, ...]:
    """Select occupied slots in the frozen specimen and model order."""
    heads = {
        (revision.specimen_id, revision.model_id): revision
        for revision in load_current_predictions(connection, frozen.freeze_id)
    }
    selected = []
    for specimen_id in frozen.roster.specimens:
        for model_id in frozen.models.ordered:
            revision = heads.get((specimen_id, model_id))
            if revision is not None:
                selected.append(revision)
    return tuple(selected)
