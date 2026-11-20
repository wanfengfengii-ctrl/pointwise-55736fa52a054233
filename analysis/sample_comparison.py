from dataclasses import dataclass
from fractions import Fraction

from bench.codec import HandoffLabel, HandoffPrediction
from bench.sample_codec import SampleHandoffBody
from bench.sampling import DesignEntry

from .loss import paired_loss_difference

VARIANCE_METHOD = 'horvitz-thompson'
VARIANCE_ESTIMABLE = 'estimable'
VARIANCE_JOINT_GAP = 'joint_gap'


@dataclass(frozen=True)
class SampledSpecimen:
    specimen_id: str
    label: HandoffLabel
    alpha: HandoffPrediction
    beta: HandoffPrediction
    difference: Fraction
    inclusion_rate: Fraction


@dataclass(frozen=True)
class SampleEstimate:
    sampled: tuple[SampledSpecimen, ...]
    mean_difference: Fraction
    variance_status: str
    variance_method: str | None
    variance_estimate: Fraction | None


def inclusion_rates(
    design: tuple[DesignEntry, ...], population: tuple[str, ...],
) -> dict[str, Fraction]:
    """First-order inclusion rate of every locked population specimen."""
    rates = {specimen_id: Fraction(0) for specimen_id in population}
    for entry in design:
        for specimen_id in entry.members:
            rates[specimen_id] += entry.weight
    return rates


def joint_inclusion_rates(
    design: tuple[DesignEntry, ...], population: tuple[str, ...],
) -> dict[frozenset[str], Fraction]:
    """Joint inclusion rate of every distinct locked population pair."""
    joints = {}
    for left_index, left in enumerate(population):
        for right in population[left_index + 1:]:
            joints[frozenset((left, right))] = Fraction(0)
    for entry in design:
        members = tuple(entry.members)
        for left_index, left in enumerate(members):
            for right in members[left_index + 1:]:
                joints[frozenset((left, right))] += entry.weight
    return joints


def point_estimate(
    population_count: int,
    sample: tuple[str, ...],
    rates: dict[str, Fraction],
    differences: dict[str, Fraction],
) -> Fraction:
    """Horvitz-Thompson estimate of the population mean Brier difference."""
    total = sum((differences[specimen_id] / rates[specimen_id] for specimen_id in sample), Fraction(0))
    return total / population_count


def variance_estimate(
    population_count: int,
    sample: tuple[str, ...],
    rates: dict[str, Fraction],
    joints: dict[frozenset[str], Fraction],
    differences: dict[str, Fraction],
) -> Fraction:
    """Horvitz-Thompson design variance estimate of the point estimate.

    For every ordered pair (i, j) of sampled specimens the term
    ((pi_ij - pi_i * pi_j) / pi_ij) * (d_i * d_j) / (pi_i * pi_j) is summed
    with pi_ii = pi_i, and the total is divided by the squared population
    count. The estimate is design-unbiased whenever every population pair
    has a positive joint inclusion rate; it keeps its sign and exact zero.
    """
    total = Fraction(0)
    for left in sample:
        for right in sample:
            if left == right:
                joint = rates[left]
            else:
                joint = joints[frozenset((left, right))]
            total += ((joint - rates[left] * rates[right]) / joint
                      * differences[left] * differences[right]
                      / (rates[left] * rates[right]))
    return total / population_count ** 2


def estimate_sample_difference(body: SampleHandoffBody) -> SampleEstimate:
    population = body.population
    population_count = len(population)
    rates = inclusion_rates(body.design, population)
    joints = joint_inclusion_rates(body.design, population)
    labels = {label.specimen_id: label for label in body.labels}
    predictions = {
        (prediction.specimen_id, prediction.model_id): prediction
        for prediction in body.predictions
    }
    sampled = []
    for specimen_id in body.sample:
        label = labels[specimen_id]
        alpha = predictions[specimen_id, body.models.alpha]
        beta = predictions[specimen_id, body.models.beta]
        difference = paired_loss_difference(alpha.probability, beta.probability, label.value)
        sampled.append(SampledSpecimen(
            specimen_id, label, alpha, beta, difference, rates[specimen_id],
        ))
    differences = {item.specimen_id: item.difference for item in sampled}
    mean = point_estimate(population_count, body.sample, rates, differences)
    if any(joint == 0 for joint in joints.values()):
        return SampleEstimate(tuple(sampled), mean, VARIANCE_JOINT_GAP, None, None)
    estimate = variance_estimate(population_count, body.sample, rates, joints, differences)
    return SampleEstimate(tuple(sampled), mean, VARIANCE_ESTIMABLE, VARIANCE_METHOD, estimate)
