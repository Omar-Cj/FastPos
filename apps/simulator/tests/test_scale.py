import pytest

from apps.simulator.scale import SimulatedScale


def test_deterministic_with_seed():
    a = [SimulatedScale(seed=7).read(500) for _ in range(3)]
    s1, s2 = SimulatedScale(seed=7), SimulatedScale(seed=7)
    assert [s1.read(500) for _ in range(20)] == [s2.read(500) for _ in range(20)]
    assert len(set(a)) == 1


def test_quantised_to_step():
    s = SimulatedScale(step_g=5, noise_sd_g=3, seed=1)
    assert all(s.read(1023) % 5 == 0 for _ in range(200))
    s = SimulatedScale(step_g=2, noise_sd_g=0)
    assert s.read(1023) in (1022, 1024)


def test_no_noise_is_exact():
    assert SimulatedScale(step_g=1, noise_sd_g=0).read(1020) == 1020


@pytest.mark.parametrize(
    ("scenario", "kwargs", "lo", "hi"),
    [
        ("honest", {}, 985, 1015),
        ("swap", {"swap_weight_g": 320}, 305, 335),
        ("double", {}, 1985, 2015),
        ("empty", {}, 0, 0),
        ("drift", {"drift_g": 40}, 1025, 1055),
    ],
)
def test_scenario_ranges(scenario, kwargs, lo, hi):
    s = SimulatedScale(seed=3)
    readings = [s.read(1000, scenario=scenario, **kwargs) for _ in range(300)]
    assert lo <= min(readings) and max(readings) <= hi


def test_never_negative():
    s = SimulatedScale(noise_sd_g=50, seed=2)
    assert min(s.read(5) for _ in range(200)) >= 0


def test_bad_arguments():
    with pytest.raises(ValueError):
        SimulatedScale().read(100, scenario="swap")
    with pytest.raises(ValueError):
        SimulatedScale().read(100, scenario="teleport")
    with pytest.raises(ValueError):
        SimulatedScale(step_g=0)
