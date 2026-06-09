"""Application entry point."""

from fastapi import FastAPI

app = FastAPI(
    title="Production Control",
    version="0.1.0",
)


@app.get("/health", tags=["system"])  # type: ignore[misc]
async def health_check() -> dict[str, str]:
    """Liveness probe для Docker healthcheck."""
    return {"status": "ok"}
