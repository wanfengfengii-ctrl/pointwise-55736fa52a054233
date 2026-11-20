import sqlite3
from dataclasses import dataclass

from trial.registry import write_transaction

from .body import ReportBody, SampleReportBody, decode_report_body, encode_report_body
from .receipts import IssueReceipt, initialize_receipts, insert_receipt


@dataclass(frozen=True)
class IssuedReport:
    report_id: str
    body: ReportBody | SampleReportBody
    receipt: IssueReceipt


def initialize_archive(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT NOT NULL PRIMARY KEY,
            acceptance_id TEXT NOT NULL REFERENCES accepted_handoffs(acceptance_id),
            body_json TEXT NOT NULL,
            UNIQUE (report_id, acceptance_id)
        )
    ''')
    initialize_receipts(connection)


def persist_issuance(connection: sqlite3.Connection, report: IssuedReport) -> None:
    if (report.receipt.report_id, report.receipt.acceptance_id) != (
        report.report_id, report.body.acceptance_id,
    ):
        raise ValueError('receipt must name this report and its acceptance')
    body_json = encode_report_body(report.body)
    with write_transaction(connection):
        connection.execute(
            'INSERT INTO reports (report_id, acceptance_id, body_json) VALUES (?, ?, ?)',
            (report.report_id, report.body.acceptance_id, body_json),
        )
        insert_receipt(connection, report.receipt)


def load_report_body(connection: sqlite3.Connection, report_id: str) -> ReportBody | SampleReportBody:
    row = connection.execute(
        'SELECT body_json FROM reports WHERE report_id = ?', (report_id,),
    ).fetchone()
    if row is None:
        raise KeyError(report_id)
    return decode_report_body(row[0])
