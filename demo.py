import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from analysis import initialize_analysis
from analysis.intake import accept_handoff, read_acceptance
from archive.issue import issue_report
from archive.readback import read_report
from bench import initialize_source
from bench.handoff import issue_handoff
from bench.labels import record_label
from predictions.intake import record_prediction
from trial.freeze import freeze_trial


SPECIMENS = ('X', 'Y')
MODELS = ('A', 'B')
PREDICTIONS = (
    ('X', 'A', '0', 'alpha-X/original'),
    ('X', 'A', '1/4', 'alpha-X/correction'),
    ('X', 'B', '3/4', 'beta-X/original'),
    ('Y', 'A', '1', 'alpha-Y/original'),
)
LABELS = (('X', 1, 'bench-X'), ('Y', 1, 'bench-Y'))


def run_demo(directory: Path) -> dict:
    analysis_path = directory / 'analysis.sqlite3'
    with closing(sqlite3.connect(analysis_path)) as analysis:
        initialize_analysis(analysis)
        with closing(sqlite3.connect(directory / 'source.sqlite3')) as source:
            initialize_source(source)
            frozen = freeze_trial(source, 'materials-17', SPECIMENS, *MODELS)
            revisions = {}
            for specimen, model, probability, provenance in PREDICTIONS:
                revisions[specimen, model] = record_prediction(
                    source, frozen.freeze_id, specimen, model, probability, provenance,
                )
            for specimen, label, provenance in LABELS:
                record_label(source, frozen.freeze_id, specimen, label, provenance)
            handoff = issue_handoff(source, frozen.freeze_id)
            acceptance_id = accept_handoff(analysis, handoff.handoff_id, handoff.body_json)
        accepted = read_acceptance(analysis, acceptance_id)
        issued = issue_report(analysis, accepted.handoff_id)

    with closing(sqlite3.connect(analysis_path)) as reopened:
        initialize_analysis(reopened)
        stored = read_report(reopened, issued.report_id)

    expected = {
        'pair_count': 1,
        'mean_difference': '1/2',
        'pairs': [['X', 'alpha-X/correction', 'beta-X/original']],
    }
    difference = issued.body.mean_difference
    actual = {
        'pair_count': issued.body.pair_count,
        'mean_difference': None if difference is None else str(difference),
        'pairs': [[pair.specimen_id, pair.alpha_source, pair.beta_source] for pair in issued.body.pairs],
    }
    downstream_expected = {'same_body': True, 'same_receipt': True, 'kept_revisions': True}
    downstream_actual = {
        'same_body': stored.body == issued.body,
        'same_receipt': stored.receipt == issued.receipt,
        'kept_revisions': [
            [pair.alpha_revision_id, pair.beta_revision_id] for pair in stored.body.pairs
        ] == [[revisions['X', 'A'].revision_id, revisions['X', 'B'].revision_id]],
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'unexpected sample result: {actual!r}, {downstream_actual!r}')
    return {
        'case_id': 'v7',
        'input': {'specimens': SPECIMENS, 'models': MODELS, 'predictions': PREDICTIONS, 'labels': LABELS},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['predictions/intake.py', 'bench/handoff.py', 'analysis/pairs.py',
                       'analysis/comparison.py', 'archive/issue.py', 'archive/readback.py'],
        'oracle': 'Only X has both predictions. Its two losses are 9/16 and 1/16. Their difference is 1/2.',
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='materials-lab-') as directory:
        scenario = run_demo(Path(directory))
    print('POINTWISE_SCENARIO ' + json.dumps(scenario, sort_keys=True))


if __name__ == '__main__':
    main()
