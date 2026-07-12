from decimal import Decimal

import pytest


@pytest.fixture
def decimal_amount():
    def make_amount(value: str) -> Decimal:
        if not isinstance(value, str):
            raise TypeError("Financial test amounts must be constructed from strings")
        return Decimal(value)

    return make_amount
