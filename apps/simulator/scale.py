"""A fake checkout scale. Pure python so it can be reused outside Django (e.g. a Tk kiosk mock).

    scale = SimulatedScale(seed=42)
    scale.read(1020)                                   # honest: ~1020 +/- noise, multiple of 5
    scale.read(320, scenario="swap", swap_weight_g=1020)  # the customer bagged something else
"""

from __future__ import annotations

import random

SCENARIOS = ("honest", "swap", "double", "empty", "drift")


class SimulatedScale:
    def __init__(self, step_g: int = 5, noise_sd_g: float = 3, seed: int | None = None):
        if step_g < 1:
            raise ValueError("step_g must be >= 1")
        if noise_sd_g < 0:
            raise ValueError("noise_sd_g must be >= 0")
        self.step_g = int(step_g)
        self.noise_sd_g = float(noise_sd_g)
        self._rng = random.Random(seed)

    def _quantise(self, grams: float) -> int:
        return max(0, int(round(grams / self.step_g)) * self.step_g)

    def _noisy(self, grams: float) -> int:
        noise = self._rng.gauss(0, self.noise_sd_g) if self.noise_sd_g else 0.0
        return self._quantise(grams + noise)

    def read(self, expected_weight_g: int, scenario: str = "honest", swap_weight_g: int | None = None,
             drift_g: int = 0) -> int:
        """Return a weight in grams as the scale would display it after the item was placed."""
        if scenario == "honest":
            return self._noisy(expected_weight_g)
        if scenario == "swap":
            if swap_weight_g is None:
                raise ValueError("swap scenario needs swap_weight_g")
            return self._noisy(swap_weight_g)
        if scenario == "double":
            return self._noisy(2 * expected_weight_g)
        if scenario == "empty":
            return 0
        if scenario == "drift":
            return self._noisy(expected_weight_g + drift_g)
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {', '.join(SCENARIOS)}")
