import json
import re
from pathlib import Path
from typing import TypedDict, Dict, Any, Optional, List, cast
from langgraph.graph import StateGraph, END # type: ignore
from langchain_ollama import ChatOllama # type: ignore
from langchain_core.messages import HumanMessage # type: ignore

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

def emit_event(run_dir: str, ev: str, node: str, payload: dict):
    events_path = Path(run_dir) / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ev": ev, "node": node, "payload": payload}) + "\n")

def load_db() -> Dict[str, Any]:
    db_path = Path(__file__).parent.parent / "data" / "inventory.json"
    with open(db_path, "r", encoding="utf-8") as f:
        return json.load(f)

# Connect to your local Ollama instance. Forcing JSON format ensures clean extraction.
llm = ChatOllama(model="llama3.2", temperature=0.0, format="json")

# --- GRAPH NODES ---

def analyze_request(state: AgentState) -> AgentState:
    msg = state.get("message", "")
    inventory = load_db()
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
        if not donor or not target or not drug or donor == target:
            emit_event(state["run_dir"], "proposed", "Logistics", {"reason": "LLM routing error: Invalid or missing parameters.", "changes": []})
            return {
                **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
                "final_response": "❌ Transfer aborted: Ensure you specify a valid source, destination, and drug name."
            }

        actual_stock = inventory.get("clinics", {}).get(donor, {}).get("inventory", {}).get(drug, {}).get("quantity", 0)
        
        if qty <= 0 or qty > actual_stock:
            emit_event(state["run_dir"], "proposed", "Logistics", {"reason": "Override REJECTED. Insufficient stock.", "changes": []})
            return {
                **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
                "final_response": f"❌ Logistics Error: Cannot transfer {qty} units. {donor.replace('_', ' ')} currently has {actual_stock} units of {drug.replace('_', ' ')}."
            }

        plan = {
            "from": donor,
            "to": target,
            "drug": drug,
            "quantity_to_move": qty
        }
        
        emit_event(state["run_dir"], "proposed", "Logistics", {
            "reason": "Manual dispatcher override.", 
            "changes": [{"target": drug, "from": donor, "to": target}]
        })
        return {**state, "inventory": inventory, "intent": "manual", "transfer_plans": [plan], "shortages": []}
        
    return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}

def check_inventory(state: AgentState) -> AgentState:
    emit_event(state["run_dir"], "tool_call", "InventoryMonitor", {"command": "data find --target clinics"})
    inventory = state.get("inventory", {})
    
    shortages: List[Dict[str, Any]] = []
    for clinic_id, data in inventory.get("clinics", {}).items():
        for drug_name, details in data.get("inventory", {}).items():
            if details.get("quantity", 0) <= 0:
                shortages.append({"clinic": clinic_id, "drug": drug_name})
                
    return {**state, "shortages": shortages}

def route_issue(state: AgentState) -> AgentState:
    shortages = state.get("shortages", [])
    if shortages:
        emit_event(state["run_dir"], "routed", "Router", {"assignments": [{"domain": f"Logistics Agent ({len(shortages)} shortages detected)"}]})
        return state
    return {**state, "final_response": "All clinic inventories are stable. No shortages detected."}

def find_surplus(state: AgentState) -> AgentState:
    shortages = state.get("shortages", [])
    inventory = state.get("inventory", {})
    plans: List[Dict[str, Any]] = []
    
    temp_stock: Dict[str, Dict[str, int]] = {}
    for cid, cdata in inventory.get("clinics", {}).items():
        temp_stock[cid] = {}
        for dname, ddetails in cdata.get("inventory", {}).items():
            temp_stock[cid][dname] = ddetails.get("quantity", 0)

    for item in shortages:
        clinic = item["clinic"]
        drug = item["drug"]
        target_region = inventory["clinics"][clinic].get("location")
        
        emit_event(state["run_dir"], "grounded", "Logistics", {
            "query": f"Surplus search for {drug} needed at {clinic}",
            "results": "Calculating optimal donor node..."
        })
        
        best_donor = None
        best_rank = None
        for donor_id, dstock in temp_stock.items():
            if donor_id == clinic:
                continue
            cur_qty = dstock.get(drug, 0)
            if cur_qty <= 0:
                continue
            donor = inventory["clinics"][donor_id]
            rank = (
                donor.get("location") == target_region,
                donor.get("type") == "hq",
                cur_qty,
            )
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_donor = donor_id
                
        if best_donor:
            max_qty = temp_stock[best_donor][drug]
            safe_surplus = max_qty - 50
            transfer_amount = min(50, safe_surplus) if safe_surplus > 0 else min(20, max_qty)
            if transfer_amount <= 0:
                transfer_amount = min(10, max_qty)
                
            temp_stock[best_donor][drug] -= transfer_amount
            
            plan = {
                "from": best_donor,
                "to": clinic,
                "drug": drug,
                "quantity_to_move": transfer_amount
            }
            plans.append(plan)
            
            emit_event(state["run_dir"], "proposed", "Logistics", {
                "reason": f"Preventing stockout at {clinic}", 
                "changes": [{"target": drug, "from": best_donor, "to": clinic}]
            })
    
    return {**state, "transfer_plans": plans}

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
    
    if decision not in ["approve", "reject"]: return state
    if decision == "executed": return state

    if decision == "approve":
        emit_event(state["run_dir"], "tool_call", "System", {"command": "Dispatching logistics vehicles...", "success": True})
        
        db_path = Path(__file__).parent.parent / "data" / "inventory.json"
        inventory = state.get("inventory", {})
        
        summary_lines = []
        for plan in plans:
            donor_stock = inventory.get("clinics", {}).get(plan["from"], {}).get("inventory", {}).get(plan["drug"], {})
            target_stock = inventory.get("clinics", {}).get(plan["to"], {}).get("inventory", {}).get(plan["drug"], {})
            
            donor_stock["quantity"] = donor_stock.get("quantity", 0) - plan["quantity_to_move"]
            target_stock["in_transit"] = target_stock.get("in_transit", 0) + plan["quantity_to_move"]
            
            summary_lines.append(f"• {plan['quantity_to_move']} units of {plan['drug'].replace('_', ' ')}: {plan['from'].replace('_', ' ')} ➔ {plan['to'].replace('_', ' ')}")
            
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump(inventory, f, indent=2)
            
        return {
            **state,
            "human_decision": "executed",
            "final_response": "✅ Dispatch Authorized & Confirmed:\n" + "\n".join(summary_lines)
        }
    else:
        return {**state, "human_decision": "executed", "final_response": "❌ Transfer rejected by dispatcher."}

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
