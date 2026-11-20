import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from uuid import uuid4

from predictions.history import load_current_predictions
from trial.freeze import read_frozen_trial
from trial.identities import require_identifier
from trial.registry import write_transaction

from .labels import load_final_labels


@dataclass(frozen=True)
class DesignEntry:
    members: frozenset[str]
    weight: Fraction


@dataclass(frozen=True)
class LockedPopulationMember:
    specimen_id: str
    alpha_revision_id: str
    beta_revision_id: str


@dataclass(frozen=True)
class LockedSample:
    draw_id: str
    freeze_id: str
    design: tuple[DesignEntry, ...]
    draw: Fraction
    population: tuple[str, ...]
    sample: tuple[str, ...]
    members: tuple[LockedPopulationMember, ...]


def parse_rational(value: object, field: str) -> Fraction:
    if type(value) is not int and not isinstance(value, (Fraction, str)):
        raise ValueError(f'{field} must be an integer, Fraction, or rational string')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as error:
        raise ValueError(f'invalid rational {field}') from error


def parse_draw_value(value: object) -> Fraction:
    draw = parse_rational(value, 'draw')
    if not 0 <= draw < 1:
        raise ValueError('draw must be a rational in [0, 1)')
    return draw


def _parse_members(value: object) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError('candidate list must be a sequence of specimen identifiers')
    members = tuple(require_identifier(item, 'specimen') for item in value)
    if not members:
        raise ValueError('candidate list must not be empty')
    if len(set(members)) != len(members):
        raise ValueError('duplicate specimen in candidate list')
    return frozenset(members)


def parse_design(design: object) -> tuple[DesignEntry, ...]:
    """Validate an ordered weighted design of equal-size distinct candidate lists."""
    if not isinstance(design, Sequence) or isinstance(design, (str, bytes, bytearray)):
        raise ValueError('design must be an ordered sequence of (specimens, weight) pairs')
    entries = []
    size = None
    seen = set()
    total = Fraction(0)
    for entry in design:
        if not isinstance(entry, Sequence) or isinstance(entry, (str, bytes, bytearray)) or len(entry) != 2:
            raise ValueError('design entries must be (specimens, weight) pairs')
        members = _parse_members(entry[0])
        weight = parse_rational(entry[1], 'design weight')
        if weight <= 0:
            raise ValueError('design weights must be positive')
        if size is None:
            size = len(members)
        elif len(members) != size:
            raise ValueError('candidate lists must have equal size')
        if members in seen:
            raise ValueError('candidate specimen sets must not repeat')
        seen.add(members)
        entries.append(DesignEntry(members, weight))
        total += weight
    if not entries:
        raise ValueError('design must contain at least one candidate list')
    if total != 1:
        raise ValueError('design weights must sum to one')
    return tuple(entries)


def require_design_coverage(design: tuple[DesignEntry, ...], population: tuple[str, ...]) -> None:
    population_set = set(population)
    covered = set()
    for entry in design:
        outside = entry.members - population_set
        if outside:
            raise ValueError(f'candidate specimens outside the locked population: {sorted(outside)!r}')
        covered |= entry.members
    missing = population_set - covered
    if missing:
        raise ValueError(f'population specimens absent from every candidate list: {sorted(missing)!r}')


def select_sample_entry(design: tuple[DesignEntry, ...], draw: Fraction) -> DesignEntry:
    """Pick the candidate list whose left-closed cumulative weight interval holds the draw."""
    cumulative = Fraction(0)
    for entry in design:
        if cumulative <= draw < cumulative + entry.weight:
            return entry
        cumulative += entry.weight
    raise ValueError('draw falls outside the design weights')


def ordered_sample(population: tuple[str, ...], entry: DesignEntry) -> tuple[str, ...]:
    return tuple(specimen_id for specimen_id in population if specimen_id in entry.members)


def initialize_sampling(connection: sqlite3.Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS sample_draws (
            draw_id TEXT NOT NULL PRIMARY KEY,
            freeze_id TEXT NOT NULL UNIQUE REFERENCES frozen_trials(freeze_id),
            draw_numerator TEXT NOT NULL,
            draw_denominator TEXT NOT NULL
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS sample_design_lists (
            draw_id TEXT NOT NULL REFERENCES sample_draws(draw_id),
            list_position INTEGER NOT NULL CHECK (list_position >= 0),
            weight_numerator TEXT NOT NULL,
            weight_denominator TEXT NOT NULL,
            PRIMARY KEY (draw_id, list_position)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS sample_design_members (
            draw_id TEXT NOT NULL,
            list_position INTEGER NOT NULL,
            specimen_id TEXT NOT NULL,
            PRIMARY KEY (draw_id, list_position, specimen_id),
            FOREIGN KEY (draw_id, list_position)
                REFERENCES sample_design_lists(draw_id, list_position)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS sample_population (
            draw_id TEXT NOT NULL REFERENCES sample_draws(draw_id),
            position INTEGER NOT NULL CHECK (position >= 0),
            specimen_id TEXT NOT NULL,
            alpha_revision_id TEXT NOT NULL REFERENCES prediction_revisions(revision_id),
            beta_revision_id TEXT NOT NULL REFERENCES prediction_revisions(revision_id),
            PRIMARY KEY (draw_id, position),
            UNIQUE (draw_id, specimen_id)
        )
    ''')


def _save_sample_lock(connection: sqlite3.Connection, lock: LockedSample) -> None:
    connection.execute(
        'INSERT INTO sample_draws (draw_id, freeze_id, draw_numerator, draw_denominator) VALUES (?, ?, ?, ?)',
        (lock.draw_id, lock.freeze_id, str(lock.draw.numerator), str(lock.draw.denominator)),
    )
    for position, entry in enumerate(lock.design):
        connection.execute(
            '''INSERT INTO sample_design_lists (draw_id, list_position, weight_numerator, weight_denominator)
               VALUES (?, ?, ?, ?)''',
            (lock.draw_id, position, str(entry.weight.numerator), str(entry.weight.denominator)),
        )
        connection.executemany(
            '''INSERT INTO sample_design_members (draw_id, list_position, specimen_id)
               VALUES (?, ?, ?)''',
            [(lock.draw_id, position, specimen_id) for specimen_id in entry.members],
        )
    connection.executemany(
        '''INSERT INTO sample_population
               (draw_id, position, specimen_id, alpha_revision_id, beta_revision_id)
           VALUES (?, ?, ?, ?, ?)''',
        [(lock.draw_id, position, member.specimen_id, member.alpha_revision_id, member.beta_revision_id)
         for position, member in enumerate(lock.members)],
    )


def load_sample_lock(connection: sqlite3.Connection, freeze_id: str) -> LockedSample | None:
    row = connection.execute(
        'SELECT draw_id, draw_numerator, draw_denominator FROM sample_draws WHERE freeze_id = ?',
        (freeze_id,),
    ).fetchone()
    if row is None:
        return None
    draw_id, draw_numerator, draw_denominator = row
    draw = Fraction(int(draw_numerator), int(draw_denominator))
    lists = connection.execute(
        '''SELECT list_position, weight_numerator, weight_denominator FROM sample_design_lists
           WHERE draw_id = ? ORDER BY list_position''',
        (draw_id,),
    ).fetchall()
    design = []
    for list_position, weight_numerator, weight_denominator in lists:
        members = connection.execute(
            'SELECT specimen_id FROM sample_design_members WHERE draw_id = ? AND list_position = ?',
            (draw_id, list_position),
        )
        design.append(DesignEntry(
            frozenset(specimen_id for (specimen_id,) in members),
            Fraction(int(weight_numerator), int(weight_denominator)),
        ))
    population_rows = connection.execute(
        '''SELECT specimen_id, alpha_revision_id, beta_revision_id FROM sample_population
           WHERE draw_id = ? ORDER BY position''',
        (draw_id,),
    ).fetchall()
    members = tuple(LockedPopulationMember(*row) for row in population_rows)
    population = tuple(member.specimen_id for member in members)
    design_tuple = tuple(design)
    sample = ordered_sample(population, select_sample_entry(design_tuple, draw))
    return LockedSample(draw_id, freeze_id, design_tuple, draw, population, sample, members)


def lock_sample(
    connection: sqlite3.Connection,
    freeze_id: str,
    design: object,
    draw: object,
) -> LockedSample:
    """Lock the unique sampling draw for a freeze before any of its final labels."""
    frozen = read_frozen_trial(connection, freeze_id)
    parsed = parse_design(design)
    draw_value = parse_draw_value(draw)
    with write_transaction(connection):
        existing = load_sample_lock(connection, frozen.freeze_id)
        if existing is not None:
            if parsed == existing.design and draw_value == existing.draw:
                return existing
            raise ValueError('freeze is already locked with a different design or draw')
        if load_final_labels(connection, frozen.freeze_id):
            raise ValueError('first lock must precede any final label of the freeze')
        heads = {
            (revision.specimen_id, revision.model_id): revision
            for revision in load_current_predictions(connection, frozen.freeze_id)
        }
        members = []
        for specimen_id in frozen.roster.specimens:
            alpha = heads.get((specimen_id, frozen.models.alpha))
            beta = heads.get((specimen_id, frozen.models.beta))
            if alpha is not None and beta is not None:
                members.append(LockedPopulationMember(specimen_id, alpha.revision_id, beta.revision_id))
        population = tuple(member.specimen_id for member in members)
        require_design_coverage(parsed, population)
        sample = ordered_sample(population, select_sample_entry(parsed, draw_value))
        lock = LockedSample(
            f'draw-{uuid4().hex}', frozen.freeze_id, parsed, draw_value,
            population, sample, tuple(members),
        )
        _save_sample_lock(connection, lock)
        return lock
