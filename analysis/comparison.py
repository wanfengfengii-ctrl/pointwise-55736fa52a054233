from dataclasses import dataclass
from fractions import Fraction

from bench.codec import HandoffBody

from .loss import paired_loss_difference
from .pairs import PairedSpecimen, match_pairs


@dataclass(frozen=True)
class ComparisonResult:
    pairs: tuple[PairedSpecimen, ...]
    pair_count: int
    mean_difference: Fraction | None


def compare_models(body: HandoffBody) -> ComparisonResult:
    pairs = match_pairs(body)
    pair_count = len(pairs)
    if pair_count == 0:
        return ComparisonResult(pairs, 0, None)

    differences = (
        paired_loss_difference(pair.alpha.probability, pair.beta.probability, pair.label.value)
        for pair in pairs
    )
    return ComparisonResult(pairs, pair_count, sum(differences, Fraction(0)) / pair_count)
