"""Launch ReClip in a native pywebview window.

Flask runs in a daemon thread; pywebview owns the main thread (required on
macOS/Cocoa). When the window is closed, the process exits and the daemon
Flask thread dies with it.
"""

import logging
import os
import socket
import threading
import time

import webview

from app import app


def wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_flask(host: str, port: int) -> None:
    app.run(host=host, port=port, use_reloader=False, threaded=True)


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("PORT", 8899))

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    threading.Thread(target=run_flask, args=(host, port), daemon=True).start()

    if not wait_for_port(host, port):
        raise RuntimeError(f"ReClip server did not start on {host}:{port} within 10s")

    webview.create_window("ReClip", f"http://{host}:{port}", width=1100, height=750)
    webview.start()


if __name__ == "__main__":
    main()
