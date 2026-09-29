"""The kingmaker check: does any single judge decide who wins a prize?

For each judge, the ranking is refitted without that judge's reviews. A judge
is a kingmaker when removing them, and nobody else, changes which projects hold
the prize places, or who comes first. The check reports which projects would
enter and leave the prize places.

Projects reviewed only by the removed judge have no evidence left, so they are
left out of both rankings before comparing. Their absence is a coverage
problem, not influence.
"""

from dataclasses import dataclass

from scoring_engine.normalization import Observation, evaluate


@dataclass(frozen=True)
class Influence:
    judge: str
    reviews: int
    entered: tuple[str, ...]
    left: tuple[str, ...]
    winner_before: str
    winner_after: str

    @property
    def changes_winner(self) -> bool:
        return self.winner_before != self.winner_after


def kingmakers(
    observations: list[Observation],
    *,
    method: str = "additive",
    places: int = 5,
    k: float = 3.0,
    lam: float = 1.0,
) -> list[Influence]:
    """Judges whose removal alone changes the prize places or the winner."""
    if not observations:
        return []
    base = evaluate(observations, method=method, bootstrap=0, k=k, lam=lam).ranking(method)
    found = []
    for judge in sorted({o.judge for o in observations}):
        rest = [o for o in observations if o.judge != judge]
        if not rest:
            continue
        after = evaluate(rest, method=method, bootstrap=0, k=k, lam=lam).ranking(method)
        still_ranked = set(after)
        before = [p for p in base if p in still_ranked]
        top_before, top_after = set(before[:places]), set(after[:places])
        if top_before != top_after or before[0] != after[0]:
            found.append(
                Influence(
                    judge=judge,
                    reviews=sum(1 for o in observations if o.judge == judge),
                    entered=tuple(sorted(top_after - top_before)),
                    left=tuple(sorted(top_before - top_after)),
                    winner_before=before[0],
                    winner_after=after[0],
                )
            )
    return found
