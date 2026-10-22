"""API local de controle; importar este módulo não inicia bots nem executa ADB."""

from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.device_profiles import ProfileStore, settings_schema, template_key
from dashboard.supervisor import BotSupervisor


STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 300_000


def access_token(state_root):
    supplied = os.environ.get("EATVENTURE_WEB_TOKEN")
    if supplied:
        if len(supplied) < 24 or len(supplied) > 512:
            raise ValueError("EATVENTURE_WEB_TOKEN deve ter entre 24 e 512 caracteres.")
        return supplied
    path = Path(state_root) / "dashboard.token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_urlsafe(32)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(token + "\n")
    if len(token) < 24 or len(token) > 512:
        raise ValueError(f"Token local inválido em {path}.")
    return token


def template_catalog(root, state_root, store):
    roots = [Path(root) / "src" / "vision" / "templates"]
    for profile in store.list_profiles():
        digest = hashlib.sha256(profile["device_id"].encode()).hexdigest()
        roots.append(Path(state_root) / "device-data" / digest / "templates")
    categories = {}
    total = 0
    for base in roots:
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir() or (
                folder.name != "default"
                and re.fullmatch(r"[1-9]\d*x[1-9]\d*", folder.name) is None
            ):
                continue
            for category in sorted(folder.iterdir()):
                if not category.is_dir() or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category.name) is None:
                    continue
                for item in sorted(category.glob("*.png")):
                    if not item.is_file():
                        continue
                    key = template_key(category.name, item.name)
                    group = categories.setdefault(category.name, {})
                    group[key] = group.get(key, 0) + 1
                    total += 1
    return {
        "total": total,
        "categories": [
            {
                "name": name,
                "count": sum(items.values()),
                "threshold": config.CATEGORY_THRESHOLDS.get(name, config.SHAPE_THRESHOLD),
                "uses_global": name not in config.CATEGORY_THRESHOLDS,
                "items": [
                    {
                        "key": key, "name": key.split("/", 1)[1], "count": count,
                        "threshold": config.CATEGORY_THRESHOLDS.get(name, config.SHAPE_THRESHOLD),
                    }
                    for key, count in sorted(items.items())
                ],
            }
            for name, items in sorted(categories.items())
        ],
    }


def create_app(root=None, state_root=None, token=None, supervisor=None):
    root = Path(root or config.PROJECT_ROOT).resolve()
    state_root = Path(state_root or root / "local").resolve()
    store = supervisor.store if supervisor is not None else ProfileStore(state_root / "devices")
    supervisor = supervisor or BotSupervisor(root, state_root=state_root, store=store)
    token = token or access_token(state_root)
    catalog_lock = threading.Lock()
    catalog_cache = {"at": 0.0, "value": None}

    def catalog():
        with catalog_lock:
            if time.monotonic() - catalog_cache["at"] > 10 or catalog_cache["value"] is None:
                catalog_cache.update(
                    at=time.monotonic(), value=template_catalog(root, state_root, store),
                )
            return catalog_cache["value"]

    @asynccontextmanager
    async def lifespan(app):
        yield
        # O encerramento também funciona ao executar como serviço no Linux.
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(supervisor.shutdown)

    app = FastAPI(title="EatVenture Control", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.supervisor = supervisor

    @app.middleware("http")
    async def authorize(request, call_next):
        if request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            presented = authorization[7:] if authorization.startswith("Bearer ") else ""
            if len(presented) > 512 or not hmac.compare_digest(presented.encode(), token.encode()):
                return JSONResponse({"detail": "Token de acesso inválido."}, status_code=401)
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    valid_origin = (parsed.scheme, parsed.netloc) == (request.url.scheme, request.url.netloc)
                except ValueError:
                    valid_origin = False
                if not valid_origin:
                    return JSONResponse({"detail": "Origem não autorizada."}, status_code=403)
            if request.method in ("POST", "PUT", "DELETE"):
                try:
                    size = int(request.headers.get("content-length", "0"))
                except ValueError:
                    return JSONResponse({"detail": "Tamanho de requisição inválido."}, status_code=400)
                if size < 0 or size > MAX_BODY:
                    return JSONResponse({"detail": "Requisição muito grande."}, status_code=413)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; object-src 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "Dispositivo não encontrado."}, status_code=404)

    @app.exception_handler(RuntimeError)
    async def unavailable(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    def known(device_id):
        if not any(item["id"] == device_id for item in supervisor.list_devices()):
            raise KeyError(device_id)

    async def body(request):
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BODY:
                raise HTTPException(status_code=413, detail="Requisição muito grande.")
            raw.extend(chunk)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ValueError("JSON inválido.") from error
        if not isinstance(value, dict):
            raise ValueError("Envie um objeto JSON.")
        return value

    @app.get("/api/session")
    def session():
        return {"authenticated": True}

    @app.get("/api/state")
    def state(refresh: bool = False):
        return {
            "devices": supervisor.list_devices(refresh=refresh),
            "schema": settings_schema(vars(config)),
            "templates": catalog(),
            "server": {"name": "EatVenture Control", "profiles": "local/devices", "requires_restart": True},
            "error": getattr(supervisor.devices, "last_error", None),
        }

    @app.get("/api/templates")
    def templates():
        return catalog()

    @app.get("/api/devices/{device_id}/profile")
    def profile(device_id: str):
        known(device_id)
        return store.load(device_id)

    @app.put("/api/devices/{device_id}/profile")
    async def save_profile(device_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(known, device_id)
        return await run_in_threadpool(store.save, device_id, await body(request))

    @app.delete("/api/devices/{device_id}/profile")
    def reset_profile(device_id: str):
        known(device_id)
        return store.reset(device_id)

    @app.post("/api/devices/{device_id}/{action}")
    def device_action(device_id: str, action: str):
        if action not in ("start", "stop", "restart"):
            raise HTTPException(status_code=404, detail="Ação não encontrada.")
        return getattr(supervisor, action)(device_id)

    @app.post("/api/actions")
    async def group_action(request: Request):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        action, identifiers = data.get("action"), data.get("device_ids")
        if action not in ("start", "stop", "restart") or not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 64:
            raise ValueError("Escolha uma ação e de 1 a 64 dispositivos.")
        if any(not isinstance(item, str) for item in identifiers):
            raise ValueError("Dispositivos inválidos.")
        limit = asyncio.Semaphore(16)

        async def perform(identifier):
            async with limit:
                try:
                    value = await run_in_threadpool(getattr(supervisor, action), identifier)
                    return {"device_id": identifier, "ok": True, "device": value}
                except (ValueError, KeyError, RuntimeError) as error:
                    return {"device_id": identifier, "ok": False, "error": str(error)}

        return {"results": await asyncio.gather(*(perform(identifier) for identifier in dict.fromkeys(identifiers)))}

    @app.get("/api/devices/{device_id}/logs")
    def logs(device_id: str, after: int = 0):
        known(device_id)
        if after < 0:
            raise ValueError("Cursor inválido.")
        return supervisor.logs(device_id, after=after)

    @app.post("/api/adb/{action}")
    async def adb_action(action: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        if action == "connect":
            return await run_in_threadpool(supervisor.connect, data.get("address"))
        if action == "pair":
            return await run_in_threadpool(supervisor.pair, data.get("address"), data.get("code"))
        raise HTTPException(status_code=404, detail="Ação não encontrada.")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
