import json
from dataclasses import dataclass
from fractions import Fraction

from predictions.probability import probability_from_parts, probability_to_parts
from trial.identities import ModelPair, build_model_pair, require_identifier, require_model
from trial.roster import OrderedRoster, build_roster, require_specimen


@dataclass(frozen=True)
class HandoffLabel:
    specimen_id: str
    label_id: str
    value: int
    source: str


@dataclass(frozen=True)
class HandoffPrediction:
    specimen_id: str
    model_id: str
    revision_id: str
    probability: Fraction
    source: str


@dataclass(frozen=True)
class HandoffBody:
    trial_id: str
    freeze_id: str
    roster: OrderedRoster
    models: ModelPair
    labels: tuple[HandoffLabel, ...]
    predictions: tuple[HandoffPrediction, ...]


def require_binary_label(value: object) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError('final label must be the integer zero or one')
    return value


def encode_handoff(body: HandoffBody) -> str:
    payload = {
        'version': 1,
        'trial_id': body.trial_id,
        'freeze_id': body.freeze_id,
        'specimens': body.roster.specimens,
        'models': {'alpha': body.models.alpha, 'beta': body.models.beta},
        'labels': [
            {'specimen_id': label.specimen_id, 'label_id': label.label_id,
             'value': label.value, 'source': label.source}
            for label in body.labels
        ],
        'predictions': [
            {'specimen_id': prediction.specimen_id, 'model_id': prediction.model_id,
             'revision_id': prediction.revision_id, 'source': prediction.source,
             'probability': probability_to_parts(prediction.probability)}
            for prediction in body.predictions
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _distinct_object(entries: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in entries:
        if name in result:
            raise ValueError(f'duplicate JSON property {name!r}')
        result[name] = value
    return result


def _object(value: object, *fields: str) -> dict:
    if not isinstance(value, dict) or value.keys() != set(fields):
        raise ValueError(f'expected JSON object with fields {fields!r}')
    return value


def _array(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f'{field} must be a JSON array')
    return value


def _record_id(value: object, field: str, seen: set[str]) -> str:
    record_id = require_identifier(value, field)
    if record_id in seen:
        raise ValueError(f'duplicate record identity {record_id!r}')
    seen.add(record_id)
    return record_id


def _decode_labels(
    values: object, roster: OrderedRoster, seen: set[str],
) -> tuple[HandoffLabel, ...]:
    labels = {}
    for value in _array(values, 'labels'):
        row = _object(value, 'specimen_id', 'label_id', 'value', 'source')
        specimen_id = require_specimen(roster, row['specimen_id'])
        if specimen_id in labels:
            raise ValueError(f'duplicate label slot for {specimen_id!r}')
        labels[specimen_id] = HandoffLabel(
            specimen_id,
            _record_id(row['label_id'], 'label id', seen),
            require_binary_label(row['value']),
            require_identifier(row['source'], 'label source'),
        )
    if len(labels) != len(roster.specimens):
        raise ValueError('handoff must contain every final label')
    return tuple(labels[specimen_id] for specimen_id in roster.specimens)


def _decode_predictions(
    values: object, roster: OrderedRoster, models: ModelPair, seen: set[str],
) -> tuple[HandoffPrediction, ...]:
    predictions = {}
    for value in _array(values, 'predictions'):
        row = _object(value, 'specimen_id', 'model_id', 'revision_id', 'probability', 'source')
        specimen_id = require_specimen(roster, row['specimen_id'])
        model_id = require_model(models, row['model_id'])
        slot = specimen_id, model_id
        if slot in predictions:
            raise ValueError(f'duplicate prediction slot {slot!r}')
        parts = _array(row['probability'], 'probability')
        if len(parts) != 2:
            raise ValueError('probability requires exactly two integer parts')
        predictions[slot] = HandoffPrediction(
            specimen_id,
            model_id,
            _record_id(row['revision_id'], 'prediction revision id', seen),
            probability_from_parts(parts[0], parts[1]),
            require_identifier(row['source'], 'prediction source'),
        )
    ordered = []
    for specimen_id in roster.specimens:
        for model_id in models.ordered:
            prediction = predictions.get((specimen_id, model_id))
            if prediction is not None:
                ordered.append(prediction)
    return tuple(ordered)


def decode_handoff(body_json: str) -> HandoffBody:
    """Validate and normalize a standalone V1 handoff."""
    if not isinstance(body_json, str):
        raise ValueError('handoff body must be JSON text')
    try:
        value = json.loads(body_json, object_pairs_hook=_distinct_object)
    except RecursionError as error:
        raise ValueError('handoff JSON nesting is excessive') from error
    row = _object(value, 'version', 'trial_id', 'freeze_id', 'specimens', 'models', 'labels', 'predictions')
    if type(row['version']) is not int or row['version'] != 1:
        raise ValueError('unsupported handoff version')
    trial_id = require_identifier(row['trial_id'], 'trial')
    freeze_id = require_identifier(row['freeze_id'], 'freeze id')
    roster = build_roster(_array(row['specimens'], 'specimens'))
    model_row = _object(row['models'], 'alpha', 'beta')
    models = build_model_pair(model_row['alpha'], model_row['beta'])
    seen: set[str] = set()
    labels = _decode_labels(row['labels'], roster, seen)
    predictions = _decode_predictions(row['predictions'], roster, models, seen)
    return HandoffBody(trial_id, freeze_id, roster, models, labels, predictions)
