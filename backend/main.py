import os
import uuid
import json
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException # type: ignore
from fastapi.middleware.cors import CORSMiddleware # type: ignore
from pydantic import BaseModel # type: ignore

from agent import jdrn_graph

app = FastAPI(title="JDRN Logistics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

session_store = {}

class ChatRequest(BaseModel):
    session_id: Optional[str] = None
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

class AgentResponse(BaseModel):
    session_id: str
    is_parked: bool
    detail: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    tree_data: Optional[list] = None

os.makedirs("logs", exist_ok=True)

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

def run_agent_turn(session_id: str, message: str = "", decision: Optional[str] = None) -> AgentResponse:
    run_dir = Path(f"logs/{session_id}")
    os.makedirs(run_dir, exist_ok=True)
    
    if session_id not in session_store:
        state = {
            "session_id": session_id,
            "run_dir": str(run_dir),
            "message": message,
            "human_decision": None,
            "transfer_plans": [],
            "shortages": []
        }
    else:
        state = session_store[session_id]
        if decision:
            state["human_decision"] = decision
        else:
            state["message"] = message
            state["human_decision"] = None
            state["transfer_plans"] = []
            state["shortages"] = []
            state["final_response"] = None
            
    result = jdrn_graph.invoke(state)
    session_store[session_id] = result
    
    tree_data = parse_tree_data(run_dir / "events.jsonl")
    
    plans = result.get("transfer_plans", [])
    if plans and result.get("human_decision") not in ["approve", "reject", "executed"]:
        deliverables = [
            {"type": "Transfer Order", "title": f"Move {p['quantity_to_move']} units of {p['drug'].replace('_', ' ')} from {p['from'].replace('_', ' ')} to {p['to'].replace('_', ' ')}"}
            for p in plans
        ]
        return AgentResponse(
            session_id=session_id,
            is_parked=True,
            detail={
                "business_process": f"Mitigation Required ({len(plans)} Relocations)",
                "deliverables": deliverables
            },
            tree_data=tree_data
        )
        
    return AgentResponse(
        session_id=session_id,
        is_parked=False,
        text=result.get("final_response", "Process completed."),
        tree_data=tree_data
    )

@app.get("/status")
async def get_agent_status(session_id: str):
    events_path = Path(f"logs/{session_id}/events.jsonl")
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
            elif ev == "grounded": return {"status": "Searching Hakeem databases for surplus..."}
            elif ev == "proposed": return {"status": "Drafting redistribution route..."}
            elif ev == "tool_call": return {"status": "Processing data pipeline..."}
            elif ev == "plan_parked": return {"status": "Awaiting human authorization."}
            elif ev == "plan_decided": return {"status": "Executing authorized transfer."}
            
            return {"status": "Processing..."}
    except Exception:
        return {"status": "Processing..."}

@app.get("/inventory")
async def get_inventory():
    db_path = Path(__file__).parent.parent / "data" / "inventory.json"
    if db_path.exists():
        with open(db_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "Database not found"}

@app.post("/demo/consume")
async def simulate_demand(req: DemandRequest):
    """Consume a fixed amount of branch stock for a repeatable local demo."""
    db_path = Path(__file__).parent.parent / "data" / "inventory.json"
    with open(db_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)

    site = inventory.get("clinics", {}).get(req.clinic_id)
    if not site or site.get("type") != "branch":
        raise HTTPException(status_code=400, detail="Choose a valid branch")
    stock = site.get("inventory", {}).get(req.drug)
    if stock is None:
        raise HTTPException(status_code=400, detail="Medicine not found at this branch")

    stock["quantity"] = max(0, stock.get("quantity", 0) - 15)
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)
    return {"clinic_id": req.clinic_id, "drug": req.drug, "quantity": stock["quantity"]}

@app.post("/demo/restock")
async def restock_demo_branch(req: DemandRequest):
    """Give one branch medicine a fresh starting quantity for another demo run."""
    db_path = Path(__file__).parent.parent / "data" / "inventory.json"
    with open(db_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)

    site = inventory.get("clinics", {}).get(req.clinic_id)
    if not site or site.get("type") != "branch":
        raise HTTPException(status_code=400, detail="Choose a valid branch")
    stock = site.get("inventory", {}).get(req.drug)
    if stock is None:
        raise HTTPException(status_code=400, detail="Medicine not found at this branch")

    stock["quantity"] = 45
    stock["in_transit"] = 0
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(inventory, f, indent=2)
    return {"clinic_id": req.clinic_id, "drug": req.drug, "quantity": 45}

@app.post("/receive")
async def receive_shipment(req: ReceiveRequest):
    db_path = Path(__file__).parent.parent / "data" / "inventory.json"
    if not db_path.exists(): return {"error": "Database not found"}
        
    with open(db_path, "r", encoding="utf-8") as f:
        inventory = json.load(f)
        
    try:
        stock = inventory["clinics"][req.clinic_id]["inventory"][req.drug]
        in_transit = stock.get("in_transit", 0)
        
        if in_transit > 0:
            stock["quantity"] = stock.get("quantity", 0) + in_transit
            stock["in_transit"] = 0
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(inventory, f, indent=2)
                
        return {"status": "success"}
    except KeyError:
        return {"error": "Clinic or drug not found in database"}

@app.post("/chat", response_model=AgentResponse)
async def start_or_continue_chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    return run_agent_turn(session_id, message=request.message)

@app.post("/action", response_model=AgentResponse)
async def handle_human_action(request: ActionRequest):
    decision = request.decision.get("decision", "reject") 
    return run_agent_turn(request.session_id, decision=decision)

if __name__ == "__main__":
    import uvicorn # type: ignore
    uvicorn.run(app, host="127.0.0.1", port=8000)
