from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="DocLink mock 1C", docs_url=None, redoc_url=None, openapi_url=None)


class MockHealth(BaseModel):
    status: str


@app.get("/health", response_model=MockHealth)
def health() -> MockHealth:
    return MockHealth(status="ok")


@app.get("/odata/standard.odata/Catalog_Документы")
def list_documents() -> dict[str, list[dict[str, str]]]:
    return {"value": []}
