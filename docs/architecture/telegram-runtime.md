# Telegram Runtime Architecture

The Telegram Runtime is a modular, multi-tenant runtime built on top of **Aiogram 3**.

It enables running hundreds of white-label Telegram commerce bots concurrently from a single codebase while preserving strict tenant isolation, auditable logging, and zero-secret exposure.

---

## 1. Update Processing Pipeline

Each incoming Telegram update flows through an outer middleware chain before reaching any handler:

```text
Incoming Telegram Update
          │
          ▼
1. CorrelationMiddleware
   - Generates/extracts unique UUID4 correlation_id
          │
          ▼
2. TenantResolutionMiddleware
   - Resolves Bot by registered bot_id
   - Enforces bot.is_enabled (aborts if disabled)
   - Resolves or provisions tenant-scoped User and TenantTelegramUser binding
   - Builds immutable TenantContext and injects into handler data
          │
          ▼
3. UpdateLoggingMiddleware
   - Logs start/completion with elapsed milliseconds and correlation ID
   - Guaranteed zero secret/token logging
          │
          ▼
4. ErrorHandlingMiddleware
   - Intercepts domain errors (InsufficientFunds, InvalidStateTransition, etc.)
   - Emits sanitized, user-friendly responses
   - Logs full stack traces with correlation ID internally
          │
          ▼
5. Modular Feature Routers
   - start_router (/start)
   - menu_router (/menu, nav:menu)
   - catalog_router (/catalog, nav:catalog)
   - orders_router (/orders, nav:orders)
   - account_router (/account, nav:account)
```

---

## 2. Tenant Context Propagation

The runtime rejects reliance on global mutable state or implicit session variables. Every handler explicitly receives `tenant_context: TenantContext`:

```python
@dataclass
class TenantContext:
    bot_id: uuid.UUID
    tenant_id: uuid.UUID
    telegram_bot_id: int
    telegram_user_id: int
    user_id: uuid.UUID | None
    correlation_id: str
    tenant_name: str
    display_name: str
    bot_username: str | None
    language_code: str | None
    config: dict[str, Any]
```

### Module Feature Flags
Tenants can enable or disable specific modules (`catalog`, `orders`, `account`) via their bot config:
```python
if tenant_context.is_module_enabled("catalog"):
    ...
```

---

## 3. FSM State Scoping

Conversational state in Aiogram is managed via `TenantFSMHelper`. 

To guarantee that state never leaks between tenants or bots (even if a user interacts with multiple stores concurrently), the storage key is scoped with composite tenant and bot metadata:

```python
key = StorageKey(
    bot_id=tenant_context.telegram_bot_id,
    chat_id=chat_id,
    user_id=user_id,
    destiny=f"tenant:{tenant_context.tenant_id}:bot:{tenant_context.bot_id}",
)
```

---

## 4. Polling vs. Webhook Architecture

The runtime abstraction is transport-agnostic:
- **Development / Local Staging:** `BotRuntimeManager.start_polling()` manages concurrent polling loops for all active bots using `asyncio.Task`.
- **Production Webhook Mode:** Handlers and middlewares do not know or care whether an update originated from polling or webhooks. The FastAPI server can expose a parameterized endpoint:
  ```text
  POST /api/v1/telegram/webhook/{bot_id}
  ```
  Incoming payloads are fed directly into the bot's pre-configured `Dispatcher.feed_update(bot, update)`.
