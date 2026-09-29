import json
from copy import deepcopy
from pathlib import Path
from typing import TypedDict, Dict, Any, Optional, List
from langgraph.graph import StateGraph, END # type: ignore
from langchain_ollama import ChatOllama # type: ignore
from langchain_core.messages import HumanMessage # type: ignore

from inventory_logic import DONOR_RESERVE, collect_alerts, propose_transfers, validate_transfer
from inventory_store import read_inventory, update_inventory

class AgentState(TypedDict):
    session_id: str
    run_dir: str
    message: str
    intent: Optional[str]
    inventory: Dict[str, Any]
    shortages: List[Dict[str, Any]]
    transfer_plans: List[Dict[str, Any]]
    human_decision: Optional[str]
    final_response: Optional[str]
    scan_scope: Optional[tuple]
    unresolved: List[str]

def emit_event(run_dir: str, ev: str, node: str, payload: dict):
    events_path = Path(run_dir) / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ev": ev, "node": node, "payload": payload}) + "\n")

def load_db() -> Dict[str, Any]:
    return read_inventory()

# Connect to your local Ollama instance. Forcing JSON format ensures clean extraction.
llm = ChatOllama(model="llama3.2", temperature=0.0, format="json")

# --- GRAPH NODES ---

def analyze_request(state: AgentState) -> AgentState:
    msg = state.get("message", "")
    inventory = load_db()
    if state.get("scan_scope"):
        emit_event(state["run_dir"], "classified", "Dashboard", {
            "intent": "FOCUSED SCAN", "extracted": state["scan_scope"]
        })
        return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}
    emit_event(state["run_dir"], "tool_call", "System", {"command": "Querying local Llama 3.2 model for intent..."})
    
    # Dynamically extract network context for the LLM
    available_clinics = list(inventory.get("clinics", {}).keys())
    all_drugs = list({d for c in inventory.get("clinics", {}).values() for d in c.get("inventory", {}).keys()})
    
    prompt = f"""
    You are the logistics router for the Jordan Drug Redistribution Network.
    User request: "{msg}"
    
    Available Clinic IDs: {available_clinics}
    Available Drug IDs: {all_drugs}
    
    Analyze the request. If the user wants to check for shortages or scan the system, set intent to "scan".
    If the user wants to move, transfer, or send medicine, set intent to "manual".
    Map the locations and drugs in the user's request to the EXACT IDs provided above.
    
    Return ONLY a valid JSON object matching this structure:
    {{
        "intent": "scan" or "manual",
        "donor_clinic": "exact_id_or_null",
        "target_clinic": "exact_id_or_null",
        "drug": "exact_id_or_null",
        "quantity": integer_or_0
    }}
    """
    
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        elif content.startswith("```"):
            content = content.replace("```", "").strip()
        
        parsed = json.loads(content)
        intent = parsed.get("intent", "scan")
        
        emit_event(state["run_dir"], "classified", "Llama 3.2", {
            "intent": intent.upper(),
            "extracted": parsed
        })
        
    except Exception as e:
        emit_event(state["run_dir"], "tool_call", "System", {"command": f"LLM Parsing failed: {e}", "success": False})
        return {
            **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
            "final_response": "SYSTEM HALT: Cannot reach local Llama 3.2 engine. Verify the Ollama background process is running."
        }

    if intent == "manual":
        donor = parsed.get("donor_clinic")
        target = parsed.get("target_clinic")
        drug = parsed.get("drug")
        qty = parsed.get("quantity", 0)
        
        # LLM Guardrails: Ensure Llama didn't hallucinate IDs or try to self-transfer
        clinics = inventory.get("clinics", {})
        if (not donor or not target or not drug or donor == target
                or donor not in clinics or target not in clinics
                or drug not in clinics[donor].get("inventory", {})
                or drug not in clinics[target].get("inventory", {})):
            emit_event(state["run_dir"], "proposed", "Logistics", {"reason": "LLM routing error: Invalid or missing parameters.", "changes": []})
            return {
                **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
                "final_response": "❌ Transfer aborted: Ensure you specify a valid source, destination, and drug name."
            }

        actual_stock = clinics[donor]["inventory"][drug].get("quantity", 0)
        
        if (not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0
                or actual_stock - qty < DONOR_RESERVE):
            emit_event(state["run_dir"], "proposed", "Logistics", {"reason": "Override REJECTED. Insufficient stock.", "changes": []})
            return {
                **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
                "final_response": f"❌ Transfer declined: {donor.replace('_', ' ')} has {actual_stock} units and must keep {DONOR_RESERVE}."
            }

        plan = {
            "from": donor,
            "to": target,
            "drug": drug,
            "quantity_to_move": qty,
            "donor_before": actual_stock,
            "donor_after": actual_stock - qty,
            "recipient_before": clinics[target]["inventory"][drug].get("quantity", 0),
            "recipient_in_transit": clinics[target]["inventory"][drug].get("in_transit", 0),
            "reason": f"manual dispatcher request; donor keeps at least {DONOR_RESERVE} units",
            "kind": "manual",
        }
        
        emit_event(state["run_dir"], "proposed", "Logistics", {
            "reason": "Manual dispatcher override.", 
            "changes": [{"target": drug, "from": donor, "to": target}]
        })
        return {**state, "inventory": inventory, "intent": "manual", "transfer_plans": [plan], "shortages": []}
        
    return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}

def check_inventory(state: AgentState) -> AgentState:
    emit_event(state["run_dir"], "tool_call", "InventoryMonitor", {"command": "Check branch stock against early-warning threshold"})
    inventory = state.get("inventory", {})
    return {**state, "shortages": collect_alerts(inventory, state.get("scan_scope"))}

def route_issue(state: AgentState) -> AgentState:
    shortages = state.get("shortages", [])
    if shortages:
        emit_event(state["run_dir"], "routed", "Router", {"assignments": [{"domain": f"Logistics Agent ({len(shortages)} alerts detected)"}]})
        return state
    return {**state, "final_response": "No at-risk or out-of-stock branch medicine found for this scan."}

def find_surplus(state: AgentState) -> AgentState:
    plans, unresolved = propose_transfers(state["inventory"], state.get("shortages", []))
    for plan in plans:
        emit_event(state["run_dir"], "proposed", "Logistics", {
            "reason": plan["reason"],
            "changes": [{"target": plan["drug"], "from": plan["from"], "to": plan["to"]}],
        })
    response = None if plans else "No safe transfer is available. " + " ".join(unresolved)
    return {**state, "transfer_plans": plans, "unresolved": unresolved, "final_response": response}

def human_approval_gate(state: AgentState) -> AgentState:
    decision = state.get("human_decision")
    if decision in ["approve", "reject"]:
        emit_event(state["run_dir"], "plan_decided", "HITL Gate", {"outcome": decision, "by": "Medical Dispatcher"})
        return state
        
    emit_event(state["run_dir"], "plan_parked", "HITL Gate", {
        "gate_id": "BATCH-TRANS-101", 
        "impact": f"High ({len(state.get('transfer_plans', []))} Relocations Pending)"
    })
    return state

def execute_transfer(state: AgentState) -> AgentState:
    decision = state.get("human_decision")
    plans = state.get("transfer_plans", [])
    if decision not in ["approve", "reject"] or not plans:
        return state
    emit_event(state["run_dir"], "plan_decided", "HITL Gate", {"outcome": decision, "by": "Medical Dispatcher"})
    if decision == "reject":
        return {**state, "human_decision": "executed", "final_response": "Transfer rejected by dispatcher. Inventory was not changed."}

    class TransferConflict(Exception):
        pass

    def dispatch(inventory):
        # Validate the entire batch before changing any item.
        preview = deepcopy(inventory)
        for plan in plans:
            problem = validate_transfer(preview, plan)
            if problem:
                raise TransferConflict(problem)
            donor = preview["clinics"][plan["from"]]["inventory"][plan["drug"]]
            recipient = preview["clinics"][plan["to"]]["inventory"][plan["drug"]]
            donor["quantity"] -= plan["quantity_to_move"]
            recipient["in_transit"] = recipient.get("in_transit", 0) + plan["quantity_to_move"]
        inventory["clinics"] = preview["clinics"]

    try:
        update_inventory(dispatch)
    except TransferConflict as error:
        emit_event(state["run_dir"], "tool_call", "System", {"command": str(error), "success": False})
        return {**state, "human_decision": "executed", "final_response": f"Transfer not dispatched: {error}"}

    emit_event(state["run_dir"], "tool_call", "System", {"command": "Reserved donor stock and marked shipments inbound", "success": True})
    summary = "\n".join(
        f"• {plan['quantity_to_move']} {plan['drug'].replace('_', ' ')}: {plan['from'].replace('_', ' ')} → {plan['to'].replace('_', ' ')}"
        for plan in plans
    )
    return {**state, "human_decision": "executed", "final_response": "Transfer approved; shipment is inbound.\n" + summary}

workflow = StateGraph(AgentState)
workflow.add_node("analyze_request", analyze_request)
workflow.add_node("check_inventory", check_inventory)
workflow.add_node("route_issue", route_issue)
workflow.add_node("find_surplus", find_surplus)
workflow.add_node("human_approval", human_approval_gate)
workflow.add_node("execute_transfer", execute_transfer)

workflow.set_entry_point("analyze_request")

def route_intent(state: AgentState) -> str:
    if state.get("intent") == "error": return END
    return "human_approval" if state.get("intent") == "manual" else "check_inventory"

workflow.add_conditional_edges("analyze_request", route_intent)
workflow.add_edge("check_inventory", "route_issue")

def route_condition(state: AgentState) -> str:
    return "find_surplus" if state.get("shortages") else END

workflow.add_conditional_edges("route_issue", route_condition)
workflow.add_edge("find_surplus", "human_approval")

def check_human_decision(state: AgentState) -> str:
    return "execute_transfer" if state.get("human_decision") in ["approve", "reject"] else END

workflow.add_conditional_edges("human_approval", check_human_decision)
workflow.add_edge("execute_transfer", END)

jdrn_graph = workflow.compile()
