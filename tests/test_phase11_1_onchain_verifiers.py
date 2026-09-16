from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from packages.payments.exceptions import (
    OnChainVerificationPending,
    PaymentIntegrityError,
    PaymentProviderError,
)
from packages.payments.onchain_verifiers import (
    TRON_USDT_CONTRACT,
    EvmERC20Verifier,
    EvmTokenIdentity,
    TronGridTRC20Verifier,
    TronTRC20Token,
)

pytestmark = pytest.mark.asyncio


async def test_tron_usdt_verifier_requires_solidified_success_and_exact_transfer() -> None:
    tx_hash = "a" * 64
    destination = "TJmmqjb1DK9TTZbQXzRQ2AuA94z4gKAPFh"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("TRON-PRO-API-KEY") == "test-key"
        if request.url.path == "/walletsolidity/gettransactioninfobyid":
            return httpx.Response(
                200,
                json={"id": tx_hash, "receipt": {"result": "SUCCESS"}},
            )
        if request.url.path == f"/v1/transactions/{tx_hash}/events":
            assert request.url.params.get("only_confirmed") == "true"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "transaction_id": tx_hash,
                            "contract_address": TRON_USDT_CONTRACT,
                            "event_name": "Transfer",
                            "block_timestamp": 1_700_000_000_000,
                            "result": {
                                "from": "TFrom1111111111111111111111111111111",
                                "to": destination,
                                "value": "12345678",
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = TronGridTRC20Verifier(
        token=TronTRC20Token(asset="USDT", contract_address=TRON_USDT_CONTRACT, decimals=6),
        api_key="test-key",
        client=client,
    )
    try:
        result = await verifier.verify_transaction(
            "TRON",
            tx_hash,
            expected_asset="USDT",
            expected_destination=destination,
        )
    finally:
        await client.aclose()

    assert result.asset == "USDT"
    assert result.asset_amount == Decimal("12.345678")
    assert result.destination_address == destination
    assert result.is_final is True
    assert result.succeeded is True
    assert result.observed_at is not None


async def test_tron_verifier_final_failed_receipt_does_not_require_transfer_event() -> None:
    tx_hash = "b" * 64
    destination = "TJmmqjb1DK9TTZbQXzRQ2AuA94z4gKAPFh"
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"id": tx_hash, "receipt": {"result": "REVERT"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = TronGridTRC20Verifier(
        token=TronTRC20Token(asset="USDT", contract_address=TRON_USDT_CONTRACT, decimals=6),
        client=client,
    )
    try:
        result = await verifier.verify_transaction(
            "TRON", tx_hash, expected_asset="USDT", expected_destination=destination
        )
    finally:
        await client.aclose()

    assert result.is_final is True
    assert result.succeeded is False
    assert result.asset_amount == Decimal(0)
    assert calls == ["/walletsolidity/gettransactioninfobyid"]


async def test_tron_verifier_keeps_unsolidified_transaction_pending() -> None:
    tx_hash = "c" * 64
    destination = "TJmmqjb1DK9TTZbQXzRQ2AuA94z4gKAPFh"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = TronGridTRC20Verifier(
        token=TronTRC20Token(asset="USDT", contract_address=TRON_USDT_CONTRACT, decimals=6),
        client=client,
    )
    try:
        with pytest.raises(OnChainVerificationPending, match="not solidified"):
            await verifier.verify_transaction(
                "TRON", tx_hash, expected_asset="USDT", expected_destination=destination
            )
    finally:
        await client.aclose()


async def test_tron_verifier_refuses_wrong_contract_or_destination_event() -> None:
    tx_hash = "d" * 64
    destination = "TJmmqjb1DK9TTZbQXzRQ2AuA94z4gKAPFh"

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/walletsolidity/gettransactioninfobyid":
            return httpx.Response(200, json={"id": tx_hash, "receipt": {"result": "SUCCESS"}})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "transaction_id": tx_hash,
                        "contract_address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE",
                        "event_name": "Transfer",
                        "result": {"to": destination, "value": "1000000"},
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = TronGridTRC20Verifier(
        token=TronTRC20Token(asset="USDT", contract_address=TRON_USDT_CONTRACT, decimals=6),
        client=client,
    )
    try:
        with pytest.raises(OnChainVerificationPending, match="not indexed"):
            await verifier.verify_transaction(
                "TRON", tx_hash, expected_asset="USDT", expected_destination=destination
            )
    finally:
        await client.aclose()


def _topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


async def test_evm_erc20_verifier_uses_exact_contract_chain_and_finalized_block() -> None:
    tx_hash = "0x" + "e" * 64
    token_contract = "0x" + "1" * 40
    destination = "0x" + "2" * 40
    transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    rpc_calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        method = payload["method"]
        rpc_calls.append(method)
        result: object
        if method == "eth_chainId":
            result = "0x38"  # BSC mainnet 56
        elif method == "eth_getTransactionReceipt":
            result = {
                "status": "0x1",
                "blockNumber": "0x64",
                "logs": [
                    {
                        "address": token_contract,
                        "topics": [transfer_topic, _topic_address("0x" + "3" * 40), _topic_address(destination)],
                        "data": hex(25 * 10**18),
                    }
                ],
            }
        elif method == "eth_getBlockByNumber" and payload["params"][0] == "finalized":
            result = {"number": "0x65"}
        elif method == "eth_blockNumber":
            result = "0x66"
        elif method == "eth_getBlockByNumber":
            result = {"number": "0x64", "timestamp": hex(1_700_000_000)}
        else:
            raise AssertionError(f"unexpected RPC method {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = EvmERC20Verifier(
        token=EvmTokenIdentity(
            network="BSC",
            chain_id=56,
            asset="BSC_USD",
            contract_address=token_contract,
            decimals=18,
        ),
        rpc_url="https://rpc.example.invalid",
        client=client,
    )
    try:
        result = await verifier.verify_transaction(
            "BSC",
            tx_hash,
            expected_asset="BSC_USD",
            expected_destination=destination,
        )
    finally:
        await client.aclose()

    assert result.succeeded is True
    assert result.is_final is True
    assert result.asset_amount == Decimal(25)
    assert result.confirmations == 3
    assert result.raw_data["token_contract"] == token_contract
    assert rpc_calls[:4] == [
        "eth_chainId",
        "eth_getTransactionReceipt",
        "eth_getBlockByNumber",
        "eth_blockNumber",
    ]


async def test_evm_verifier_rejects_rpc_chain_mismatch_before_settlement() -> None:
    tx_hash = "0x" + "f" * 64
    token_contract = "0x" + "1" * 40
    destination = "0x" + "2" * 40

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": "0x1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    verifier = EvmERC20Verifier(
        token=EvmTokenIdentity(
            network="BSC",
            chain_id=56,
            asset="BSC_USD",
            contract_address=token_contract,
            decimals=18,
        ),
        rpc_url="https://rpc.example.invalid",
        client=client,
    )
    try:
        with pytest.raises(PaymentIntegrityError, match="chain id"):
            await verifier.verify_transaction(
                "BSC", tx_hash, expected_asset="BSC_USD", expected_destination=destination
            )
    finally:
        await client.aclose()


async def test_evm_verifier_refuses_redirects() -> None:
    tx_hash = "0x" + "1" * 64
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"Location": "https://evil.invalid"}))
    )
    verifier = EvmERC20Verifier(
        token=EvmTokenIdentity(
            network="BSC",
            chain_id=56,
            asset="BSC_USD",
            contract_address="0x" + "a" * 40,
            decimals=18,
        ),
        rpc_url="https://rpc.example.invalid",
        client=client,
    )
    try:
        with pytest.raises(PaymentProviderError, match="redirects"):
            await verifier.verify_transaction(
                "BSC",
                tx_hash,
                expected_asset="BSC_USD",
                expected_destination="0x" + "b" * 40,
            )
    finally:
        await client.aclose()


async def test_configured_verifier_registry_keeps_chain_authority_installation_owned() -> None:
    from types import SimpleNamespace

    from packages.payments.onchain_verifiers import register_configured_onchain_verifiers
    from packages.payments.platform import OnChainVerifierRegistry

    settings = SimpleNamespace(
        payment_tron_usdt_enabled=True,
        payment_trongrid_api_key="installation-secret",
        payment_trongrid_base_url="https://api.trongrid.io",
        payment_tron_timeout_seconds=10.0,
        payment_bsc_token_enabled=True,
        payment_bsc_rpc_url="https://bsc-rpc.example.invalid",
        payment_bsc_token_asset="BINANCE_PEG_USD",
        payment_bsc_token_contract="0x" + "a" * 40,
        payment_bsc_token_decimals=18,
        payment_bsc_chain_id=56,
        payment_bsc_timeout_seconds=10.0,
    )
    registry = OnChainVerifierRegistry()

    registered = register_configured_onchain_verifiers(registry, settings)

    assert registered == ("TRON", "BSC")
    assert set(registry.registered_networks()) == {"BSC", "TRON"}
    tron = registry.get("TRON")
    bsc = registry.get("BSC")
    assert tron.token.contract_address == TRON_USDT_CONTRACT
    assert bsc.token.asset == "BINANCE_PEG_USD"
    assert bsc.token.contract_address == "0x" + "a" * 40
