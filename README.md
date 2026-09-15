# GH-Bot-Factory

Multi-tenant Telegram commerce Bot Factory.

## Project Structure

```
.
├── apps/
│   ├── admin/
│   ├── api/
│   ├── bot-runtime/
│   └── worker/
├── packages/
│   ├── commerce/
│   ├── core/
│   ├── factory/
│   ├── payments/
│   ├── providers/
│   ├── telegram/
│   └── tenants/
├── infra/
│   └── docker/
├── docs/
│   ├── architecture/
│   └── decisions/
├── scripts/
└── tests/
```

## Getting Started

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Set up virtual environment and install dependencies:
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -e .
   ```
