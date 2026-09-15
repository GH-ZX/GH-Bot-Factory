from fastapi import FastAPI

from apps.api.v1.auth import router as auth_router
from apps.api.v1.payments import router as payments_router

app = FastAPI(
    title="GH Bot Factory API",
    version="0.1.0",
)

# Health endpoint
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# API v1 routes
app.include_router(payments_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
