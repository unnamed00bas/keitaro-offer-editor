import pytest

from app.shares import ShareError, Slot, even_split, recalc, validate_total


def test_even_split_three_offers():
    assert even_split(100, 3) == [33, 33, 34]


@pytest.mark.parametrize("n", range(1, 12))
def test_even_split_sums_to_total(n):
    parts = even_split(100, n)
    assert sum(parts) == 100 and max(parts) - min(parts) <= 1


def test_removed_offer_gets_zero_and_others_share():
    res = recalc([Slot(1, True, False, 33), Slot(2, False, False, 33), Slot(3, True, False, 34)])
    assert res == {1: 50, 2: 0, 3: 50}


def test_pinned_share_is_kept():
    res = recalc([Slot(1, True, True, 25), Slot(2, True, False, 0), Slot(3, True, False, 0)])
    assert res[1] == 25 and res[2] + res[3] == 75 and sorted([res[2], res[3]]) == [37, 38]


def test_pinned_over_100_rejected():
    with pytest.raises(ShareError):
        recalc([Slot(1, True, True, 60), Slot(2, True, True, 50)])


def test_all_pinned_keeps_values():
    assert recalc([Slot(1, True, True, 30), Slot(2, True, True, 70)]) == {1: 30, 2: 70}


def test_validate_total():
    validate_total([50, 50])
    with pytest.raises(ShareError):
        validate_total([30, 30])
    with pytest.raises(ShareError):
        validate_total([])
