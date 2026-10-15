"""CLU-5 (baseline + score). Deliberately identical to te-detector's
detector/scoring.py: the design doc builds the Cluster Velocity Scorer the
same way as the term Anomaly Scorer, so te-aggregator merges two z-scores on
equal footing. (Copied rather than shared, since each service builds from its
own directory.)

Each cluster keeps an EWMA of its new-members-per-window count plus an EWMA
of the variance. The growth score is a z-score of the newest window:

    z = (count - mean) / sqrt(variance + mean + 1)

The `+ mean + 1` is a Poisson-style floor: without it, a brand-new cluster
going from 0 to 2 members would look like an enormous spike.
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
