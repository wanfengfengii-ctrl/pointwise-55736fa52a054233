import sqlite3

from trial.identities import require_identifier

from .receipts import load_receipt
from .storage import IssuedReport, load_report_body


def read_report(connection: sqlite3.Connection, report_id: str) -> IssuedReport:
    report_id = require_identifier(report_id, 'report id')
    body = load_report_body(connection, report_id)
    receipt = load_receipt(connection, report_id)
    return IssuedReport(report_id, body, receipt)
