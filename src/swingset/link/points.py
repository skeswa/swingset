"""WSDC point chart calculations from the published 2026 rules."""

_TOP_FIVE = (
    (5, 10, (3, 2, 1, 0, 0)),
    (11, 19, (6, 4, 3, 2, 1)),
    (20, 39, (10, 8, 6, 4, 2)),
    (40, 79, (15, 12, 10, 8, 6)),
    (80, 129, (20, 16, 14, 12, 10)),
    (130, 10**9, (25, 22, 18, 15, 12)),
)


def expected_points(competitors: int, place: int) -> int:
    """Return expected points, including the lower-place tier extras."""
    for low, high, top in _TOP_FIVE:
        if low <= competitors <= high:
            if 1 <= place <= 5:
                return top[place - 1]
            if competitors >= 80 and place <= 15:
                return 2
            if competitors >= 20 and place <= (12 if competitors >= 40 else 10):
                return 1
            return 0
    return 0
