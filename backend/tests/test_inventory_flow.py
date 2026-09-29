import json
import io
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
import inventory_store
import main
import ollama_setup
import start
from inventory_logic import collect_alerts, propose_transfers


def sample_inventory():
    def stock(quantity, inbound=0):
        return {"quantity": quantity, "in_transit": inbound, "expiry": "2027-12-31"}

    return {"clinics": {
        "Amman_Main": {"location": "Amman", "type": "hq", "inventory": {
            "Salbutamol_Inhaler": stock(270),
        }},
        "Amman_East": {"location": "Amman", "type": "branch", "inventory": {
            "Salbutamol_Inhaler": stock(32),
        }},
        "Mafraq_North": {"location": "Mafraq", "type": "branch", "inventory": {
            "Paracetamol": stock(0),
        }},
        "Mafraq_HQ": {"location": "Mafraq", "type": "hq", "inventory": {
            "Paracetamol": stock(130),
        }},
    }}


class InventoryFlowTests(unittest.TestCase):
    def setUp(self):
        tests_dir = Path(__file__).resolve().parent
        self.test_dir = tests_dir / f"test-run-{uuid.uuid4().hex}"
        self.test_dir.mkdir()
        self.addCleanup(self.remove_test_dir)
        self.db_path = self.test_dir / "inventory.json"
        self.db_path.write_text(json.dumps(sample_inventory()), encoding="utf-8")
        self.db_patch = patch.object(inventory_store, "DB_PATH", self.db_path)
        self.logs_patch = patch.object(main, "LOGS_DIR", self.test_dir / "logs")
        self.db_patch.start()
        self.logs_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.logs_patch.stop)
        main.session_store.clear()
        self.client = TestClient(main.app)

    def remove_test_dir(self):
        tests_dir = Path(__file__).resolve().parent
        if self.test_dir.resolve().parent != tests_dir or not self.test_dir.name.startswith("test-run-"):
            raise RuntimeError("Refusing to remove a test directory outside backend/tests")
        shutil.rmtree(self.test_dir)

    def scan(self):
        return self.client.post("/scan", json={
            "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler",
        })

    def decide(self, ticket, decision="approve"):
        return self.client.post("/action", json={
            "session_id": ticket, "decision": {"decision": decision},
        })

    def test_warning_to_receipt_without_touching_other_stockout(self):
        self.assertFalse(self.scan().json()["is_parked"])
        consumed = self.client.post("/demo/consume", json={
            "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler",
        })
        self.assertEqual(consumed.json()["quantity"], 17)
        alerts = collect_alerts(inventory_store.read_inventory())
        self.assertEqual({a["status"] for a in alerts}, {"at_risk", "out_of_stock"})

        proposed = self.scan().json()
        self.assertTrue(proposed["is_parked"])
        self.assertEqual(len(proposed["detail"]["deliverables"]), 1)
        self.assertIn("270 → 242", proposed["detail"]["deliverables"][0]["detail"])
        approved = self.decide(proposed["session_id"])
        self.assertEqual(approved.status_code, 200)
        self.assertFalse(approved.json()["is_parked"])
        self.assertEqual(self.decide(proposed["session_id"]).status_code, 409)

        current = inventory_store.read_inventory()["clinics"]
        self.assertEqual(current["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"], 242)
        self.assertEqual(current["Amman_East"]["inventory"]["Salbutamol_Inhaler"]["in_transit"], 28)
        self.assertFalse(self.scan().json()["is_parked"])
        self.assertEqual(self.client.post("/receive", json={
            "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler",
        }).json()["quantity"], 45)
        self.assertFalse(self.scan().json()["is_parked"])
        self.assertEqual(len(collect_alerts(inventory_store.read_inventory())), 1)

    def test_rejection_and_stale_donor_cannot_dispatch(self):
        self.client.post("/demo/consume", json={
            "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler",
        })
        first = self.scan().json()["session_id"]
        self.assertIn("rejected", self.decide(first, "reject").json()["text"])
        self.assertEqual(self.decide(first).status_code, 409)
        self.assertEqual(inventory_store.read_inventory()["clinics"]["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"], 270)

        second = self.scan().json()["session_id"]
        inventory_store.update_inventory(lambda data: data["clinics"]["Amman_Main"]["inventory"]["Salbutamol_Inhaler"].update(quantity=55))
        response = self.decide(second)
        self.assertIn("not dispatched", response.json()["text"])
        current = inventory_store.read_inventory()["clinics"]
        self.assertEqual(current["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"], 55)
        self.assertEqual(current["Amman_East"]["inventory"]["Salbutamol_Inhaler"]["in_transit"], 0)

    def test_inbound_and_reserve_prevent_duplicate_or_unsafe_plans(self):
        inventory = sample_inventory()
        target = inventory["clinics"]["Amman_East"]["inventory"]["Salbutamol_Inhaler"]
        target.update(quantity=17, in_transit=28)
        plans, notes = propose_transfers(inventory, collect_alerts(inventory, ("Amman_East", "Salbutamol_Inhaler")))
        self.assertEqual(plans, [])
        self.assertIn("already has enough stock inbound", notes[0])

        target.update(in_transit=0)
        inventory["clinics"]["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"] = 50
        plans, notes = propose_transfers(inventory, collect_alerts(inventory, ("Amman_East", "Salbutamol_Inhaler")))
        self.assertEqual(plans, [])
        self.assertIn("no donor can spare", notes[0])

    def test_complete_transfer_beats_local_partial_and_stale_inbound_blocks_approval(self):
        inventory = sample_inventory()
        inventory["clinics"]["Amman_East"]["inventory"]["Salbutamol_Inhaler"]["quantity"] = 17
        inventory["clinics"]["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"] = 55
        inventory["clinics"]["Mafraq_HQ"]["inventory"]["Salbutamol_Inhaler"] = {
            "quantity": 130, "in_transit": 0, "expiry": "2027-12-31",
        }
        plans, _ = propose_transfers(inventory, collect_alerts(inventory, ("Amman_East", "Salbutamol_Inhaler")))
        self.assertEqual(plans[0]["from"], "Mafraq_HQ")
        self.assertEqual(plans[0]["quantity_to_move"], 28)

        self.client.post("/demo/consume", json={
            "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler",
        })
        ticket = self.scan().json()["session_id"]
        inventory_store.update_inventory(lambda data: data["clinics"]["Amman_East"]["inventory"]["Salbutamol_Inhaler"].update(in_transit=28))
        self.assertIn("not dispatched", self.decide(ticket).json()["text"])
        self.assertEqual(inventory_store.read_inventory()["clinics"]["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"], 270)

    def test_manual_terminal_transfer_respects_donor_reserve(self):
        parsed = json.dumps({
            "intent": "manual", "donor_clinic": "Amman_Main",
            "target_clinic": "Amman_East", "drug": "Salbutamol_Inhaler",
            "quantity": 20,
        })
        with patch.object(agent, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content=parsed))):
            ticket = self.client.post("/chat", json={"message": "Transfer 20 inhalers"}).json()
        self.assertTrue(ticket["is_parked"])
        self.assertEqual(self.decide(ticket["session_id"]).status_code, 200)
        stock = inventory_store.read_inventory()["clinics"]
        self.assertEqual(stock["Amman_Main"]["inventory"]["Salbutamol_Inhaler"]["quantity"], 250)
        self.assertEqual(stock["Amman_East"]["inventory"]["Salbutamol_Inhaler"]["in_transit"], 20)

        parsed = json.dumps({
            "intent": "manual", "donor_clinic": "Amman_Main",
            "target_clinic": "Amman_East", "drug": "Salbutamol_Inhaler",
            "quantity": 201,
        })
        with patch.object(agent, "llm", SimpleNamespace(invoke=lambda _: SimpleNamespace(content=parsed))):
            rejected = self.client.post("/chat", json={"message": "Transfer too much"}).json()
        self.assertFalse(rejected["is_parked"])
        self.assertIn("Transfer declined", rejected["text"])

    def test_network_scan_works_without_ollama(self):
        unavailable = SimpleNamespace(invoke=lambda _: (_ for _ in ()).throw(RuntimeError("Ollama should not be called")))
        with patch.object(agent, "llm", unavailable):
            result = self.client.post("/chat", json={"message": "Scan for shortages in all branches"})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["is_parked"])
        self.assertIn("Paracetamol", result.json()["detail"]["deliverables"][0]["title"])

    def test_missing_model_has_specific_status_and_terminal_error(self):
        missing = {"state": "model_missing", "message": "Ollama is running, but llama3.2 is missing."}
        with patch.object(main, "probe_ollama", return_value=missing):
            self.assertEqual(self.client.get("/ollama/status").json(), missing)
        unavailable = SimpleNamespace(invoke=lambda _: (_ for _ in ()).throw(RuntimeError("model not found (404)")))
        with patch.object(agent, "llm", unavailable), patch.object(agent, "probe_ollama", return_value=missing):
            result = self.client.post("/chat", json={"message": "Transfer 20 inhalers"}).json()
        self.assertFalse(result["is_parked"])
        self.assertEqual(result["text"], missing["message"])

    def test_launcher_selects_server_with_model(self):
        with patch.dict(os.environ, {"JDRN_OLLAMA_URL": ""}), patch.object(start, "probe_ollama", side_effect=[
            {"state": "model_missing"}, {"state": "ready"},
        ]):
            self.assertEqual(start.choose_ollama_server(), (start.JDRN_URL, None))

    def test_ollama_probe_distinguishes_missing_model_from_offline(self):
        payload = json.dumps({"models": [{"name": "other:latest"}]}).encode("utf-8")
        with patch.object(ollama_setup, "urlopen", return_value=io.BytesIO(payload)):
            self.assertEqual(ollama_setup.probe_ollama("http://127.0.0.1:11434")["state"], "model_missing")
        with patch.object(ollama_setup, "urlopen", side_effect=OSError("connection refused")):
            self.assertEqual(ollama_setup.probe_ollama("http://127.0.0.1:11434")["state"], "offline")


if __name__ == "__main__":
    unittest.main()
