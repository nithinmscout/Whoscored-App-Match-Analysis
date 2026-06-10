# .\venv-win\Scripts\activate
# uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# C:\Program Files\Google\Chromium\chrome.exe
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import analysis, debug, loader, processed, spatial, viewer

app = FastAPI(title="WhoScored Match Analysis API")


DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def get_cors_origins() -> list[str]:
    raw_origins = os.environ.get("CORS_ALLOW_ORIGINS", "")
    deployed_origins = [
        origin.strip().rstrip("/")
        for origin in raw_origins.split(",")
        if origin.strip()
    ]

    return list(dict.fromkeys(DEFAULT_CORS_ORIGINS + deployed_origins))


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(loader.router, prefix="/api/loader", tags=["Loader"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["Analysis"])
app.include_router(spatial.router, prefix="/api/spatial", tags=["Spatial"])
app.include_router(viewer.router, prefix="/api/viewer", tags=["Viewer"])
app.include_router(debug.router, prefix="/api/debug", tags=["Debug"])
app.include_router(processed.router, prefix="/api/processed", tags=["Processed"])


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "WhoScored Match Analysis backend is running"}
