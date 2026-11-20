import sqlite3

from trial.registry import write_transaction


def initialize_analysis(connection: sqlite3.Connection) -> None:
    """Ensure the offline acceptance and archive schema exists."""
    from archive.storage import initialize_archive

    from .accepted import initialize_acceptances

    connection.execute('PRAGMA foreign_keys = ON')
    with write_transaction(connection):
        initialize_acceptances(connection)
        initialize_archive(connection)
