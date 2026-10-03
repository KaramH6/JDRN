"""Single-process inventory access for the local MVP."""

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


DB_PATH = Path(__file__).parent.parent / "data" / "inventory.json"
_lock = RLock()


def utc_now():
    return datetime.now(timezone.utc)


def add_tracking_defaults(inventory):
    """Old records have unknown update times, rather than invented timestamps."""
    for clinic in inventory.get("clinics", {}).values():
        for stock in clinic.get("inventory", {}).values():
            stock.setdefault("last_updated", None)
            stock.setdefault("inbound_shipments", [])
    return inventory


def read_inventory():
    with _lock, DB_PATH.open("r", encoding="utf-8") as handle:
        return add_tracking_defaults(json.load(handle))


def update_inventory(change):
    """Apply a checked change and replace the JSON file only on success."""
    with _lock:
        with DB_PATH.open("r", encoding="utf-8") as handle:
            inventory = add_tracking_defaults(json.load(handle))
        previous = deepcopy(inventory)
        result = change(inventory)
        changed_at = utc_now().isoformat()
        for clinic_id, clinic in inventory.get("clinics", {}).items():
            old_stock = previous.get("clinics", {}).get(clinic_id, {}).get("inventory", {})
            for drug, stock in clinic.get("inventory", {}).items():
                if stock != old_stock.get(drug):
                    stock["last_updated"] = changed_at
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
