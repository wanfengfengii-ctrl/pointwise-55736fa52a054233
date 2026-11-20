from fractions import Fraction


ProbabilityInput = Fraction | int | str


def _check_bounds(value: Fraction) -> Fraction:
    if not 0 <= value <= 1:
        raise ValueError('probability must be between zero and one inclusive')
    return value


def parse_probability(value: ProbabilityInput) -> Fraction:
    if type(value) is not int and not isinstance(value, (Fraction, str)):
        raise ValueError('probability must be an integer, Fraction, or rational string')
    try:
        probability = Fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as error:
        raise ValueError('invalid rational probability') from error
    return _check_bounds(probability)


def probability_to_parts(value: Fraction) -> tuple[int, int]:
    """Return the reduced wire representation of a validated probability."""
    return value.numerator, value.denominator


def probability_from_parts(numerator: object, denominator: object) -> Fraction:
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise ValueError('probability requires an integer numerator and positive integer denominator')
    return _check_bounds(Fraction(numerator, denominator))
