"""DET-4 (baseline) + DET-5 (anomaly score).

Each term keeps an exponentially-weighted moving average (EWMA) of its
per-window count, plus an EWMA of the variance, so both its "normal" level and
its normal wobble adapt over time. The anomaly score is a z-score of the
newest window against that baseline:

    z = (count - mean) / sqrt(variance + mean + 1)

The `+ mean + 1` is a Poisson-style floor. A term with almost no history has
variance ~0, and dividing by that would make 0 -> 2 mentions look like a huge
spike. With the floor, small counts need a real jump to score high.
"""
import math
from dataclasses import dataclass

SPIKE, RISING, STEADY, FALLING = "spike", "rising", "steady", "falling"


@dataclass
class Baseline:
    alpha: float
    mean: float = 0.0
    var: float = 0.0

    def z(self, count: float) -> float:
        return (count - self.mean) / math.sqrt(self.var + self.mean + 1.0)

    def update(self, count: float) -> None:
        # Incremental EWMA of mean and variance (West, 1979).
        delta = count - self.mean
        self.mean += self.alpha * delta
        self.var = (1.0 - self.alpha) * (self.var + self.alpha * delta * delta)

    @property
    def negligible(self) -> bool:
        """Decayed to ~nothing: safe to forget; rebuilds from 0 if the term returns."""
        return self.mean < 0.01 and self.var < 0.01


def velocity_label(z: float, spike_threshold: float) -> str:
    if z >= spike_threshold:
        return SPIKE
    if z >= 1.0:
        return RISING
    if z <= -1.0:
        return FALLING
    return STEADY
