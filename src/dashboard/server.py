"""API local de controle; importar este módulo não inicia bots nem executa ADB."""

from contextlib import asynccontextmanager
import asyncio
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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.device_profiles import ProfileStore, settings_schema, template_key
from core.preview import MIN_FRAME_INTERVAL
from dashboard.supervisor import BotSupervisor
from dataset import layout
from dataset.manager import DatasetManager


STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 300_000


async def vision_stream_frames(request, supervisor, device_id, session_id, mode):
    from starlette.concurrency import run_in_threadpool

    previous = None
    renewed_at = time.monotonic()
    while not await request.is_disconnected():
        started_at = time.monotonic()
        renew = started_at - renewed_at >= 2
        try:
            frame = await run_in_threadpool(
                supervisor.next_vision_frame, device_id, session_id, previous, renew, mode,
            )
        except (KeyError, RuntimeError):
            return
        if renew:
            renewed_at = started_at
        if frame is not None and frame["signature"] != previous:
            previous = frame["signature"]
            content = frame["content"]
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(content)).encode("ascii") + b"\r\n\r\n"
            )
            yield content
            yield b"\r\n"
            del content, frame
        await asyncio.sleep(max(0, MIN_FRAME_INTERVAL - (time.monotonic() - started_at)))


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


def template_catalog(root):
    roots = [Path(root) / "src" / "vision" / "templates"]
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


def model_catalog(root):
    root = Path(root)
    models = [{"value": "auto", "label": "Automático pela resolução da captura"}]
    if (root / "IA" / "model.joblib").is_file() or (root / "IA" / "model.current.json").is_file():
        models.append({"value": "IA/model.joblib", "label": "Modelo geral (IA/model.joblib)"})
    directory = root / "IA" / "models"
    if directory.is_dir():
        for folder in sorted(directory.iterdir()):
            if re.fullmatch(r"[1-9]\d*x[1-9]\d*", folder.name) and (
                (folder / "category.joblib").is_file() or (folder / "category.current.json").is_file()
            ):
                models.append({"value": f"IA/models/{folder.name}/category.joblib", "label": folder.name})
    return models


def training_catalog(root):
    """Quantidade de modelos treinados com sucesso, agrupada por resolução."""
    counts = {}
    reports = Path(root) / "IA" / "training_reports"
    if not reports.is_dir():
        return counts
    for path in reports.glob("*.json"):
        try:
            if path.stat().st_size > 2_000_000:
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if report.get("status") != "success":
            continue
        resolution = str(report.get("parameters", {}).get("resolution") or "all")
        counts[resolution] = counts.get(resolution, 0) + 1
    return counts


def collected_catalog(root):
    """Total de amostras já gravadas em cada `samples.jsonl`."""
    totals = {}
    dataset_root = Path(root) / "dataset"
    for shard in layout.shard_roots(dataset_root):
        resolution = shard.name if shard != dataset_root else "legacy"
        try:
            with layout.index_path(shard).open(encoding="utf-8") as source:
                totals[resolution] = sum(1 for line in source if line.strip())
        except (OSError, UnicodeError):
            continue
    return totals

def create_app(root=None, state_root=None, token=None, supervisor=None, require_token=False):
    root = Path(root or config.PROJECT_ROOT).resolve()
    state_root = Path(state_root or root / "local").resolve()
    store = supervisor.store if supervisor is not None else ProfileStore(state_root / "devices")
    supervisor = supervisor or BotSupervisor(root, state_root=state_root, store=store)
    token = token if token is not None else os.environ.get("EATVENTURE_WEB_TOKEN")
    if not token:
        token = access_token(state_root) if require_token else None
    if token is not None and not 24 <= len(token) <= 512:
        raise ValueError("O token deve ter entre 24 e 512 caracteres.")
    catalog_lock = threading.Lock()
    catalog_cache = {"at": 0.0, "value": None}
    training_cache = {"at": 0.0, "value": None}
    collected_cache = {"at": 0.0, "value": None}
    datasets = DatasetManager(root / "dataset")

    def catalog():
        with catalog_lock:
            if time.monotonic() - catalog_cache["at"] > 10 or catalog_cache["value"] is None:
                catalog_cache.update(
                    at=time.monotonic(), value=template_catalog(root),
                )
            return catalog_cache["value"]

    def training():
        with catalog_lock:
            if time.monotonic() - training_cache["at"] > 10 or training_cache["value"] is None:
                training_cache.update(at=time.monotonic(), value=training_catalog(root))
            return training_cache["value"]

    def collected():
        with catalog_lock:
            if time.monotonic() - collected_cache["at"] > 10 or collected_cache["value"] is None:
                collected_cache.update(at=time.monotonic(), value=collected_catalog(root))
            return collected_cache["value"]

    @asynccontextmanager
    async def lifespan(app):
        yield
        # O encerramento também funciona ao executar como serviço no Linux.
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(supervisor.shutdown)

    app = FastAPI(title="EatVenture Control", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.supervisor = supervisor
    app.state.web_server = None
    app.state.auth_required = token is not None

    def authenticated(request):
        if token is None:
            return True
        authorization = request.headers.get("authorization", "")
        presented = authorization[7:] if authorization.startswith("Bearer ") else ""
        return len(presented) <= 512 and hmac.compare_digest(presented.encode(), token.encode())

    @app.middleware("http")
    async def authorize(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.url.path != "/api/session" and not authenticated(request):
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
                "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
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
        supervisor.require_device(device_id)

    def dataset_resolution(value):
        if value is not None and re.fullmatch(r"[1-9]\d*x[1-9]\d*", value) is None:
            raise ValueError("Resolução do dataset inválida.")
        return value

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
    def session(request: Request):
        return {"authenticated": authenticated(request), "auth_required": app.state.auth_required}

    @app.post("/api/server/shutdown")
    def shutdown_server():
        server = app.state.web_server
        if server is None:
            raise HTTPException(status_code=503, detail="Encerramento do painel indisponível.")
        # A transmissão da Visão IA mantém uma resposta HTTP aberta. Pare os bots
        # antes de encerrar o Uvicorn para que a transmissão não retenha o shutdown.
        supervisor.shutdown()
        server.should_exit = True
        return {"message": "Encerramento do painel solicitado."}

    @app.get("/api/state")
    def state(refresh: bool = False):
        return {
            "devices": supervisor.list_devices(refresh=refresh),
            "schema": settings_schema(vars(config)),
            "templates": catalog(),
            "models": model_catalog(root),
            "training": training(),
            "collected": collected(),
            "server": {"name": "EatVenture Control", "profiles": "local/devices", "requires_restart": True},
            "error": getattr(supervisor.devices, "last_error", None),
        }

    @app.get("/api/templates")
    def templates():
        return catalog()

    @app.get("/api/runtime")
    def runtime():
        return supervisor.runtime_state()

    @app.get("/api/datasets/sessions")
    def dataset_sessions(resolution: str | None = None, session_id: str | None = None):
        return {"sessions": datasets.sessions(dataset_resolution(resolution), session_id)}

    @app.get("/api/datasets/sessions/{session_id}")
    def dataset_session(session_id: str, offset: int = 0, limit: int = 200, resolution: str | None = None):
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("Paginação inválida.")
        try:
            return datasets.page(session_id, offset, limit, dataset_resolution(resolution))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Sessão do dataset não encontrada.") from error

    @app.get("/api/datasets/sessions/{session_id}/images/{sample_id}")
    def dataset_image(session_id: str, sample_id: str, resolution: str | None = None):
        try:
            path = datasets.image(session_id, sample_id, dataset_resolution(resolution))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Imagem do dataset não encontrada.") from error
        media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media_type)

    @app.delete("/api/datasets/sessions/{session_id}")
    async def delete_dataset_session(session_id: str, request: Request, resolution: str | None = None):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        try:
            return await run_in_threadpool(
                datasets.delete, session_id, data.get("start"), data.get("end"),
                data.get("all") is True, dataset_resolution(resolution),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Sessão do dataset não encontrada.") from error

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

    @app.post("/api/devices/{device_id}/vision")
    async def request_vision(device_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        if type(data.get("enabled")) is not bool:
            raise ValueError("Informe enabled como verdadeiro ou falso.")
        mode = data.get("mode", "vision")
        if mode not in ("vision", "template", "clean"):
            raise ValueError("Modo de prévia inválido.")
        return await run_in_threadpool(supervisor.request_vision, device_id, data["enabled"], mode)

    @app.get("/api/devices/{device_id}/vision")
    def vision_frame(device_id: str, mode: str = "vision", snapshot_id: str = ""):
        if mode == "template":
            snapshot = supervisor.template_snapshot(device_id, snapshot_id or None)
            return Response(
                snapshot["content"], media_type="image/jpeg",
                headers={"X-Template-Snapshot": snapshot["id"]},
            )
        path = supervisor.vision_frame(device_id, mode)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RuntimeError("Aguardando frame da visão IA.") from error
        return Response(content, media_type="image/jpeg")

    @app.post("/api/devices/{device_id}/templates")
    async def save_template(device_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        data = await body(request)
        return await run_in_threadpool(
            supervisor.save_template, device_id, data.get("category"), data.get("name", ""),
            data.get("snapshot"), data.get("x"), data.get("y"),
            data.get("width"), data.get("height"),
        )

    @app.get("/api/devices/{device_id}/vision/stream")
    async def vision_stream(device_id: str, request: Request, mode: str = "vision"):
        from starlette.concurrency import run_in_threadpool
        if mode not in ("vision", "clean"):
            raise ValueError("Modo de prévia inválido.")
        stream = await run_in_threadpool(supervisor.open_vision_stream, device_id, mode)
        return StreamingResponse(
            vision_stream_frames(request, supervisor, stream["device_id"], stream["session_id"], mode),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

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
