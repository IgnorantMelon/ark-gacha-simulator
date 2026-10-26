import random

import pytest

from ark_gacha.engine import character_weights, simulate, six_rate
from ark_gacha.models import DrawState, RuleProfile, UpGroup


class Fixed:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


class Sequence:
    def __init__(self, values):
        self.values = iter(values)

    def random(self):
        return next(self.values)


@pytest.mark.parametrize(
    ("misses", "expected"), [(0, 0.02), (49, 0.02), (50, 0.04), (97, 0.98), (98, 1), (999, 1)]
)
def test_soft_pity_boundaries(misses, expected):
    assert six_rate(0.02, misses) == pytest.approx(expected)


def test_guaranteed_six_at_99_and_reset(pool):
    results, state = simulate(pool, DrawState(), 100, Fixed(0.999999))
    assert results[98].rarity == 6
    assert state.misses == 1
    assert sum(r.rarity == 6 for r in results) == 1


def test_first_ten_guarantee_only_once(pool):
    results, _ = simulate(pool, DrawState(), 20, Fixed(0.999999))
    assert results[9].rarity == 5
    assert results[19].rarity == 3
    _, state = simulate(pool, DrawState(pulls=9), 1, Fixed(0))
    assert state.first_five_done  # natural six satisfies the first-ten guarantee


def test_first_ten_does_not_increase_six_probability(pool):
    results, _ = simulate(pool, DrawState(pulls=9), 1, Fixed(0.03))
    assert results[0].rarity == 5


def test_direct_up_percent_is_per_character(pool):
    assert character_weights(pool, 5) == pytest.approx([0.25, 0.25, 0.5])


def test_weight_up_is_relative_to_baseline_100(pool):
    pool = pool.model_copy(
        update={
            "roster": {**pool.roster, 6: ["char_up", "char_old", "char_off"]},
            "weight_up": {"char_old": 500},
        }
    )
    assert character_weights(pool, 6) == pytest.approx([0.5, 0.5 * 5 / 6, 0.5 / 6])


def test_exclusive_up_roster_truncation(pool):
    pool = pool.model_copy(
        update={
            "roster": {**pool.roster, 5: ["char_fivea", "char_fiveb", "char_fiveoff"]},
            "up": [UpGroup(rarity=5, characters=pool.roster[5], per_character=0.333333)],
        }
    )
    assert len(character_weights(pool, 5)) == 3
    results, _ = simulate(pool, DrawState(), 10, Fixed(0.05))
    assert all(r.char_id in pool.roster[5] for r in results)


def test_directed_pity_waits_for_next_six_and_only_once(pool):
    pool = pool.model_copy(
        update={"rules": RuleProfile(directed_threshold=150, directed_target="char_up")}
    )
    results, state = simulate(pool, DrawState(pulls=150, directed_misses=150), 1, Fixed(0.9))
    assert results[0].rarity != 6 and not state.directed_done
    results, state = simulate(pool, state, 1, Fixed(0))
    assert results[0].char_id == "char_up" and state.directed_done
    results, _ = simulate(
        pool, state.model_copy(update={"directed_misses": 200}), 1, Sequence([0, 0.99])
    )
    assert results[0].char_id == "char_off"


def test_natural_up_resets_directed_counter_without_consuming_guarantee(pool):
    pool = pool.model_copy(
        update={"rules": RuleProfile(directed_threshold=150, directed_target="char_up")}
    )
    _, state = simulate(pool, DrawState(directed_misses=149), 1, Fixed(0))
    assert state.directed_misses == 0 and not state.directed_done


@pytest.mark.parametrize("mode", ["once", "cycle"])
def test_linkage_120_target_and_early_target_behavior(pool, mode):
    pool = pool.model_copy(
        update={
            "rules": RuleProfile(
                target_six="char_up", target_six_threshold=120, target_six_mode=mode
            )
        }
    )
    results, state = simulate(pool, DrawState(target_six_misses=119), 1, Fixed(0.99))
    assert results[0].char_id == "char_up"
    # Early natural success consumes the one-time guarantee; a cycle re-arms.
    state.target_six_misses = 119
    results, _ = simulate(pool, state, 1, Fixed(0.99))
    assert (results[0].char_id == "char_up") == (mode == "cycle")


def test_five_complement_after_any_intervening_rarity(pool):
    pool = pool.model_copy(update={"rules": RuleProfile(complementary_five=True)})
    state = DrawState(first_up_five="char_fivea", up_five_seen=True)
    _, state = simulate(pool, state, 1, Fixed(0.5))  # four-star must not consume the guarantee
    assert not state.complementary_five_done
    results, state = simulate(pool, state, 1, Fixed(0.05))
    assert results[0].char_id == "char_fiveb" and state.complementary_five_done


def test_target_five_threshold_does_not_force_rarity(pool):
    pool = pool.model_copy(update={"rules": RuleProfile(target_five_threshold=50)})
    results, state = simulate(pool, DrawState(pulls=50), 1, Fixed(0.9))
    assert results[0].rarity == 3 and not state.target_five_done
    results, state = simulate(pool, state, 1, Fixed(0.05))
    assert results[0].char_id in pool.direct_up(5) and state.target_five_done


def test_double_up_150_300_guarantees_other_character(pool):
    pool = pool.model_copy(
        update={
            "rules": RuleProfile(selection_thresholds=[150, 300]),
            "up": [UpGroup(rarity=6, characters=pool.roster[6], per_character=0.5)],
        }
    )
    results, state = simulate(pool, DrawState(pulls=150), 1, Fixed(0))
    assert results[0].char_id == "char_up" and state.selection_picks == ["char_up"]
    state.pulls = 300
    results, state = simulate(pool, state, 1, Fixed(0))
    assert results[0].char_id == "char_off"
    assert state.selection_picks == ["char_up", "char_off"]


def test_segmenting_draws_preserves_state_and_results(pool):
    whole, final = simulate(pool, DrawState(), 300, random.Random(731))
    source, state, segments = random.Random(731), DrawState(), []
    for _ in range(30):
        draws, state = simulate(pool, state, 10, source)
        segments += draws
    assert [r.char_id for r in whole] == [r.char_id for r in segments]
    assert final == state


def test_large_sample_base_probability_and_up_share(pool):
    # Reset per single pull to isolate base rates from soft pity and first-ten mechanics.
    rng = random.Random(31415)
    count, six, up = 30000, 0, 0
    for _ in range(count):
        results, _ = simulate(pool, DrawState(), 1, rng)
        six += results[0].rarity == 6
        up += results[0].char_id == "char_up"
    assert 0.017 < six / count < 0.023
    assert 0.43 < up / six < 0.57
