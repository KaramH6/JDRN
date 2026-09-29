"""Start the JDRN backend with an Ollama server that can see its model."""

import os
import shutil
import socket
import subprocess
import sys
import time

from ollama_setup import OLLAMA_MODEL, probe_ollama


PRIMARY_URL = "http://127.0.0.1:11434"
JDRN_URL = "http://127.0.0.1:11435"


def choose_ollama_server():
    """Use a working server or start a private one using this terminal's model path."""
    configured = os.getenv("JDRN_OLLAMA_URL")
    if configured:
        status = probe_ollama(configured, OLLAMA_MODEL)
        if status["state"] != "ready":
            raise RuntimeError(status["message"])
        return configured, None

    if probe_ollama(PRIMARY_URL, OLLAMA_MODEL)["state"] == "ready":
        return PRIMARY_URL, None

    secondary = probe_ollama(JDRN_URL, OLLAMA_MODEL)
    if secondary["state"] == "ready":
        return JDRN_URL, None
    if secondary["state"] != "offline":
        raise RuntimeError(f"Port 11435 is occupied by another Ollama server. {secondary['message']}")

    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama is not installed or is not on PATH. Install Ollama, then reopen the terminal.")

    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = "127.0.0.1:11435"
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        [executable, "serve"], env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(20):
        if process.poll() is not None:
            break
        status = probe_ollama(JDRN_URL, OLLAMA_MODEL)
        if status["state"] == "ready":
            return JDRN_URL, process
        if status["state"] == "model_missing":
            break
        time.sleep(0.5)

    process.terminate()
    raise RuntimeError(
        f"Ollama started, but {OLLAMA_MODEL} is unavailable in its model directory. "
        "Check OLLAMA_MODELS in this terminal. If the model is not installed there, run "
        f"ollama pull {OLLAMA_MODEL} after setting OLLAMA_HOST=127.0.0.1:11435."
    )


def main():
    with socket.socket() as check:
        if check.connect_ex(("127.0.0.1", 8000)) == 0:
            print("JDRN startup: port 8000 already has a backend running. Stop the old uvicorn terminal, then run python start.py.", file=sys.stderr)
            return 1
    try:
        url, process = choose_ollama_server()
    except RuntimeError as error:
        print(f"JDRN startup: {error}", file=sys.stderr)
        return 1

    os.environ["JDRN_OLLAMA_URL"] = url
    print(f"JDRN: using {OLLAMA_MODEL} at {url}", flush=True)
    try:
        import uvicorn
        uvicorn.run("main:app", host="127.0.0.1", port=8000)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
