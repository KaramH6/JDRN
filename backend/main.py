import uuid
import json
from pathlib import Path
from threading import RLock
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from pydantic import BaseModel # type: ignore

from agent import emit_event, execute_transfer, jdrn_graph
from inventory_store import read_inventory, update_inventory
from ollama_setup import OLLAMA_MODEL, get_ollama_url, probe_ollama

app = FastAPI(title="JDRN Logistics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_store = {}
conversation_store = {}
session_lock = RLock()

class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str

class ActionRequest(BaseModel):
    session_id: str
    decision: Any 

class ReceiveRequest(BaseModel):
    clinic_id: str
    drug: str

class DemandRequest(BaseModel):
    clinic_id: str
    drug: str

class ScanRequest(BaseModel):
    clinic_id: str
    drug: str

class AgentResponse(BaseModel):
    session_id: str
    conversation_id: Optional[str] = None
    is_parked: bool
    detail: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    tree_data: Optional[list] = None

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

def parse_tree_data(events_path: Path) -> list:
    tree_data = []
    if events_path.exists():
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                try:
                    data = json.loads(line)
                    ev = data.get("ev")
                    node = data.get("node", "System").title()
                    payload = data.get("payload", {})
                    
                    if ev == "classified":
                        tree_data.append({"node": node, "title": "Intent Classification", "detail": f"Intent: {payload.get('intent')}\nExtracted: {json.dumps(payload.get('extracted', {}))}", "status": "success"})
                    elif ev == "routed":
                        tree_data.append({"node": "Router", "title": "Task Handoff", "detail": "Assigned to Domain Specialist", "status": "success"})
                    elif ev == "grounded":
                        tree_data.append({"node": node, "title": "Database Query", "detail": payload.get('query'), "status": "success"})
                    elif ev == "proposed":
                        changes = payload.get("changes", [])
                        ch_text = "\n".join([f"• {c.get('target')}: {c.get('from')} -> {c.get('to')}" for c in changes])
                        tree_data.append({"node": node, "title": "Redistribution Drafted", "detail": f"{payload.get('reason')}\n{ch_text}", "status": "success"})
                    elif ev == "tool_call":
                        tree_data.append({"node": node, "title": "Executing System Process", "detail": payload.get("command"), "status": "success" if payload.get("success", True) else "error"})
                    elif ev == "plan_parked":
                        tree_data.append({"node": "HITL Gate", "title": "Paused for Verification", "detail": f"Gate ID: {payload.get('gate_id')}\nImpact: {payload.get('impact')}", "status": "warning"})
                    elif ev == "plan_decided":
                        outcome = payload.get("outcome", "unknown")
                        tree_data.append({"node": "HITL Gate", "title": f"Decision: {outcome.title()}", "detail": f"By: {payload.get('by')}", "status": "success" if outcome == "approve" else "error"})
                except Exception:
                    pass
    return tree_data

def run_agent_turn(session_id: str, message: str = "", decision: Optional[str] = None, scope=None, history=None, conversation_id=None) -> AgentResponse:
    if decision is not None:
        with session_lock:
            previous = session_store.get(session_id)
            if previous is None:
                raise HTTPException(status_code=404, detail="Transfer ticket not found. Scan again.")
            if previous.get("human_decision") == "executed" or not previous.get("transfer_plans"):
                raise HTTPException(status_code=409, detail="Transfer ticket is no longer pending. Scan again.")
            run_dir = Path(previous["run_dir"])
            emit_event(str(run_dir), "request", "User", {"decision": decision})
            result = execute_transfer({**previous, "human_decision": decision})
            session_store[session_id] = result
    else:
        run_dir = LOGS_DIR / session_id
        run_dir.mkdir(parents=True, exist_ok=True)
        emit_event(str(run_dir), "request", "User", {"message": message, "scan_scope": scope, "conversation_id": conversation_id})
        state = {
            "session_id": session_id,
            "run_dir": str(run_dir),
            "message": message,
            "history": history or [],
            "human_decision": None,
            "transfer_plans": [],
            "shortages": [],
            "unresolved": [],
            "scan_scope": scope,
        }
        result = jdrn_graph.invoke(state)
        with session_lock:
            session_store[session_id] = result
    
    tree_data = parse_tree_data(run_dir / "events.jsonl")
    
    plans = result.get("transfer_plans", [])
    if plans and result.get("human_decision") not in ["approve", "reject", "executed"]:
        deliverables = [
            {
                "type": "Transfer Order",
                "title": f"Move {p['quantity_to_move']} units of {p['drug'].replace('_', ' ')} from {p['from'].replace('_', ' ')} to {p['to'].replace('_', ' ')}",
                "detail": f"Recipient: {p['recipient_before']} on hand, {p['recipient_in_transit']} inbound. Donor: {p['donor_before']} → {p['donor_after']} after dispatch.",
                "reason": p["reason"],
            }
            for p in plans
        ]
        response = AgentResponse(
            session_id=session_id,
            is_parked=True,
            detail={
                "business_process": f"Mitigation required ({len(plans)} proposed transfer{'s' if len(plans) != 1 else ''})",
                "deliverables": deliverables,
                "unresolved": result.get("unresolved", []),
            },
            tree_data=tree_data
        )
        emit_event(str(run_dir), "response", "System", {"is_parked": True, "detail": response.detail})
        return response
        
    response = AgentResponse(
        session_id=session_id,
        is_parked=False,
        text=result.get("final_response", "Process completed."),
        tree_data=tree_data
    )
    emit_event(str(run_dir), "response", "System", {"is_parked": False, "text": response.text})
    return response

@app.get("/status")
async def get_agent_status(session_id: str):
    events_path = LOGS_DIR / session_id / "events.jsonl"
    if not events_path.exists():
        return {"status": "Waking up JDRN AI..."}
        
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
            if not lines: return {"status": "Initializing workflow..."}
                
            last_event = json.loads(lines[-1])
            ev = last_event.get("ev", "")
            node = last_event.get("node", "Agent").title()
            
            if ev == "classified": return {"status": "Analyzing inventory systems..."}
            elif ev == "routed": return {"status": "Assigning Logistics Agent..."}
            elif ev == "grounded": return {"status": "Checking simulated network inventory for surplus..."}
            elif ev == "proposed": return {"status": "Drafting redistribution route..."}
            elif ev == "tool_call": return {"status": "Processing data pipeline..."}
            elif ev == "plan_parked": return {"status": "Awaiting human authorization."}
            elif ev == "plan_decided": return {"status": "Executing authorized transfer."}
            elif ev == "response": return {"status": "Completed."}
            
            return {"status": "Processing..."}
    except Exception:
        return {"status": "Processing..."}

@app.get("/inventory")
async def get_inventory():
    return read_inventory()

@app.get("/ollama/status")
def get_ollama_status():
    return probe_ollama(get_ollama_url(), OLLAMA_MODEL)

@app.post("/demo/consume")
async def simulate_demand(req: DemandRequest):
    """Consume a fixed amount of branch stock for a repeatable local demo."""
    def consume(inventory):
        stock = branch_stock(inventory, req.clinic_id, req.drug)
        stock["quantity"] = max(0, stock.get("quantity", 0) - 15)
        return {"clinic_id": req.clinic_id, "drug": req.drug, "quantity": stock["quantity"]}
    return update_inventory(consume)

@app.post("/demo/restock")
async def restock_demo_branch(req: DemandRequest):
    """Give one branch medicine a fresh starting quantity for another demo run."""
    def restock(inventory):
        stock = branch_stock(inventory, req.clinic_id, req.drug)
        if stock.get("in_transit", 0) > 0:
            raise HTTPException(status_code=409, detail="Receive the inbound shipment before resetting this demo item")
        quantity = 32 if (req.clinic_id, req.drug) == ("Amman_East", "Salbutamol_Inhaler") else 45
        stock["quantity"] = quantity
        return {"clinic_id": req.clinic_id, "drug": req.drug, "quantity": quantity}
    return update_inventory(restock)

def branch_stock(inventory, clinic_id: str, drug: str):
    site = inventory.get("clinics", {}).get(clinic_id)
    if not site or site.get("type") != "branch":
        raise HTTPException(status_code=400, detail="Choose a valid branch")
    stock = site.get("inventory", {}).get(drug)
    if stock is None:
        raise HTTPException(status_code=400, detail="Medicine not found at this branch")
    return stock

@app.post("/receive")
async def receive_shipment(req: ReceiveRequest):
    def receive(inventory):
        stock = inventory.get("clinics", {}).get(req.clinic_id, {}).get("inventory", {}).get(req.drug)
        if stock is None:
            raise HTTPException(status_code=404, detail="Clinic or medicine not found")
        in_transit = stock.get("in_transit", 0)
        if in_transit <= 0:
            raise HTTPException(status_code=409, detail="No inbound shipment to receive")
        stock["quantity"] = stock.get("quantity", 0) + in_transit
        stock["in_transit"] = 0
        return {"status": "success", "quantity": stock["quantity"]}
    return update_inventory(receive)

@app.post("/chat", response_model=AgentResponse)
async def start_or_continue_chat(request: ChatRequest):
    conversation_id = request.conversation_id or str(uuid.uuid4())
    with session_lock:
        history = list(conversation_store.get(conversation_id, []))[-4:]
    response = run_agent_turn(str(uuid.uuid4()), message=request.message, history=history, conversation_id=conversation_id)
    reply = response.text or (response.detail or {}).get("business_process", "")
    with session_lock:
        conversation_store.setdefault(conversation_id, []).append({"user": request.message, "assistant": reply})
        conversation_store[conversation_id] = conversation_store[conversation_id][-4:]
    return response.model_copy(update={"conversation_id": conversation_id})

@app.post("/scan", response_model=AgentResponse)
async def scan_selected_alert(request: ScanRequest):
    branch_stock(read_inventory(), request.clinic_id, request.drug)
    return run_agent_turn(str(uuid.uuid4()), scope=(request.clinic_id, request.drug))

@app.post("/action", response_model=AgentResponse)
async def handle_human_action(request: ActionRequest):
    decision = request.decision.get("decision") if isinstance(request.decision, dict) else None
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Decision must be approve or reject")
    return run_agent_turn(request.session_id, decision=decision)

if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host="127.0.0.1", port=8000)
