from collections.abc import Sequence
from dataclasses import dataclass, field

from .identities import require_identifier


@dataclass(frozen=True)
class OrderedRoster:
    specimens: tuple[str, ...]
    _members: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        members = frozenset(self.specimens)
        if len(members) != len(self.specimens):
            raise ValueError('duplicate specimen in roster')
        object.__setattr__(self, '_members', members)

    def __contains__(self, specimen_id: str) -> bool:
        return specimen_id in self._members


def build_roster(values: Sequence[object]) -> OrderedRoster:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError('roster must be an ordered sequence of specimen identifiers')
    return OrderedRoster(tuple(require_identifier(value, 'specimen') for value in values))


def require_specimen(roster: OrderedRoster, value: object) -> str:
    specimen_id = require_identifier(value, 'specimen')
    if specimen_id not in roster:
        raise ValueError(f'specimen {specimen_id!r} is outside the frozen roster')
    return specimen_id
