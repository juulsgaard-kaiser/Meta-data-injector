"""Local desktop API: metadata inspection, edit previews and isolated batch jobs."""

from __future__ import annotations

import asyncio
import copy
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apex_injector.codec_manifest import MetadataSchema
from apex_injector.config import get_config, set_config
from apex_injector.engine import InjectionEngine
from apex_injector.handlers import MetadataField, MetadataPayload
from apex_injector.handlers.exiftool_bridge import get_exiftool_bridge
from apex_injector.manifest import Manifest, ManifestEntry, parse_manifest
from apex_injector.risk import assess_risks, field_risk

logger = logging.getLogger(__name__)


class InjectRequest(BaseModel):
    files: list[str] = Field(min_length=1)
    metadata: dict[str, dict[str, Any]]
    acknowledge_risk: bool = False


class InspectRequest(BaseModel):
    file: str
    deep: bool = True


class BatchRequest(BaseModel):
    manifest_path: str | None = None
    manifest_data: list[dict] | None = None
    acknowledge_risk: bool = False


class ScanRequest(BaseModel):
    directory: str
    recursive: bool = True
    glob_pattern: str = "*"


class ConfigUpdate(BaseModel):
    tools: dict[str, str] | None = None
    engine: dict[str, Any] | None = None
    log_level: str | None = None


def payload_from(data, acknowledge=False):
    try:
        payload = MetadataPayload.from_dict(data)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from e
    if not payload.fields:
        raise HTTPException(422, "No metadata fields supplied")
    payload.acknowledge_risk = acknowledge
    return payload


def file_result(result):
    return dict(
        file=str(result.file_path),
        status=result.status.value,
        fields_written=result.fields_written,
        fields_failed=result.fields_failed,
        message=result.message,
        details=result.details,
        duration_ms=result.duration_ms,
        backup_path=str(result.backup_path) if result.backup_path else None,
    )


def load_manifest(req):
    if req.manifest_path:
        manifest = parse_manifest(Path(req.manifest_path))
    elif req.manifest_data:
        manifest = Manifest()
        for item in req.manifest_data:
            if not item.get("file") or not Path(item["file"]).is_absolute():
                raise HTTPException(422, "Inline manifest entries require absolute file paths")
            manifest.entries.append(ManifestEntry(Path(item["file"]), payload_from(item.get("metadata", {}))))
    else:
        raise HTTPException(422, "Provide a manifest path or inline manifest data")
    errors = manifest.errors + manifest.validate()
    if errors:
        raise HTTPException(422, errors)
    for entry in manifest.entries:
        entry.metadata.acknowledge_risk = req.acknowledge_risk
    return manifest


class AppState:
    def __init__(self):
        self.sockets = set()
        self.batches = {}
        self.engines = {}
        self.tasks = set()
        self.active_writes = 0

    async def broadcast(self, event, data):
        for ws in list(self.sockets):
            try:
                await ws.send_json({"event": event, "data": data})
            except Exception:
                self.sockets.discard(ws)


def create_app():
    state = AppState()

    @asynccontextmanager
    async def lifespan(app):
        yield
        for engine in state.engines.values():
            engine.cancel_batch()
        if state.tasks:
            await asyncio.gather(*state.tasks, return_exceptions=True)

    app = FastAPI(title="Apex Meta-Injector", version="0.0.2", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.jobs = state

    @app.middleware("http")
    async def local_origin(request, call_next):
        # Cross-origin pages must not be able to reconfigure local executables or edit files.
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Cross-origin access is not allowed"}, status_code=403)
        return await call_next(request)

    static = (
        Path(sys._MEIPASS) / "apex_injector/gui/static"
        if getattr(sys, "frozen", False)
        else Path(__file__).parent / "static"
    )
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse((static / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/status")
    async def status():
        from apex_injector.codec_manifest import get_manifest_summary
        from apex_injector.tool_status import validate_all_tools

        config = get_config()
        return {
            "app_name": "Apex Meta-Injector",
            "version": "0.0.2",
            "tools": await asyncio.to_thread(validate_all_tools),
            "codecs": get_manifest_summary(),
            "engine": {
                "max_workers": config.engine.get_worker_count(),
                "backups_enabled": config.engine.create_backups,
                "verify_bitstream": config.engine.verify_bitstream,
            },
        }

    @app.post("/api/scan")
    async def scan(req: ScanRequest):
        if not Path(req.directory).is_dir():
            raise HTTPException(404, "Directory not found")
        analyses = await asyncio.to_thread(
            InjectionEngine().scan_directory, Path(req.directory).resolve(), req.recursive, req.glob_pattern
        )
        return {
            "total": len(analyses),
            "files": [
                dict(
                    file=str(r.file_path),
                    name=r.file_path.name,
                    container=r.container.value if r.container else None,
                    codec=r.codec,
                    size=r.file_size,
                    injectable=r.injectable,
                    complex_wrap_risk=r.complex_wrap_risk,
                    metadata=r.current_metadata,
                    error=r.error,
                )
                for r in analyses
            ],
        }

    @app.post("/api/inspect")
    async def inspect(req: InspectRequest):
        path = Path(req.file)
        if not path.is_absolute() or not path.is_file():
            raise HTTPException(404, "Select an existing file using its full path")
        analysis = await asyncio.to_thread(InjectionEngine().analyze_file, path)
        response = dict(
            file=str(path),
            container=analysis.container.value if analysis.container else None,
            codec=analysis.codec,
            size=analysis.file_size,
            injectable=analysis.injectable,
            metadata=analysis.current_metadata,
            error=analysis.error,
        )
        if req.deep:
            if not get_config().tools.exiftool:
                response["inspection_warning"] = "Configure ExifTool to read embedded, duplicate and unknown tags."
                response["inventory"] = []
            else:
                try:
                    deep = await asyncio.to_thread(get_exiftool_bridge().inspect_all, path)
                    inventory = []
                    for document in deep["tags"]:
                        for key, value in document.items():
                            if key == "SourceFile":
                                continue
                            parts = key.split(":")
                            qualifiers = parts[1:-1]
                            editable = len(parts) > 1 and all(q == "Main" for q in qualifiers)
                            edit_tag = f"{parts[0]}:{parts[-1]}" if editable else None
                            risk = field_risk(MetadataField(MetadataSchema.EXIFTOOL, edit_tag or key, value))
                            if (
                                not editable
                                or not analysis.injectable
                                or analysis.container not in get_exiftool_bridge().supported_containers
                            ):
                                risk.update(
                                    level="read_only",
                                    reason=(
                                        "This embedded/duplicate tag or container cannot be targeted "
                                        "safely by the current writer."
                                    ),
                                )
                            inventory.append(dict(tag=key, edit_tag=edit_tag, value=value, risk=risk))
                    response["inventory"] = inventory
                    response["inspection_warning"] = deep["warnings"]
                except Exception as e:
                    raise HTTPException(422, f"Deep inspection failed: {e}") from e
        return response

    @app.get("/api/metadata/{file_path:path}")
    async def metadata(file_path: str):
        return await inspect(InspectRequest(file=str(Path(file_path).resolve()), deep=False))

    def preview_payload(payload):
        risks = assess_risks(payload)
        config = get_config()
        risks["protection_warnings"] = []
        if not config.engine.create_backups:
            risks["protection_warnings"].append("Backups are disabled: there will be no recovery copy.")
        if not config.engine.verify_bitstream:
            risks["protection_warnings"].append(
                "Bitstream verification is disabled: media changes will not be hash-checked."
            )
        return risks

    @app.post("/api/preview")
    async def preview(req: InjectRequest):
        payload = payload_from(req.metadata)
        result = preview_payload(payload)
        result["files"] = req.files
        return result

    @app.post("/api/inject")
    async def inject(req: InjectRequest):
        payload = payload_from(req.metadata, req.acknowledge_risk)
        if any(not Path(path).is_absolute() for path in req.files):
            raise HTTPException(422, "Injection requires absolute file paths")
        results = []
        engine = InjectionEngine()
        state.active_writes += 1
        try:
            for path in req.files:
                result = file_result(await asyncio.to_thread(engine.inject_with_retry, Path(path), payload))
                results.append(result)
                await state.broadcast("file_complete", result)
        finally:
            state.active_writes -= 1
        return {"results": results}

    @app.post("/api/batch/preview")
    async def preview_batch(req: BatchRequest):
        manifest = load_manifest(req)
        return {
            "total": manifest.file_count,
            "entries": [
                {"file": str(entry.file_path), **preview_payload(entry.metadata)} for entry in manifest.entries
            ],
        }

    @app.post("/api/batch")
    async def start_batch(req: BatchRequest):
        manifest = load_manifest(req)
        # Bound simultaneous disk work; each retained job has its own engine and events.
        if any(job["status"] in ("running", "cancelling") for job in state.batches.values()):
            raise HTTPException(409, "Wait for the active batch to finish or cancel it")
        batch_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()

        def progress(p):
            asyncio.run_coroutine_threadsafe(
                state.broadcast("batch_progress", {"batch_id": batch_id, **p.to_dict()}), loop
            )

        def complete(r):
            asyncio.run_coroutine_threadsafe(
                state.broadcast("file_complete", {"batch_id": batch_id, **file_result(r)}), loop
            )

        engine = InjectionEngine(on_progress=progress, on_file_complete=complete)
        state.engines[batch_id] = engine
        job = {"id": batch_id, "status": "running", "total": manifest.file_count}
        state.batches[batch_id] = job

        async def run():
            try:
                result = await asyncio.to_thread(engine.run_batch, manifest)
                job["status"] = "cancelled" if job["status"] == "cancelling" else "completed"
                job["result"] = {**result.to_dict(), "file_results": [file_result(r) for r in result.file_results]}
                await state.broadcast(
                    "batch_complete", {"batch_id": batch_id, "status": job["status"], **job["result"]}
                )
            except Exception as e:
                logger.exception("Batch failed")
                job.update(status="failed", error=str(e))
                await state.broadcast("batch_error", {"batch_id": batch_id, "error": str(e)})
            finally:
                state.engines.pop(batch_id, None)

        task = asyncio.create_task(run())
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)
        return {"batch_id": batch_id, "total_files": manifest.file_count}

    @app.get("/api/batch/{batch_id}/status")
    async def batch_status(batch_id: str):
        if batch_id not in state.batches:
            raise HTTPException(404, "Batch not found")
        return state.batches[batch_id]

    @app.post("/api/batch/{batch_id}/cancel")
    async def cancel(batch_id: str):
        if batch_id not in state.batches:
            raise HTTPException(404, "Batch not found")
        job = state.batches[batch_id]
        if batch_id in state.engines:
            job["status"] = "cancelling"
            state.engines[batch_id].cancel_batch()
        return {"status": job["status"]}

    @app.get("/api/config")
    async def config():
        from dataclasses import asdict

        current = get_config()
        return {
            "tools": current.tools.get_status(),
            "engine": {**asdict(current.engine), "max_workers_effective": current.engine.get_worker_count()},
            "log_level": current.log_level,
        }

    @app.put("/api/config")
    async def update_config(update: ConfigUpdate):
        if state.active_writes or state.engines:
            raise HTTPException(409, "Wait for file edits to finish before changing configuration")
        draft = copy.deepcopy(get_config())
        try:
            draft._apply_overrides(update.model_dump(exclude_none=True))
            draft.save()
        except (ValueError, TypeError, OSError) as e:
            raise HTTPException(422, str(e)) from e
        set_config(draft)
        return {"status": "saved"}

    @app.websocket("/ws/events")
    async def websocket(ws: WebSocket):
        origin = ws.headers.get("origin")
        if origin and origin not in (f"http://{ws.headers.get('host')}", f"https://{ws.headers.get('host')}"):
            await ws.close(code=1008)
            return
        await ws.accept()
        state.sockets.add(ws)
        try:
            while True:
                if (await ws.receive_json()).get("type") == "ping":
                    await ws.send_json({"event": "pong"})
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            state.sockets.discard(ws)

    return app
