"""
Apex Meta-Injector — GUI Launcher.

Initializes pywebview with the FastAPI backend running
in a background thread. Creates a native Windows desktop
window using Edge WebView2.
"""

from __future__ import annotations

import logging
import socket
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(host: str, port: int):
    """Start the FastAPI server in a background thread."""
    import uvicorn
    from apex_injector.gui.server import create_app

    app = create_app()

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def launch_gui():
    """Launch the desktop GUI application."""
    import webview

    from apex_injector import __app_name__, __version__
    from apex_injector.config import get_config

    config = get_config()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%H:%M:%S",
    )

    # Find free port
    port = _find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    logger.info("Starting %s v%s on %s", __app_name__, __version__, url)

    # Start FastAPI server in background thread
    server_thread = threading.Thread(
        target=_start_server,
        args=(host, port),
        daemon=True,
        name="apex-server",
    )
    server_thread.start()

    # Wait for server to be ready
    import time
    import urllib.request

    for _ in range(50):
        try:
            urllib.request.urlopen(f"{url}/api/status", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    # Create native window
    window = webview.create_window(
        title=f"{__app_name__} v{__version__}",
        url=url,
        width=1440,
        height=900,
        min_size=(1024, 600),
        resizable=True,
        text_select=False,
        confirm_close=True,
    )

    # Start webview (blocks until window is closed)
    webview.start(
        debug=config.log_level == "DEBUG",
        gui="edgechromium",  # Use Edge WebView2 on Windows
    )

    logger.info("GUI closed, shutting down")
