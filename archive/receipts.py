import sqlite3
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True)
class IssueReceipt:
    receipt_id: str
    report_id: str
    acceptance_id: str


def initialize_receipts(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS issue_receipts (
            receipt_id TEXT NOT NULL UNIQUE,
            report_id TEXT NOT NULL PRIMARY KEY,
            acceptance_id TEXT NOT NULL,
            FOREIGN KEY (report_id, acceptance_id) REFERENCES reports(report_id, acceptance_id)
        )
    ''')


def build_receipt(report_id: str, acceptance_id: str) -> IssueReceipt:
    return IssueReceipt(f'receipt-{uuid4().hex}', report_id, acceptance_id)


def insert_receipt(connection: sqlite3.Connection, receipt: IssueReceipt) -> None:
    connection.execute(
        'INSERT INTO issue_receipts (receipt_id, report_id, acceptance_id) VALUES (?, ?, ?)',
        (receipt.receipt_id, receipt.report_id, receipt.acceptance_id),
    )


def load_receipt(connection: sqlite3.Connection, report_id: str) -> IssueReceipt:
    row = connection.execute(
        'SELECT receipt_id, report_id, acceptance_id FROM issue_receipts WHERE report_id = ?',
        (report_id,),
    ).fetchone()
    if row is None:
        raise KeyError(report_id)
    return IssueReceipt(*row)
