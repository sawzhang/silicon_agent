from __future__ import annotations

from app.schemas.common import PaginatedResponse


def test_paginated_response_generic_model():
    model = PaginatedResponse[int](
        items=[1, 2, 3],
        total=3,
        page=1,
        page_size=10,
        total_pages=1,
    )

    assert model.items == [1, 2, 3]
    assert model.total == 3
    assert model.page == 1
    assert model.page_size == 10
    assert model.total_pages == 1
