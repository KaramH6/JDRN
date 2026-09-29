"""Small Ollama health check shared by the API and backend launcher."""

import json
import os
from urllib.error import URLError
from urllib.request import urlopen


OLLAMA_MODEL = os.getenv("JDRN_OLLAMA_MODEL", "llama3.2")
PRIMARY_URL = "http://127.0.0.1:11434"
JDRN_URL = "http://127.0.0.1:11435"


def probe_ollama(base_url=PRIMARY_URL, model=OLLAMA_MODEL):
    """Report whether a server is reachable and exposes the required local model."""
    base_url = base_url.rstrip("/")
    try:
        with urlopen(f"{base_url}/api/tags", timeout=3) as response:
            models = json.load(response).get("models", [])
    except (OSError, URLError, ValueError) as error:
        return {
            "state": "offline",
            "message": f"Ollama is not reachable at {base_url}. Start Ollama or run python start.py ({error}).",
        }

    expected = model if ":" in model else f"{model}:latest"
    if any(item.get("name") == expected or item.get("model") == expected for item in models):
        return {"state": "ready", "message": f"Ollama is ready with {model}."}
    return {
        "state": "model_missing",
        "message": (
            f"Ollama is running at {base_url}, but {model} is not in its model list. "
            "Check OLLAMA_MODELS and restart Ollama, or pull the model into that server."
        ),
    }


def get_ollama_url():
    """Read the launcher's choice at runtime, or discover a ready local server."""
    configured = os.getenv("JDRN_OLLAMA_URL")
    if configured:
        return configured.rstrip("/")
    for url in (PRIMARY_URL, JDRN_URL):
        if probe_ollama(url, OLLAMA_MODEL)["state"] == "ready":
            return url
    return PRIMARY_URL
