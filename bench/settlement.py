import sqlite3

from predictions.selection import select_current_predictions
from trial.revisions import FrozenTrial

from .codec import HandoffBody, HandoffLabel, HandoffPrediction
from .labels import load_final_labels


def build_settled_snapshot(
    connection: sqlite3.Connection, frozen: FrozenTrial,
) -> HandoffBody:
    """Assemble label coverage and current predictions inside the issuance transaction."""
    final_labels = load_final_labels(connection, frozen.freeze_id)
    missing = [specimen_id for specimen_id in frozen.roster.specimens
               if specimen_id not in final_labels]
    if missing:
        raise ValueError(f'missing final labels for {missing!r}')

    label_rows = (final_labels[specimen_id] for specimen_id in frozen.roster.specimens)
    labels = tuple(
        HandoffLabel(label.specimen_id, label.label_id, label.value, label.source)
        for label in label_rows
    )
    predictions = tuple(
        HandoffPrediction(
            revision.specimen_id, revision.model_id, revision.revision_id,
            revision.probability, revision.source,
        )
        for revision in select_current_predictions(connection, frozen)
    )
    return HandoffBody(frozen.trial_id, frozen.freeze_id, frozen.roster, frozen.models, labels, predictions)
