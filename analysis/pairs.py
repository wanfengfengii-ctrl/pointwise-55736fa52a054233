from dataclasses import dataclass

from bench.codec import HandoffBody, HandoffLabel, HandoffPrediction


@dataclass(frozen=True)
class PairedSpecimen:
    specimen_id: str
    label: HandoffLabel
    alpha: HandoffPrediction
    beta: HandoffPrediction


def match_pairs(body: HandoffBody) -> tuple[PairedSpecimen, ...]:
    labels = {label.specimen_id: label for label in body.labels}
    predictions = {
        (prediction.specimen_id, prediction.model_id): prediction
        for prediction in body.predictions
    }
    pairs = []
    for specimen_id in body.roster.specimens:
        alpha = predictions.get((specimen_id, body.models.alpha))
        beta = predictions.get((specimen_id, body.models.beta))
        if alpha is not None and beta is not None:
            pairs.append(PairedSpecimen(specimen_id, labels[specimen_id], alpha, beta))
    return tuple(pairs)
