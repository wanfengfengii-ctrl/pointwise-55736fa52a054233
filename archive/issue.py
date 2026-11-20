import sqlite3
from uuid import uuid4

from analysis.comparison import compare_models
from analysis.intake import load_accepted_body

from .body import build_report_body
from .receipts import build_receipt
from .storage import IssuedReport, persist_issuance


def issue_report(connection: sqlite3.Connection, handoff_id: str) -> IssuedReport:
    accepted, handoff = load_accepted_body(connection, handoff_id)
    result = compare_models(handoff)
    body = build_report_body(accepted.acceptance_id, accepted.handoff_id, handoff, result)
    report_id = f'report-{uuid4().hex}'
    report = IssuedReport(report_id, body, build_receipt(report_id, accepted.acceptance_id))
    persist_issuance(connection, report)
    return report
