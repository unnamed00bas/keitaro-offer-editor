"""Пересчёт долей офферов в потоке.

Правила (как в видео с эталонным редактором):
* удалённые офферы получают 0%;
* закреплённые (pinned) доли не меняются;
* остаток 100 - сумма закреплённых делится поровну между незакреплёнными,
  целыми процентами; лишние проценты от деления уходят последним офферам
  (три оффера -> 33/33/34).
"""

from dataclasses import dataclass

TOTAL = 100


class ShareError(ValueError):
    pass


@dataclass
class Slot:
    key: int
    active: bool
    pinned: bool
    share: int


def even_split(total: int, n: int) -> list[int]:
    if n <= 0:
        return []
    base, extra = divmod(total, n)
    # остаток отдаём последним, чтобы получилось 33/33/34
    return [base] * (n - extra) + [base + 1] * extra


def recalc(slots: list[Slot]) -> dict[int, int]:
    """Возвращает новые доли {key: share}. Порядок slots = порядок в потоке."""
    result: dict[int, int] = {}
    active = [s for s in slots if s.active]
    pinned = [s for s in active if s.pinned]
    free = [s for s in active if not s.pinned]

    pinned_sum = sum(s.share for s in pinned)
    if any(s.share < 0 or s.share > TOTAL for s in pinned):
        raise ShareError("Закреплённая доля должна быть от 0 до 100")
    if pinned_sum > TOTAL:
        raise ShareError(f"Закреплённые доли в сумме {pinned_sum}%, больше 100%")

    for s in slots:
        if not s.active:
            result[s.key] = 0
    for s in pinned:
        result[s.key] = s.share
    for s, share in zip(free, even_split(TOTAL - pinned_sum, len(free))):
        result[s.key] = share
    return result


def validate_total(shares: list[int]) -> None:
    if not shares:
        raise ShareError("В потоке должен остаться хотя бы один активный оффер")
    total = sum(shares)
    if total != TOTAL:
        raise ShareError(f"Сумма долей {total}%, нужно ровно 100%")
