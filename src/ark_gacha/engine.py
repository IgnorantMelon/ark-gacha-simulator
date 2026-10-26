import random
from typing import Protocol

from .models import DrawResult, DrawState, PoolDefinition


class RandomSource(Protocol):
    def random(self) -> float: ...


def six_rate(base: float, misses: int) -> float:
    return min(1.0, base + 0.02 * max(0, misses - 49))


def weighted(items: list, weights: list[float], rng: RandomSource):
    total = sum(weights)
    if total <= 0 or len(items) != len(weights):
        raise ValueError("Invalid probability distribution")
    point = rng.random() * total
    cumulative = 0.0
    for item, weight in zip(items, weights, strict=True):
        cumulative += weight
        if point < cumulative:
            return item
    return items[-1]


def character_weights(pool: PoolDefinition, rarity: int) -> list[float]:
    roster = pool.roster[rarity]
    direct = {
        char: group.per_character
        for group in pool.up
        if group.rarity == rarity
        for char in group.characters
    }
    mass = sum(direct.values())
    others = [char for char in roster if char not in direct]
    residual = max(0.0, 1.0 - mass)
    # Source values are sometimes truncated to 6 decimal places. If everyone
    # is UP, normalize the source distribution rather than inventing an off-UP.
    if not others:
        return [direct.get(char, 0.0) for char in roster]
    total_weight = sum(pool.weight_up.get(char, 100.0) for char in others)
    return [
        direct[char]
        if char in direct
        else residual * pool.weight_up.get(char, 100.0) / total_weight
        for char in roster
    ]


def simulate(
    pool: PoolDefinition,
    initial: DrawState,
    count: int,
    rng: RandomSource | None = None,
) -> tuple[list[DrawResult], DrawState]:
    if count < 1:
        raise ValueError("Draw count must be positive")
    rng = rng or random.SystemRandom()
    state = initial.model_copy(deep=True)
    rules = pool.rules
    distributions = {rarity: character_weights(pool, rarity) for rarity in pool.roster}
    results = []
    for index in range(1, count + 1):
        force_target = (
            rules.target_six is not None
            and rules.target_six_threshold is not None
            and state.target_six_misses >= rules.target_six_threshold - 1
            and (rules.target_six_mode == "cycle" or not state.target_six_done)
        )
        p6 = six_rate(pool.rates[6], state.misses)
        rarity = weighted(
            [6, 5, 4, 3],
            [p6] + [pool.rates[r] * (1 - p6) / (1 - pool.rates[6]) for r in [5, 4, 3]],
            rng,
        )
        if force_target:
            rarity = 6
        if rules.first_ten and not state.first_five_done and state.pulls == 9:
            rarity = max(5, rarity)

        forced_char = None
        if force_target:
            forced_char = rules.target_six
        elif rarity == 6:
            stage = len(state.selection_picks)
            if (
                stage < len(rules.selection_thresholds)
                and state.pulls >= rules.selection_thresholds[stage]
            ):
                choices = [c for c in pool.direct_up(6) if c not in state.selection_picks]
                forced_char = weighted(choices, [1.0] * len(choices), rng)
                state.selection_picks.append(forced_char)
            elif (
                rules.directed_threshold is not None
                and not state.directed_done
                and state.directed_misses >= rules.directed_threshold
            ):
                forced_char = rules.directed_target
                state.directed_done = True
        elif rarity == 5:
            if (
                rules.complementary_five
                and state.first_up_five
                and not state.complementary_five_done
            ):
                choices = [c for c in pool.direct_up(5) if c != state.first_up_five]
                forced_char = weighted(choices, [1.0] * len(choices), rng)
                state.complementary_five_done = True
            elif (
                rules.target_five_threshold is not None
                and state.pulls >= rules.target_five_threshold
                and not state.up_five_seen
                and not state.target_five_done
            ):
                choices = pool.direct_up(5)
                forced_char = weighted(choices, [1.0] * len(choices), rng)
                state.target_five_done = True
        char = forced_char or weighted(pool.roster[rarity], distributions[rarity], rng)
        state.misses = 0 if rarity == 6 else state.misses + 1
        state.pulls += 1
        state.first_five_done |= rarity >= 5
        state.directed_misses = 0 if char == rules.directed_target else state.directed_misses + 1
        state.target_six_misses = 0 if char == rules.target_six else state.target_six_misses + 1
        if char == rules.target_six:
            state.target_six_done = True
        if rarity == 5 and char in pool.direct_up(5):
            state.up_five_seen = True
            if state.first_up_five is None:
                state.first_up_five = char
        results.append(DrawResult(index=index, char_id=char, rarity=rarity))
    return results, state
