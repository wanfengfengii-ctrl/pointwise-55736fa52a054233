import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from analysis import initialize_analysis
from analysis.intake import accept_handoff, read_acceptance
from archive.issue import issue_report, issue_sample_report
from archive.readback import read_report
from bench import initialize_source
from bench.handoff import issue_handoff, issue_sample_handoff, lock_sample
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


SAMPLING_SPECIMENS = ('X', 'Y', 'Z', 'W')
SAMPLING_MODELS = ('A', 'B')
SAMPLING_LABELS = (('X', 1), ('Y', 0))
DESIGNS = {
    'pairs': tuple(
        ((left, right), '1/6')
        for left, right in (('X', 'Y'), ('X', 'Z'), ('X', 'W'), ('Y', 'Z'), ('Y', 'W'), ('Z', 'W'))
    ),
    'blocks': ((('X', 'Y'), '1/2'), (('Z', 'W'), '1/2')),
}


def run_sampling_demo(directory: Path) -> dict:
    """Lock, issue and sign sampled handoffs; the bench database is closed before signing."""
    analysis_path = directory / 'sampling-analysis.sqlite3'
    with closing(sqlite3.connect(analysis_path)) as analysis:
        initialize_analysis(analysis)
        with closing(sqlite3.connect(directory / 'sampling-source.sqlite3')) as source:
            initialize_source(source)
            handoffs = {}
            for tag, design in DESIGNS.items():
                frozen = freeze_trial(source, f'materials-{tag}', SAMPLING_SPECIMENS, *SAMPLING_MODELS)
                for specimen_id in SAMPLING_SPECIMENS:
                    record_prediction(source, frozen.freeze_id, specimen_id, 'A', '1/4',
                                      f'alpha/{specimen_id}')
                    record_prediction(source, frozen.freeze_id, specimen_id, 'B', '3/4',
                                      f'beta/{specimen_id}')
                lock_sample(source, frozen.freeze_id, design, 0)
                for specimen_id, value in SAMPLING_LABELS:
                    record_label(source, frozen.freeze_id, specimen_id, value,
                                 f'instrument/{specimen_id}')
                handoffs[tag] = issue_sample_handoff(source, frozen.freeze_id)
        # The bench database is closed: only the received bodies are used downstream.
        acceptances = {
            tag: accept_handoff(analysis, handoff.handoff_id, handoff.body_json)
            for tag, handoff in handoffs.items()
        }
        reports = {tag: issue_sample_report(analysis, acceptance_id)
                   for tag, acceptance_id in acceptances.items()}

    with closing(sqlite3.connect(analysis_path)) as reopened:
        initialize_analysis(reopened)
        stored = {tag: read_report(reopened, report.report_id) for tag, report in reports.items()}
        kept = {tag: read_acceptance(reopened, acceptance_id)
                for tag, acceptance_id in acceptances.items()}

    def summary(report):
        body = report.body
        estimate = body.variance_estimate
        return {
            'sample': list(body.sample),
            'population_count': body.population_count,
            'mean_difference': str(body.mean_difference),
            'variance_status': body.variance_status,
            'variance_method': body.variance_method,
            'variance_estimate': None if estimate is None else str(estimate),
        }

    expected = {
        'pairs': {'sample': ['X', 'Y'], 'population_count': 4, 'mean_difference': '0',
                  'variance_status': 'estimable', 'variance_method': 'horvitz-thompson',
                  'variance_estimate': '1/8'},
        'blocks': {'sample': ['X', 'Y'], 'population_count': 4, 'mean_difference': '0',
                   'variance_status': 'joint_gap', 'variance_method': None,
                   'variance_estimate': None},
    }
    actual = {tag: summary(report) for tag, report in reports.items()}
    downstream_expected = {
        'same_body': True, 'same_receipt': True, 'acceptance_kept': True,
        'same_sample': True, 'distinct_status': True,
    }
    downstream_actual = {
        'same_body': all(stored[tag].body == reports[tag].body for tag in reports),
        'same_receipt': all(stored[tag].receipt == reports[tag].receipt for tag in reports),
        'acceptance_kept': all(kept[tag].body_json == handoffs[tag].body_json for tag in handoffs),
        'same_sample': actual['pairs']['sample'] == actual['blocks']['sample'],
        'distinct_status': actual['pairs']['variance_status'] != actual['blocks']['variance_status'],
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError(f'unexpected sampling result: {actual!r}, {downstream_actual!r}')
    return {
        'case_id': 'v2-sampling',
        'input': {'specimens': SAMPLING_SPECIMENS, 'models': SAMPLING_MODELS,
                  'designs': {tag: [[list(entry[0]), entry[1]] for entry in design]
                              for tag, design in DESIGNS.items()},
                  'draw': 0, 'labels': SAMPLING_LABELS},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['bench/sampling.py', 'bench/handoff.py', 'bench/sample_codec.py',
                       'analysis/intake.py', 'analysis/sample_comparison.py',
                       'archive/issue.py', 'archive/readback.py'],
        'oracle': 'Both designs draw X,Y with rate 1/2 each. Differences are 1/2 and -1/2, '
                  'so the mean estimate is exact zero. Only the all-pairs design covers every '
                  'joint pair, giving variance 1/8; the block design reports joint_gap.',
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='materials-lab-') as directory:
        scenarios = [run_demo(Path(directory))]
        scenarios.append(run_sampling_demo(Path(directory)))
    for scenario in scenarios:
        print('POINTWISE_SCENARIO ' + json.dumps(scenario, sort_keys=True))


if __name__ == '__main__':
    main()
