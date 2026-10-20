"""Cognito FastAPI application. Routes delegate to narrow service modules."""
from contextlib import asynccontextmanager
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import database, initialize_database
from .routers import auth, chat, conversations, learning
from .services.language_model import load_model
from .settings import ROOT, settings

logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger("cognito")


@asynccontextmanager
async def lifespan(app:FastAPI):
    if settings.production and (len(settings.session_secret)<32 or settings.session_secret.startswith("replace-with")):
        raise RuntimeError("LINGUAAI_SESSION_SECRET must be at least 32 characters in production.")
    initialize_database()
    app.state.language_model=load_model()
    logger.info("Application started; environment=%s AI provider configured=%s language model loaded=%s",settings.environment,settings.provider_configured,app.state.language_model is not None)
    yield


app=FastAPI(title="Cognito",version="2.0.0",description="A multilingual language-learning assistant.",lifespan=lifespan,docs_url="/api/docs" if not settings.production else None,redoc_url=None,openapi_url="/api/openapi.json" if not settings.production else None)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(learning.router)
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request:Request,exc:RequestValidationError):
    # Do not echo submitted values such as passwords, answers, or private messages.
    errors=[{"field":".".join(str(part) for part in item.get("loc",())[1:]),"message":item.get("msg","Invalid value.")} for item in exc.errors()]
    return JSONResponse({"detail":errors},status_code=422)


@app.middleware("http")
async def security_headers(request:Request,call_next):
    content_length=request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length)>64_000:
        return JSONResponse({"detail":"Request body is too large."},status_code=413)
    try:
        response=await call_next(request)
    except Exception:
        # Keep the URL, method, and error type; never log request bodies or credentials.
        logger.exception("Unhandled request error: %s %s",request.method,request.url.path)
        raise
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]="camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; style-src-attr 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    if request.url.path.startswith("/api/") or request.url.path=="/": response.headers["Cache-Control"]="no-store"
    return response


@app.get("/",include_in_schema=False)
def index():
    return FileResponse(ROOT/"static"/"index.html")


@app.get("/api/health")
def health():
    try:
        with database() as conn: conn.execute("SELECT 1").fetchone()
        db_state="ok"
    except Exception:
        db_state="unavailable"
    return {"status":"ok" if db_state=="ok" else "degraded","service":"Cognito","database":db_state,"tutoring":"configured" if settings.provider_configured else "optional provider not configured"}


@app.get("/api/ready")
def readiness():
    try:
        with database() as conn: conn.execute("SELECT 1").fetchone()
        return {"ready":True}
    except Exception:
        return JSONResponse({"ready":False},status_code=503)
