import sqlite3

from trial.registry import initialize_trial_registry, write_transaction
from trial.revisions import initialize_trial_revisions


def initialize_source(connection: sqlite3.Connection) -> None:
    """Ensure the source schema exists and retain all previously saved work."""
    from predictions.history import initialize_prediction_history

    from .issued import initialize_issued_handoffs
    from .labels import initialize_labels

    connection.execute('PRAGMA foreign_keys = ON')
    with write_transaction(connection):
        initialize_trial_registry(connection)
        initialize_trial_revisions(connection)
        initialize_prediction_history(connection)
        initialize_labels(connection)
        initialize_issued_handoffs(connection)
