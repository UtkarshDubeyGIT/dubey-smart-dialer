import math


def wilson_upper_bound(successes: int, trials: int, z_score: float = 1.6448536269514722) -> float:
    """One-sided 95% Wilson upper confidence bound for a Bernoulli rate."""
    if trials <= 0:
        return 1.0
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between zero and trials")
    rate = successes / trials
    z2 = z_score * z_score
    denominator = 1.0 + z2 / trials
    centre = rate + z2 / (2.0 * trials)
    spread = z_score * math.sqrt(
        (rate * (1.0 - rate) + z2 / (4.0 * trials)) / trials
    )
    return min(1.0, (centre + spread) / denominator)


def _log_binomial_pmf(calls: int, answers: int, probability: float) -> float:
    return (
        math.lgamma(calls + 1)
        - math.lgamma(answers + 1)
        - math.lgamma(calls - answers + 1)
        + answers * math.log(probability)
        + (calls - answers) * math.log1p(-probability)
    )


def binomial_overload_probability(calls: int, answer_probability: float, capacity: int) -> float:
    """Return P(X > capacity) for X ~ Binomial(calls, answer_probability)."""
    if calls < 0 or capacity < 0:
        raise ValueError("calls and capacity must be non-negative")
    if not 0.0 <= answer_probability <= 1.0:
        raise ValueError("answer_probability must be between zero and one")
    if calls <= capacity or answer_probability == 0.0:
        return 0.0
    if answer_probability == 1.0:
        return 1.0

    logs = [
        _log_binomial_pmf(calls, answers, answer_probability)
        for answers in range(capacity + 1, calls + 1)
    ]
    maximum = max(logs)
    probability = math.exp(maximum) * math.fsum(math.exp(value - maximum) for value in logs)
    return min(1.0, max(0.0, probability))


def largest_safe_batch(
    proposed_calls: int,
    answer_probability: float,
    capacity: int,
    risk_tolerance: float,
) -> tuple[int, float]:
    if proposed_calls <= 0 or capacity <= 0:
        return 0, 0.0
    if risk_tolerance <= 0.0:
        approved = min(proposed_calls, capacity)
        return approved, 0.0

    low = 0
    high = proposed_calls
    while low < high:
        candidate = (low + high + 1) // 2
        risk = binomial_overload_probability(candidate, answer_probability, capacity)
        if risk <= risk_tolerance:
            low = candidate
        else:
            high = candidate - 1
    return low, binomial_overload_probability(low, answer_probability, capacity)
