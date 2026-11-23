import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.api.auth import router as auth_router
from app.api.me import router as me_router
from app.api.trips import router as trips_router
from app.core.config import settings
from app.services.llm_client import LLMOutputError

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO, which drowns the app's own logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="Adieu API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(me_router)
app.include_router(trips_router)


@app.get("/health")
def health():
    return {"ok": True, "llm_model": settings.LLM_MODEL}


@app.exception_handler(APIConnectionError)
@app.exception_handler(APITimeoutError)
@app.exception_handler(APIStatusError)
def llm_unavailable(request: Request, exc: Exception):
    logging.getLogger(__name__).error("LLM request failed: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "I can't think straight right now — please try again in a moment."})


@app.exception_handler(LLMOutputError)
def llm_bad_output(request: Request, exc: LLMOutputError):
    return JSONResponse(status_code=502, content={"detail": "The AI gave an unusable answer. Please try again."})
