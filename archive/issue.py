import sqlite3
from uuid import uuid4

from analysis.accepted import get_by_acceptance_id
from analysis.comparison import compare_models
from analysis.intake import load_accepted_body
from analysis.sample_comparison import estimate_sample_difference
from bench.sample_codec import SampleHandoffBody, decode_any_handoff
from trial.identities import require_identifier

from .body import ReportPair, SampleReportBody, build_report_body
from .receipts import build_receipt
from .storage import IssuedReport, persist_issuance


def issue_report(connection: sqlite3.Connection, handoff_id: str) -> IssuedReport:
    accepted, handoff = load_accepted_body(connection, handoff_id)
    if isinstance(handoff, SampleHandoffBody):
        raise ValueError('sampled V2 handoff requires issue_sample_report')
    result = compare_models(handoff)
    body = build_report_body(accepted.acceptance_id, accepted.handoff_id, handoff, result)
    report_id = f'report-{uuid4().hex}'
    report = IssuedReport(report_id, body, build_receipt(report_id, accepted.acceptance_id))
    persist_issuance(connection, report)
    return report


def issue_sample_report(connection: sqlite3.Connection, acceptance_id: str) -> IssuedReport:
    """Sign the offline sampling report for an accepted V2 handoff."""
    acceptance_id = require_identifier(acceptance_id, 'acceptance id')
    accepted = get_by_acceptance_id(connection, acceptance_id)
    if accepted is None:
        raise KeyError(acceptance_id)
    handoff = decode_any_handoff(accepted.body_json)
    if not isinstance(handoff, SampleHandoffBody):
        raise ValueError('full-label V1 handoff requires issue_report')
    estimate = estimate_sample_difference(handoff)
    body = SampleReportBody(
        acceptance_id=accepted.acceptance_id,
        handoff_id=accepted.handoff_id,
        trial_id=handoff.trial_id,
        freeze_id=handoff.freeze_id,
        alpha_model=handoff.models.alpha,
        beta_model=handoff.models.beta,
        draw_id=handoff.draw_id,
        population_count=len(handoff.population),
        sample=handoff.sample,
        pairs=tuple(
            ReportPair(
                specimen_id=item.specimen_id,
                label_id=item.label.label_id,
                label=item.label.value,
                label_source=item.label.source,
                alpha_revision_id=item.alpha.revision_id,
                alpha_source=item.alpha.source,
                beta_revision_id=item.beta.revision_id,
                beta_source=item.beta.source,
            )
            for item in estimate.sampled
        ),
        mean_difference=estimate.mean_difference,
        variance_status=estimate.variance_status,
        variance_method=estimate.variance_method,
        variance_estimate=estimate.variance_estimate,
    )
    report_id = f'report-{uuid4().hex}'
    report = IssuedReport(report_id, body, build_receipt(report_id, accepted.acceptance_id))
    persist_issuance(connection, report)
    return report
