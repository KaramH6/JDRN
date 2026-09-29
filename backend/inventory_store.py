"""Single-process inventory access for the local MVP."""

import json
import os
import tempfile
from pathlib import Path
from threading import RLock


DB_PATH = Path(__file__).parent.parent / "data" / "inventory.json"
_lock = RLock()


def read_inventory():
    with _lock, DB_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def update_inventory(change):
    """Apply a checked change and replace the JSON file only on success."""
    with _lock:
        with DB_PATH.open("r", encoding="utf-8") as handle:
            inventory = json.load(handle)
        result = change(inventory)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=DB_PATH.parent,
                prefix=".inventory-", suffix=".tmp", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(inventory, handle, indent=2)
                handle.write("\n")
            os.replace(temporary_path, DB_PATH)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return result
