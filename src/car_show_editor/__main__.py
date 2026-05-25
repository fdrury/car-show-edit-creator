import threading
import webbrowser

import uvicorn

from .server import app

PORT = 8765


def open_browser() -> None:
    webbrowser.open(f"http://localhost:{PORT}")


def main() -> None:
    threading.Timer(0.8, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
