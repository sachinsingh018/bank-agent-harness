from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Bank Agent Harness API")


class HealthResponse(BaseModel):
    status: str = "ok"


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()
