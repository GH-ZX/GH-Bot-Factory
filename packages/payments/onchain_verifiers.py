from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from packages.payments.exceptions import (
    OnChainVerificationPending,
    PaymentIntegrityError,
    PaymentProviderError,
)
from packages.payments.platform import OnChainVerificationResult

_TRON_TX_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_EVM_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Tether's official TRON USDt contract. Keep this constant visible/auditable rather
# than accepting a tenant-supplied contract name or symbol.
TRON_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class _BoundedHttpMixin:
    max_response_bytes = 256 * 1024

    @staticmethod
    def _assert_response_size(response: httpx.Response, max_bytes: int) -> None:
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise PaymentProviderError("On-chain verifier response exceeds size limit.")
            except ValueError:
                pass
        if len(response.content) > max_bytes:
            raise PaymentProviderError("On-chain verifier response exceeds size limit.")

    @classmethod
    def _json_object(cls, response: httpx.Response) -> dict[str, Any]:
        cls._assert_response_size(response, cls.max_response_bytes)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentProviderError("On-chain verifier returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise PaymentProviderError("On-chain verifier returned an invalid payload shape.")
        return payload


# ---- TRON address helpers -------------------------------------------------
# Implement Base58Check locally so the verifier does not depend on a wallet SDK and can
# compare TronGrid event hex addresses with the merchant's Base58 destination safely.
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {char: index for index, char in enumerate(_B58_ALPHABET)}


def _b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        try:
            number = number * 58 + _B58_INDEX[char]
        except KeyError as exc:
            raise PaymentIntegrityError("Invalid TRON Base58 address.") from exc
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading + raw


def _tron_base58_to_hex(address: str) -> str:
    decoded = _b58decode(address)
    if len(decoded) != 25:
        raise PaymentIntegrityError("Invalid TRON address length.")
    payload, checksum = decoded[:-4], decoded[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected or len(payload) != 21 or payload[0] != 0x41:
        raise PaymentIntegrityError("Invalid TRON address checksum/network prefix.")
    return payload.hex().lower()


def _normalize_tron_address(address: str) -> str:
    candidate = address.strip()
    if candidate.startswith("T"):
        return _tron_base58_to_hex(candidate)
    raw = candidate.lower().removeprefix("0x")
    if not _HEX_RE.fullmatch(raw):
        raise PaymentIntegrityError("Invalid TRON address encoding.")
    # Indexed event parameters are commonly ABI-padded to 32 bytes. TRON addresses are
    # the final 20 bytes with network prefix 0x41 reattached.
    if len(raw) == 64:
        raw = "41" + raw[-40:]
    elif len(raw) == 40:
        raw = "41" + raw
    if len(raw) != 42 or not raw.startswith("41"):
        raise PaymentIntegrityError("Invalid TRON address encoding.")
    return raw


@dataclass(frozen=True)
class TronTRC20Token:
    asset: str
    contract_address: str
    decimals: int


class TronGridTRC20Verifier(_BoundedHttpMixin):
    """Verify one explicitly configured TRC-20 token using TronGrid + SolidityNode.

    Token identity is installation-owned. Tenant configuration can select this verifier's
    network/asset, but cannot substitute a token contract or RPC endpoint.
    """

    verifier_name = "trongrid-trc20"

    def __init__(
        self,
        *,
        token: TronTRC20Token,
        api_key: str | None = None,
        base_url: str = "https://api.trongrid.io",
        timeout_seconds: float = 12.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token.asset.strip() or token.decimals < 0 or token.decimals > 36:
            raise ValueError("Invalid TRC-20 token identity.")
        # Validate the trusted contract once at construction time.
        _normalize_tron_address(token.contract_address)
        self.token = TronTRC20Token(
            asset=token.asset.strip().upper(),
            contract_address=token.contract_address.strip(),
            decimals=token.decimals,
        )
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("https://", "http://")):
            raise ValueError("TronGrid base URL must be HTTP(S).")
        self.api_key = (api_key or "").strip() or None
        self.timeout_seconds = float(timeout_seconds)
        if not 2 <= self.timeout_seconds <= 30:
            raise ValueError("TRON verifier timeout must be between 2 and 30 seconds.")
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if self.api_key:
            headers["TRON-PRO-API-KEY"] = self.api_key
        owned = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            if response.is_redirect:
                raise PaymentProviderError("TRON verifier refuses HTTP redirects.")
            response.raise_for_status()
            return self._json_object(response)
        except httpx.HTTPStatusError as exc:
            raise PaymentProviderError(
                f"TRON verifier HTTP error {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise PaymentProviderError("TRON verifier network request failed.") from exc
        finally:
            if owned:
                await client.aclose()

    @staticmethod
    def _event_result(event: dict[str, Any]) -> dict[str, Any]:
        result = event.get("result")
        return result if isinstance(result, dict) else {}

    def _parse_transfer_event(
        self,
        payload: dict[str, Any],
        *,
        tx_hash: str,
        expected_destination: str,
    ) -> tuple[Decimal, datetime | None]:
        events = payload.get("data")
        if not isinstance(events, list):
            raise PaymentProviderError("TronGrid event response is malformed.")
        expected_contract = _normalize_tron_address(self.token.contract_address)
        expected_to = _normalize_tron_address(expected_destination)
        matches: list[tuple[Decimal, datetime | None]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            if str(item.get("transaction_id") or "").lower() != tx_hash.lower():
                continue
            if str(item.get("event_name") or "") != "Transfer":
                continue
            try:
                if _normalize_tron_address(str(item.get("contract_address") or "")) != expected_contract:
                    continue
            except PaymentIntegrityError:
                continue
            result = self._event_result(item)
            to_value = result.get("to", result.get("1"))
            amount_value = result.get("value", result.get("2"))
            if to_value is None or amount_value is None:
                continue
            try:
                if _normalize_tron_address(str(to_value)) != expected_to:
                    continue
                amount_text = str(amount_value).strip()
                integer_amount = int(amount_text, 16) if amount_text.lower().startswith("0x") else int(amount_text, 10)
            except (PaymentIntegrityError, ValueError):
                continue
            amount = Decimal(integer_amount) / (Decimal(10) ** self.token.decimals)
            timestamp_raw = item.get("block_timestamp")
            observed_at = None
            if timestamp_raw is not None:
                try:
                    observed_at = datetime.fromtimestamp(int(timestamp_raw) / 1000, tz=UTC)
                except (TypeError, ValueError, OSError):
                    observed_at = None
            matches.append((amount, observed_at))
        if not matches:
            raise OnChainVerificationPending("TRC-20 transfer event is not indexed yet.")
        if len(matches) != 1:
            raise PaymentIntegrityError(
                "Transaction contains multiple matching token transfers to the destination; manual review required."
            )
        return matches[0]

    async def verify_transaction(
        self,
        network: str,
        tx_hash: str,
        *,
        expected_asset: str | None = None,
        expected_destination: str | None = None,
    ) -> OnChainVerificationResult:
        if network.strip().upper() != "TRON":
            raise PaymentIntegrityError("TRON verifier received a different network.")
        if not _TRON_TX_RE.fullmatch(tx_hash.strip()):
            raise PaymentIntegrityError("TRON transaction hash must be 64 hexadecimal characters.")
        if expected_asset and expected_asset.strip().upper() != self.token.asset:
            raise PaymentIntegrityError("Requested asset does not match trusted TRC-20 token identity.")
        if not expected_destination:
            raise PaymentIntegrityError("TRON verification requires an expected destination address.")
        # Fail before network I/O if the merchant destination itself is malformed.
        _normalize_tron_address(expected_destination)

        tx_hash = tx_hash.lower()
        receipt = await self._request(
            "POST",
            "/walletsolidity/gettransactioninfobyid",
            json={"value": tx_hash},
        )
        if not receipt or not receipt.get("id"):
            raise OnChainVerificationPending("TRON transaction is not solidified yet.")
        receipt_data = receipt.get("receipt")
        if not isinstance(receipt_data, dict):
            raise OnChainVerificationPending("TRON solidified receipt is not available yet.")
        result = str(receipt_data.get("result") or "").upper()
        if result != "SUCCESS":
            # The SolidityNode endpoint only returns solidified receipts. A final non-success
            # result is authoritative failure even though a Transfer event will normally be absent.
            return OnChainVerificationResult(
                network="TRON",
                tx_hash=tx_hash,
                asset=self.token.asset,
                destination_address=expected_destination,
                asset_amount=Decimal(0),
                confirmations=1,
                is_final=True,
                succeeded=False,
                raw_data={"receipt_result": result or "UNKNOWN"},
            )

        event_payload = await self._request(
            "GET",
            f"/v1/transactions/{tx_hash}/events",
            params={"only_confirmed": "true"},
        )
        amount, observed_at = self._parse_transfer_event(
            event_payload,
            tx_hash=tx_hash,
            expected_destination=expected_destination,
        )
        return OnChainVerificationResult(
            network="TRON",
            tx_hash=tx_hash,
            asset=self.token.asset,
            destination_address=expected_destination,
            asset_amount=amount,
            confirmations=1,
            is_final=True,
            succeeded=True,
            observed_at=observed_at,
            raw_data={"receipt_result": result},
        )


@dataclass(frozen=True)
class EvmTokenIdentity:
    network: str
    chain_id: int
    asset: str
    contract_address: str
    decimals: int


class EvmERC20Verifier(_BoundedHttpMixin):
    """Generic installation-owned ERC-20 verifier using JSON-RPC finality.

    This class deliberately does not contain a "USDT BEP20" preset. The installation must
    provide the exact token contract/decimals/asset identity, preventing a display symbol from
    becoming token authority.
    """

    verifier_name = "evm-erc20"

    def __init__(
        self,
        *,
        token: EvmTokenIdentity,
        rpc_url: str,
        timeout_seconds: float = 12.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        contract = token.contract_address.strip().lower()
        if not _EVM_ADDRESS_RE.fullmatch(contract):
            raise ValueError("EVM token contract must be a 20-byte 0x address.")
        if token.chain_id <= 0 or token.decimals < 0 or token.decimals > 36:
            raise ValueError("Invalid EVM token identity.")
        self.token = EvmTokenIdentity(
            network=token.network.strip().upper(),
            chain_id=token.chain_id,
            asset=token.asset.strip().upper(),
            contract_address=contract,
            decimals=token.decimals,
        )
        self.rpc_url = rpc_url.strip()
        if not self.rpc_url.startswith(("https://", "http://")):
            raise ValueError("EVM RPC URL must be HTTP(S).")
        self.timeout_seconds = float(timeout_seconds)
        if not 2 <= self.timeout_seconds <= 30:
            raise ValueError("EVM verifier timeout must be between 2 and 30 seconds.")
        self._client = client
        self._rpc_id = 0

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        owned = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            response = await client.post(self.rpc_url, json=payload)
            if response.is_redirect:
                raise PaymentProviderError("EVM verifier refuses HTTP redirects.")
            response.raise_for_status()
            body = self._json_object(response)
        except httpx.HTTPStatusError as exc:
            raise PaymentProviderError(
                f"EVM verifier HTTP error {exc.response.status_code}."
            ) from exc
        except httpx.HTTPError as exc:
            raise PaymentProviderError("EVM verifier network request failed.") from exc
        finally:
            if owned:
                await client.aclose()
        if body.get("error") is not None:
            raise PaymentProviderError("EVM RPC returned an error response.")
        return body.get("result")

    @staticmethod
    def _parse_hex_int(value: Any, field: str) -> int:
        if not isinstance(value, str) or not value.startswith("0x"):
            raise PaymentProviderError(f"EVM RPC {field} is malformed.")
        try:
            return int(value, 16)
        except ValueError as exc:
            raise PaymentProviderError(f"EVM RPC {field} is malformed.") from exc

    async def verify_transaction(
        self,
        network: str,
        tx_hash: str,
        *,
        expected_asset: str | None = None,
        expected_destination: str | None = None,
    ) -> OnChainVerificationResult:
        if network.strip().upper() != self.token.network:
            raise PaymentIntegrityError("EVM verifier received a different network.")
        if expected_asset and expected_asset.strip().upper() != self.token.asset:
            raise PaymentIntegrityError("Requested asset does not match trusted EVM token identity.")
        if not _EVM_TX_RE.fullmatch(tx_hash.strip()):
            raise PaymentIntegrityError("EVM transaction hash must be 0x + 64 hexadecimal characters.")
        destination = (expected_destination or "").strip().lower()
        if not _EVM_ADDRESS_RE.fullmatch(destination):
            raise PaymentIntegrityError("EVM verification requires a valid expected destination address.")

        chain_id = self._parse_hex_int(await self._rpc("eth_chainId", []), "chainId")
        if chain_id != self.token.chain_id:
            raise PaymentIntegrityError("EVM RPC chain id does not match configured payment network.")

        receipt = await self._rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt is None:
            raise OnChainVerificationPending("EVM transaction receipt is not available yet.")
        if not isinstance(receipt, dict):
            raise PaymentProviderError("EVM transaction receipt is malformed.")
        status = self._parse_hex_int(receipt.get("status"), "receipt status")
        block_number = self._parse_hex_int(receipt.get("blockNumber"), "receipt blockNumber")

        finalized = await self._rpc("eth_getBlockByNumber", ["finalized", False])
        if not isinstance(finalized, dict) or finalized.get("number") is None:
            raise OnChainVerificationPending("EVM finalized block is not available from the configured RPC.")
        finalized_number = self._parse_hex_int(finalized.get("number"), "finalized block number")
        latest = await self._rpc("eth_blockNumber", [])
        latest_number = self._parse_hex_int(latest, "latest block number")
        confirmations = max(0, latest_number - block_number + 1)
        is_final = block_number <= finalized_number
        if status != 1:
            if not is_final:
                raise OnChainVerificationPending("Failed EVM receipt is not finalized yet.")
            return OnChainVerificationResult(
                network=self.token.network,
                tx_hash=tx_hash.lower(),
                asset=self.token.asset,
                destination_address=destination,
                asset_amount=Decimal(0),
                confirmations=confirmations,
                is_final=True,
                succeeded=False,
                raw_data={
                    "chain_id": self.token.chain_id,
                    "token_contract": self.token.contract_address,
                    "receipt_block": block_number,
                    "finalized_block": finalized_number,
                },
            )

        transfer_amounts: list[int] = []
        logs = receipt.get("logs")
        if not isinstance(logs, list):
            raise PaymentProviderError("EVM transaction receipt logs are malformed.")
        expected_topic_to = "0" * 24 + destination.removeprefix("0x")
        for log in logs:
            if not isinstance(log, dict):
                continue
            if str(log.get("address") or "").lower() != self.token.contract_address:
                continue
            topics = log.get("topics")
            if not isinstance(topics, list) or len(topics) < 3:
                continue
            if str(topics[0]).lower() != _ERC20_TRANSFER_TOPIC:
                continue
            topic_to = str(topics[2]).lower().removeprefix("0x")
            if topic_to != expected_topic_to:
                continue
            data = str(log.get("data") or "")
            transfer_amounts.append(self._parse_hex_int(data, "Transfer value"))

        if not transfer_amounts:
            raise PaymentIntegrityError(
                "Transaction receipt contains no transfer of the configured token to the expected destination."
            )
        if len(transfer_amounts) != 1:
            raise PaymentIntegrityError(
                "Transaction contains multiple matching token transfers to the destination; manual review required."
            )
        amount = Decimal(transfer_amounts[0]) / (Decimal(10) ** self.token.decimals)

        observed_at = None
        block = await self._rpc("eth_getBlockByNumber", [hex(block_number), False])
        if isinstance(block, dict) and block.get("timestamp") is not None:
            timestamp = self._parse_hex_int(block.get("timestamp"), "block timestamp")
            observed_at = datetime.fromtimestamp(timestamp, tz=UTC)

        return OnChainVerificationResult(
            network=self.token.network,
            tx_hash=tx_hash.lower(),
            asset=self.token.asset,
            destination_address=destination,
            asset_amount=amount,
            confirmations=confirmations,
            is_final=is_final,
            succeeded=status == 1,
            observed_at=observed_at,
            raw_data={
                "chain_id": self.token.chain_id,
                "token_contract": self.token.contract_address,
                "receipt_block": block_number,
                "finalized_block": finalized_number,
            },
        )


def register_configured_onchain_verifiers(registry: Any, app_settings: Any) -> tuple[str, ...]:
    """Register installation-owned chain verifiers from validated application settings.

    Nothing in tenant/payment-method rows can influence RPC endpoints, token contracts, API
    keys, decimals or chain ids. This is the trust boundary between merchant configuration and
    authoritative blockchain evidence.
    """

    registered: list[str] = []
    if bool(app_settings.payment_tron_usdt_enabled):
        verifier = TronGridTRC20Verifier(
            token=TronTRC20Token(
                asset="USDT",
                contract_address=TRON_USDT_CONTRACT,
                decimals=6,
            ),
            api_key=app_settings.payment_trongrid_api_key,
            base_url=app_settings.payment_trongrid_base_url,
            timeout_seconds=app_settings.payment_tron_timeout_seconds,
        )
        registry.register("TRON", verifier)
        registered.append("TRON")

    if bool(app_settings.payment_bsc_token_enabled):
        if not app_settings.payment_bsc_rpc_url:
            raise ValueError("PAYMENT_BSC_RPC_URL is required when BSC token verifier is enabled.")
        if not app_settings.payment_bsc_token_asset:
            raise ValueError("PAYMENT_BSC_TOKEN_ASSET is required when BSC token verifier is enabled.")
        if not app_settings.payment_bsc_token_contract:
            raise ValueError("PAYMENT_BSC_TOKEN_CONTRACT is required when BSC token verifier is enabled.")
        verifier = EvmERC20Verifier(
            token=EvmTokenIdentity(
                network="BSC",
                chain_id=int(app_settings.payment_bsc_chain_id),
                asset=str(app_settings.payment_bsc_token_asset),
                contract_address=str(app_settings.payment_bsc_token_contract),
                decimals=int(app_settings.payment_bsc_token_decimals),
            ),
            rpc_url=str(app_settings.payment_bsc_rpc_url),
            timeout_seconds=float(app_settings.payment_bsc_timeout_seconds),
        )
        registry.register("BSC", verifier)
        registered.append("BSC")

    return tuple(registered)
