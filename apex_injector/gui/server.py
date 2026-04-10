"""
Apex Meta-Injector — GUI Server (FastAPI Backend).

Provides REST API and WebSocket endpoints for the desktop GUI.
Handles all engine operations, real-time progress streaming,
and configuration management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────

class MetadataFieldModel(BaseModel):
    schema_name: str = Field(alias="schema")
    key: str
    value: Any

    class Config:
        populate_by_name = True


class InjectRequest(BaseModel):
    files: list[str]
    metadata: dict[str, dict[str, Any]]


class BatchRequest(BaseModel):
    manifest_path: Optional[str] = None
    manifest_data: Optional[list[dict]] = None
    auto_approve_wrap: bool = False


class ScanRequest(BaseModel):
    directory: str
    recursive: bool = True
    glob_pattern: str = "*"


class ConfigUpdate(BaseModel):
    tools: Optional[dict[str, str]] = None
    engine: Optional[dict[str, Any]] = None
    log_level: Optional[str] = None


class ComplexWrapApproval(BaseModel):
    file_path: str
    approved: bool


# ─────────────────────────────────────────────────────────────────────
# WebSocket Connection Manager
# ─────────────────────────────────────────────────────────────────────

class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info("WebSocket connected (%d active)", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)
        logger.info("WebSocket disconnected (%d active)", len(self.active))

    async def broadcast(self, event: str, data: dict):
        message = json.dumps({"event": event, "data": data})
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.active.remove(ws)


# ─────────────────────────────────────────────────────────────────────
# Application State
# ─────────────────────────────────────────────────────────────────────

class AppState:
    """Shared state for the GUI backend."""

    def __init__(self):
        self.ws_manager = ConnectionManager()
        self.active_batches: dict[str, dict] = {}
        self.complex_wrap_queue: dict[str, asyncio.Event] = {}
        self.complex_wrap_decisions: dict[str, bool] = {}
        self._engine = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def get_engine(self):
        if self._engine is None:
            from apex_injector.engine import InjectionEngine
            self._engine = InjectionEngine(
                on_progress=self._sync_progress_callback,
                on_file_complete=self._sync_file_complete_callback,
                on_complex_wrap=self._sync_complex_wrap_callback,
            )
        return self._engine

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def _sync_progress_callback(self, progress):
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.ws_manager.broadcast("batch_progress", progress.to_dict()),
                self._loop,
            )

    def _sync_file_complete_callback(self, result):
        if self._loop:
            data = {
                "file": str(result.file_path),
                "status": result.status.value,
                "fields_written": result.fields_written,
                "duration_ms": result.duration_ms,
                "message": result.message,
            }
            asyncio.run_coroutine_threadsafe(
                self.ws_manager.broadcast("file_complete", data),
                self._loop,
            )

    def _sync_complex_wrap_callback(self, path, info):
        # For sync callbacks from engine threads, we block until the user responds
        file_key = str(path)
        event = asyncio.Event()
        self.complex_wrap_queue[file_key] = event
        self.complex_wrap_decisions[file_key] = False

        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self.ws_manager.broadcast("complex_wrap_request", {
                    "file": file_key,
                    "info": info,
                }),
                self._loop,
            )

        # Wait for user decision (with timeout)
        import threading
        decision_event = threading.Event()
        self.complex_wrap_queue[file_key] = decision_event
        decision_event.wait(timeout=300)  # 5 minute timeout

        approved = self.complex_wrap_decisions.get(file_key, False)
        self.complex_wrap_queue.pop(file_key, None)
        self.complex_wrap_decisions.pop(file_key, None)

        return approved


state = AppState()


# ─────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Apex Meta-Injector",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
    )

    # Serve static frontend files — handle PyInstaller bundle
    import sys
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        static_dir = Path(sys._MEIPASS) / "apex_injector" / "gui" / "static"
    else:
        static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    async def startup():
        state.set_loop(asyncio.get_event_loop())

    # ── Root ─────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def root():
        index = static_dir / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Apex Meta-Injector</h1>")

    # ── System Status ────────────────────────────────────────

    @app.get("/api/status")
    async def get_status():
        from apex_injector.config import get_config
        from setup_tools import validate_all_tools
        from apex_injector.codec_manifest import get_manifest_summary

        config = get_config()
        tools = validate_all_tools()

        return {
            "app_name": "Apex Meta-Injector",
            "version": "0.0.1",
            "tools": tools,
            "engine": {
                "max_workers": config.engine.get_worker_count(),
                "backups_enabled": config.engine.create_backups,
                "verify_bitstream": config.engine.verify_bitstream,
            },
            "codecs": get_manifest_summary(),
        }

    # ── File Analysis ────────────────────────────────────────

    @app.post("/api/scan")
    async def scan_directory(req: ScanRequest):
        engine = state.get_engine()
        directory = Path(req.directory)

        if not directory.exists():
            raise HTTPException(404, f"Directory not found: {req.directory}")

        results = await asyncio.to_thread(
            engine.scan_directory,
            directory,
            recursive=req.recursive,
            glob_pattern=req.glob_pattern,
        )

        return {
            "total": len(results),
            "files": [
                {
                    "file": str(r.file_path),
                    "name": r.file_path.name,
                    "container": r.container.value if r.container else None,
                    "codec": r.codec,
                    "size": r.file_size,
                    "injectable": r.injectable,
                    "complex_wrap_risk": r.complex_wrap_risk,
                    "metadata": r.current_metadata,
                    "error": r.error,
                }
                for r in results
            ],
        }

    @app.get("/api/metadata/{file_path:path}")
    async def read_metadata(file_path: str):
        fpath = Path(file_path)
        if not fpath.exists():
            raise HTTPException(404, f"File not found: {file_path}")

        engine = state.get_engine()
        analysis = await asyncio.to_thread(engine.analyze_file, fpath)

        return {
            "file": str(fpath),
            "container": analysis.container.value if analysis.container else None,
            "codec": analysis.codec,
            "size": analysis.file_size,
            "injectable": analysis.injectable,
            "metadata": analysis.current_metadata,
        }

    # ── Single / Multi Inject ────────────────────────────────

    @app.post("/api/inject")
    async def inject_files(req: InjectRequest):
        from apex_injector.handlers import MetadataPayload

        engine = state.get_engine()
        payload = MetadataPayload.from_dict(req.metadata)

        results = []
        for file_str in req.files:
            fpath = Path(file_str)
            result = await asyncio.to_thread(engine.inject_with_retry, fpath, payload)
            results.append({
                "file": str(fpath),
                "status": result.status.value,
                "fields_written": result.fields_written,
                "fields_failed": result.fields_failed,
                "message": result.message,
                "duration_ms": result.duration_ms,
                "backup_path": str(result.backup_path) if result.backup_path else None,
                "complex_wrap_info": result.complex_wrap_info,
            })

            await state.ws_manager.broadcast("file_complete", results[-1])

        return {"results": results}

    # ── Batch Operations ─────────────────────────────────────

    @app.post("/api/batch")
    async def start_batch(req: BatchRequest):
        from apex_injector.manifest import parse_manifest, Manifest, ManifestEntry
        from apex_injector.handlers import MetadataPayload

        batch_id = str(uuid.uuid4())[:8]

        if req.manifest_path:
            manifest = parse_manifest(Path(req.manifest_path))
        elif req.manifest_data:
            manifest = Manifest()
            for item in req.manifest_data:
                file_path = Path(item.get("file", ""))
                metadata = MetadataPayload.from_dict(item.get("metadata", {}))
                manifest.entries.append(ManifestEntry(file_path=file_path, metadata=metadata))
        else:
            raise HTTPException(400, "Either manifest_path or manifest_data is required")

        if manifest.errors:
            raise HTTPException(400, {"errors": manifest.errors})

        state.active_batches[batch_id] = {
            "id": batch_id,
            "status": "running",
            "total": manifest.file_count,
        }

        async def run_batch():
            engine = state.get_engine()
            result = await asyncio.to_thread(engine.run_batch, manifest)
            state.active_batches[batch_id]["status"] = "completed"
            state.active_batches[batch_id]["result"] = result.to_dict()
            await state.ws_manager.broadcast("batch_complete", {
                "batch_id": batch_id,
                **result.to_dict(),
            })

        asyncio.create_task(run_batch())

        return {"batch_id": batch_id, "total_files": manifest.file_count}

    @app.get("/api/batch/{batch_id}/status")
    async def batch_status(batch_id: str):
        batch = state.active_batches.get(batch_id)
        if not batch:
            raise HTTPException(404, f"Batch not found: {batch_id}")
        return batch

    @app.post("/api/batch/{batch_id}/approve-complex-wrap")
    async def approve_complex_wrap(batch_id: str, approval: ComplexWrapApproval):
        file_key = approval.file_path
        state.complex_wrap_decisions[file_key] = approval.approved

        event = state.complex_wrap_queue.get(file_key)
        if event and hasattr(event, 'set'):
            event.set()

        return {"approved": approval.approved}

    @app.post("/api/batch/{batch_id}/cancel")
    async def cancel_batch(batch_id: str):
        engine = state.get_engine()
        engine.cancel_batch()
        state.active_batches[batch_id]["status"] = "cancelled"
        return {"cancelled": True}

    # ── Configuration ────────────────────────────────────────

    @app.get("/api/config")
    async def get_config_api():
        from apex_injector.config import get_config
        config = get_config()
        return {
            "tools": config.tools.get_status(),
            "engine": {
                "max_workers": config.engine.max_workers,
                "max_workers_effective": config.engine.get_worker_count(),
                "create_backups": config.engine.create_backups,
                "verify_bitstream": config.engine.verify_bitstream,
                "hash_algorithm": config.engine.hash_algorithm,
                "file_in_use_retries": config.engine.file_in_use_retries,
            },
            "log_level": config.log_level,
        }

    @app.put("/api/config")
    async def update_config(update: ConfigUpdate):
        from apex_injector.config import get_config
        config = get_config()

        if update.tools:
            for key, value in update.tools.items():
                if hasattr(config.tools, key):
                    setattr(config.tools, key, value)

        if update.engine:
            for key, value in update.engine.items():
                if hasattr(config.engine, key):
                    setattr(config.engine, key, value)

        if update.log_level:
            config.log_level = update.log_level

        return {"status": "updated"}

    # ── WebSocket ────────────────────────────────────────────

    @app.websocket("/ws/events")
    async def websocket_events(ws: WebSocket):
        await state.ws_manager.connect(ws)
        try:
            while True:
                # Keep connection alive, handle client messages
                data = await ws.receive_text()
                msg = json.loads(data)

                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"event": "pong"}))

        except WebSocketDisconnect:
            state.ws_manager.disconnect(ws)
        except Exception as e:
            logger.debug("WebSocket error: %s", e)
            state.ws_manager.disconnect(ws)

    return app
