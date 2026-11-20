import json
from dataclasses import dataclass
from fractions import Fraction

from predictions.probability import probability_from_parts, probability_to_parts
from trial.identities import ModelPair, build_model_pair, require_identifier, require_model
from trial.roster import OrderedRoster, build_roster, require_specimen

from .codec import (
    HandoffBody,
    HandoffLabel,
    HandoffPrediction,
    _array,
    _distinct_object,
    _object,
    _record_id,
    decode_handoff,
    encode_handoff,
    require_binary_label,
)
from .sampling import (
    DesignEntry,
    ordered_sample,
    parse_design,
    require_design_coverage,
    select_sample_entry,
)


@dataclass(frozen=True)
class SampleHandoffBody:
    trial_id: str
    freeze_id: str
    population: tuple[str, ...]
    models: ModelPair
    labels: tuple[HandoffLabel, ...]
    predictions: tuple[HandoffPrediction, ...]
    draw_id: str
    design: tuple[DesignEntry, ...]
    draw: Fraction
    sample: tuple[str, ...]


def rational_to_parts(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def rational_from_parts(numerator: object, denominator: object, field: str) -> Fraction:
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError(f'{field} requires an integer numerator and positive integer denominator')
    return Fraction(numerator, denominator)


def encode_sample_handoff(body: SampleHandoffBody) -> str:
    order = {specimen_id: position for position, specimen_id in enumerate(body.population)}
    payload = {
        'version': 2,
        'trial_id': body.trial_id,
        'freeze_id': body.freeze_id,
        'specimens': list(body.population),
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
        'draw_id': body.draw_id,
        'design': [
            [sorted(entry.members, key=order.__getitem__), rational_to_parts(entry.weight)]
            for entry in body.design
        ],
        'draw': rational_to_parts(body.draw),
        'sample': list(body.sample),
    }
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _decode_design(value: object, population: OrderedRoster) -> tuple[DesignEntry, ...]:
    entries = []
    for item in _array(value, 'design'):
        pair = _array(item, 'design entry')
        if len(pair) != 2:
            raise ValueError('design entry must be a [specimens, weight] pair')
        parts = _array(pair[1], 'design weight')
        if len(parts) != 2:
            raise ValueError('design weight requires exactly two integer parts')
        entries.append((pair[0], rational_from_parts(parts[0], parts[1], 'design weight')))
    design = parse_design(entries)
    require_design_coverage(design, population.specimens)
    return design


def _decode_draw(value: object) -> Fraction:
    parts = _array(value, 'draw')
    if len(parts) != 2:
        raise ValueError('draw requires exactly two integer parts')
    draw = rational_from_parts(parts[0], parts[1], 'draw')
    if not 0 <= draw < 1:
        raise ValueError('draw must be a rational in [0, 1)')
    return draw


def _decode_sample(
    value: object,
    population: OrderedRoster,
    design: tuple[DesignEntry, ...],
    draw: Fraction,
) -> tuple[str, ...]:
    members = tuple(require_specimen(population, item) for item in _array(value, 'sample'))
    if len(set(members)) != len(members):
        raise ValueError('duplicate specimen in sample')
    if members != ordered_sample(population.specimens, select_sample_entry(design, draw)):
        raise ValueError('sample does not match the design and draw')
    return members


def _decode_sample_labels(
    values: object, population: OrderedRoster, sample: tuple[str, ...], seen: set[str],
) -> tuple[HandoffLabel, ...]:
    sampled = set(sample)
    labels = {}
    for value in _array(values, 'labels'):
        row = _object(value, 'specimen_id', 'label_id', 'value', 'source')
        specimen_id = require_specimen(population, row['specimen_id'])
        if specimen_id not in sampled:
            raise ValueError(f'label for unsampled specimen {specimen_id!r}')
        if specimen_id in labels:
            raise ValueError(f'duplicate label slot for {specimen_id!r}')
        labels[specimen_id] = HandoffLabel(
            specimen_id,
            _record_id(row['label_id'], 'label id', seen),
            require_binary_label(row['value']),
            require_identifier(row['source'], 'label source'),
        )
    if len(labels) != len(sample):
        raise ValueError('handoff must contain every sampled final label')
    return tuple(labels[specimen_id] for specimen_id in sample)


def _decode_sample_predictions(
    values: object, population: OrderedRoster, models: ModelPair, seen: set[str],
) -> tuple[HandoffPrediction, ...]:
    predictions = {}
    for value in _array(values, 'predictions'):
        row = _object(value, 'specimen_id', 'model_id', 'revision_id', 'probability', 'source')
        specimen_id = require_specimen(population, row['specimen_id'])
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
    for specimen_id in population.specimens:
        for model_id in models.ordered:
            prediction = predictions.get((specimen_id, model_id))
            if prediction is None:
                raise ValueError(f'missing locked prediction for slot {(specimen_id, model_id)!r}')
            ordered.append(prediction)
    return tuple(ordered)


def decode_sample_handoff(body_json: str) -> SampleHandoffBody:
    """Validate and normalize a standalone V2 sampled handoff."""
    if not isinstance(body_json, str):
        raise ValueError('handoff body must be JSON text')
    try:
        value = json.loads(body_json, object_pairs_hook=_distinct_object)
    except RecursionError as error:
        raise ValueError('handoff JSON nesting is excessive') from error
    row = _object(value, 'version', 'trial_id', 'freeze_id', 'specimens', 'models', 'labels',
                  'predictions', 'draw_id', 'design', 'draw', 'sample')
    if type(row['version']) is not int or row['version'] != 2:
        raise ValueError('unsupported handoff version')
    trial_id = require_identifier(row['trial_id'], 'trial')
    freeze_id = require_identifier(row['freeze_id'], 'freeze id')
    population = build_roster(_array(row['specimens'], 'specimens'))
    model_row = _object(row['models'], 'alpha', 'beta')
    models = build_model_pair(model_row['alpha'], model_row['beta'])
    draw_id = require_identifier(row['draw_id'], 'draw id')
    design = _decode_design(row['design'], population)
    draw = _decode_draw(row['draw'])
    sample = _decode_sample(row['sample'], population, design, draw)
    seen: set[str] = set()
    labels = _decode_sample_labels(row['labels'], population, sample, seen)
    predictions = _decode_sample_predictions(row['predictions'], population, models, seen)
    return SampleHandoffBody(
        trial_id, freeze_id, population.specimens, models, labels, predictions,
        draw_id, design, draw, sample,
    )


def _peek_version(body_json: str) -> int | None:
    if not isinstance(body_json, str):
        raise ValueError('handoff body must be JSON text')
    try:
        value = json.loads(body_json, object_pairs_hook=_distinct_object)
    except RecursionError as error:
        raise ValueError('handoff JSON nesting is excessive') from error
    if isinstance(value, dict):
        version = value.get('version')
        if type(version) is int:
            return version
    return None


def decode_any_handoff(body_json: str) -> HandoffBody | SampleHandoffBody:
    if _peek_version(body_json) == 2:
        return decode_sample_handoff(body_json)
    return decode_handoff(body_json)


def encode_any_handoff(body: HandoffBody | SampleHandoffBody) -> str:
    if isinstance(body, SampleHandoffBody):
        return encode_sample_handoff(body)
    return encode_handoff(body)
