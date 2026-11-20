import json
from dataclasses import asdict, dataclass
from fractions import Fraction

from analysis.comparison import ComparisonResult
from bench.codec import HandoffBody


@dataclass(frozen=True)
class ReportPair:
    specimen_id: str
    label_id: str
    label: int
    label_source: str
    alpha_revision_id: str
    alpha_source: str
    beta_revision_id: str
    beta_source: str


@dataclass(frozen=True)
class ReportBody:
    acceptance_id: str
    handoff_id: str
    trial_id: str
    freeze_id: str
    alpha_model: str
    beta_model: str
    pairs: tuple[ReportPair, ...]
    pair_count: int
    mean_difference: Fraction | None


@dataclass(frozen=True)
class SampleReportBody:
    acceptance_id: str
    handoff_id: str
    trial_id: str
    freeze_id: str
    alpha_model: str
    beta_model: str
    draw_id: str
    population_count: int
    sample: tuple[str, ...]
    pairs: tuple[ReportPair, ...]
    mean_difference: Fraction
    variance_status: str
    variance_method: str | None
    variance_estimate: Fraction | None


def build_report_body(
    acceptance_id: str, handoff_id: str, handoff: HandoffBody, result: ComparisonResult,
) -> ReportBody:
    pairs = tuple(
        ReportPair(
            specimen_id=pair.specimen_id,
            label_id=pair.label.label_id,
            label=pair.label.value,
            label_source=pair.label.source,
            alpha_revision_id=pair.alpha.revision_id,
            alpha_source=pair.alpha.source,
            beta_revision_id=pair.beta.revision_id,
            beta_source=pair.beta.source,
        )
        for pair in result.pairs
    )
    return ReportBody(
        acceptance_id=acceptance_id,
        handoff_id=handoff_id,
        trial_id=handoff.trial_id,
        freeze_id=handoff.freeze_id,
        alpha_model=handoff.models.alpha,
        beta_model=handoff.models.beta,
        pairs=pairs,
        pair_count=result.pair_count,
        mean_difference=result.mean_difference,
    )


def _rational_payload(value: Fraction | None) -> list[int] | None:
    return None if value is None else [value.numerator, value.denominator]


def encode_report_body(body: ReportBody | SampleReportBody) -> str:
    payload = asdict(body)
    if isinstance(body, SampleReportBody):
        payload['version'] = 2
        payload['mean_difference'] = _rational_payload(body.mean_difference)
        payload['variance_estimate'] = _rational_payload(body.variance_estimate)
    else:
        payload['mean_difference'] = _rational_payload(body.mean_difference)
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _decode_pairs(payload: dict) -> tuple[ReportPair, ...]:
    return tuple(ReportPair(**pair) for pair in payload.pop('pairs'))


def _decode_sample_report_body(payload: dict) -> SampleReportBody:
    payload.pop('version')
    pairs = _decode_pairs(payload)
    difference = payload.pop('mean_difference')
    estimate = payload.pop('variance_estimate')
    sample = tuple(payload.pop('sample'))
    return SampleReportBody(
        **payload,
        sample=sample,
        pairs=pairs,
        mean_difference=Fraction(*difference),
        variance_estimate=None if estimate is None else Fraction(*estimate),
    )


def decode_report_body(body_json: str) -> ReportBody | SampleReportBody:
    payload = json.loads(body_json)
    if payload.get('version') == 2:
        return _decode_sample_report_body(payload)
    pairs = _decode_pairs(payload)
    difference = payload.pop('mean_difference')
    return ReportBody(
        **payload,
        pairs=pairs,
        mean_difference=None if difference is None else Fraction(*difference),
    )
