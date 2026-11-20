from fractions import Fraction


def brier_loss(probability: Fraction, label: int) -> Fraction:
    error = probability - label
    return error * error


def paired_loss_difference(alpha: Fraction, beta: Fraction, label: int) -> Fraction:
    return brier_loss(alpha, label) - brier_loss(beta, label)
