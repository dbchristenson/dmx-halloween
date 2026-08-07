"""Dwell-time sampling for phase transitions.

Each phase draws its dwell at entry from a Weibull with shape k > 1 (rising
hazard — "more likely over time"), clipped to [t_min, t_max]. Not a Poisson
process: that would be memoryless. Parameters come from config so timing can
be retuned night-to-night without a deploy.
"""

from dataclasses import dataclass

import numpy as np
from scipy.stats import weibull_min


@dataclass(frozen=True)
class DwellConfig:
    t_min: float  # seconds
    t_max: float
    k: float  # Weibull shape; k > 1 gives rising hazard
    scale: float  # Weibull scale (lambda), seconds


def sample_dwell(cfg: DwellConfig, rng: np.random.Generator) -> float:
    t = weibull_min.rvs(cfg.k, scale=cfg.scale, random_state=rng)
    return float(np.clip(t, cfg.t_min, cfg.t_max))
