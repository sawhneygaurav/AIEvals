from competitive_scoring.book_policy import BOOK_CATEGORIES


def test_book_policy_has_six_categories_totalling_one_hundred() -> None:
    assert len(BOOK_CATEGORIES) == 6
    assert sum(category.weight for category in BOOK_CATEGORIES) == 100


def test_book_policy_keeps_book_pages_explicit() -> None:
    assert all(category.printed_ranges for category in BOOK_CATEGORIES)
    assert all(category.principle_id.startswith("PI-") for category in BOOK_CATEGORIES)
