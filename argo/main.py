from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Argo", version="0.0.1")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
