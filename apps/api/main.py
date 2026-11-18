from fastapi import FastAPI
from pydantic import BaseModel

from apps.api.evals import router as evals_router
from apps.api.queue import router as queue_router

app = FastAPI(title="Bank Agent Harness API")
app.include_router(queue_router)
app.include_router(evals_router)


class HealthResponse(BaseModel):
    status: str = "ok"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
