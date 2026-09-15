from decimal import Decimal

import pytest

from packages.providers.interface import (
    Provider,
    ProviderPurchaseRequest,
)
from packages.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_provider_protocol_conformance():
    mock_prov = MockProvider(name="AlphaProvider", balance=Decimal("1000.00"))

    # Runtime protocol check
    assert isinstance(mock_prov, Provider)

    # 1. Check products
    products = await mock_prov.get_products()
    assert len(products) >= 2
    assert products[0].price > Decimal("0.00")

    # 2. Check initial balance
    balance = await mock_prov.get_balance()
    assert balance.balance == Decimal("1000.00")

    # 3. Perform purchase
    req = ProviderPurchaseRequest(
        external_product_id="prod-101",
        quantity=10,
        recipient="@telegram_buyer",
    )
    result = await mock_prov.purchase(req)
    assert result.status == "COMPLETED"
    assert result.total_cost == Decimal("12.50")

    # 4. Check reduced balance
    balance_after = await mock_prov.get_balance()
    assert balance_after.balance == Decimal("987.50")

    # 5. Query order status
    order_status = await mock_prov.get_order(result.external_order_id)
    assert order_status.is_completed is True
    assert order_status.status == "COMPLETED"
