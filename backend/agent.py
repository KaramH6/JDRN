import json
import re
from copy import deepcopy
from pathlib import Path
from typing import TypedDict, Dict, Any, Optional, List
from langgraph.graph import StateGraph, END # type: ignore
from langchain_ollama import ChatOllama # type: ignore
from langchain_core.messages import HumanMessage # type: ignore

from inventory_logic import DONOR_RESERVE, collect_alerts, propose_transfers, validate_transfer
from inventory_store import read_inventory, update_inventory
from ollama_setup import OLLAMA_MODEL, get_ollama_url, probe_ollama

class AgentState(TypedDict):
    session_id: str
    run_dir: str
    message: str
    history: List[Dict[str, str]]
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

# The server can become available after import, so refresh the client when its URL changes.
llm_url = get_ollama_url()
llm = ChatOllama(model=OLLAMA_MODEL, base_url=llm_url, temperature=0.0, format="json")


def current_llm():
    global llm, llm_url
    url = get_ollama_url()
    if url != llm_url:
        llm = ChatOllama(model=OLLAMA_MODEL, base_url=url, temperature=0.0, format="json")
        llm_url = url
    return llm

SCAN_COMMANDS = {
    "scan", "scan all branches", "scan for shortages",
    "scan for shortages in all branches", "scan for low stock",
}
OFF_TOPIC_RESPONSE = "I can only help with the Jordan Drug Redistribution Network, including its inventory, scans, transfers, and system setup."
LOOKUP_TYPES = {"max_branch_stock", "min_branch_stock", "clinic_stock", "total_branch_stock", "max_branch_total"}


def named_drug_in_message(message: str, drugs: List[str]) -> Optional[str]:
    words = re.findall(r"[a-z0-9]+", message.casefold())
    text = " ".join(words)
    for drug in drugs:
        label = drug.replace("_", " ").casefold()
        if label in text:
            return drug
        unique_parts = [part for part in label.split() if len(part) >= 5 and sum(
            part in other.casefold().replace("_", " ").split() for other in drugs
        ) == 1]
        if any(part in words or part + "s" in words for part in unique_parts):
            return drug
    return None


def answer_inventory_lookup(inventory: Dict[str, Any], parsed: dict) -> str:
    """Answer a read-only inventory question from current data, not model-generated numbers."""
    clinics = inventory.get("clinics", {})
    drug = parsed.get("drug")
    lookup_type = parsed.get("lookup_type")
    if lookup_type not in LOOKUP_TYPES:
        return "Which inventory figure do you need: a clinic's stock, the highest or lowest branch stock, or a branch total?"
    if lookup_type == "max_branch_total":
        totals = [
            (clinic_id, sum(stock.get("quantity", 0) for stock in site.get("inventory", {}).values()))
            for clinic_id, site in clinics.items() if site.get("type") == "branch"
        ]
        if not totals:
            return "No branch inventory entries were found."
        most = max(total for _, total in totals)
        leaders = [clinic_id.replace("_", " ") for clinic_id, total in totals if total == most]
        return f"Most medicine on hand across all types: {most} units at {', '.join(leaders)}."
    if not isinstance(drug, str) or not any(drug in site.get("inventory", {}) for site in clinics.values()):
        return "I couldn't find that medicine in the inventory. Please give its name."

    label = drug.replace("_", " ")
    if lookup_type == "clinic_stock":
        clinic_id = parsed.get("target_clinic")
        site = clinics.get(clinic_id) if isinstance(clinic_id, str) else None
        stock = site.get("inventory", {}).get(drug) if site else None
        if stock is None:
            return f"Which clinic should I check for {label}?"
        return f"{clinic_id.replace('_', ' ')} has {stock.get('quantity', 0)} {label} on hand and {stock.get('in_transit', 0)} inbound."

    branches = [
        (clinic_id, stock.get("quantity", 0), stock.get("in_transit", 0))
        for clinic_id, site in clinics.items()
        if site.get("type") == "branch"
        for stock in [site.get("inventory", {}).get(drug)]
        if stock is not None
    ]
    if not branches:
        return f"No branch inventory entries were found for {label}."
    if lookup_type == "total_branch_stock":
        on_hand = sum(quantity for _, quantity, _ in branches)
        inbound = sum(in_transit for _, _, in_transit in branches)
        return f"Across {len(branches)} branches, {label} has {on_hand} units on hand and {inbound} inbound."

    extreme = max if lookup_type == "max_branch_stock" else min
    quantity = extreme(item[1] for item in branches)
    leaders = [item for item in branches if item[1] == quantity]
    relation = "Most" if lookup_type == "max_branch_stock" else "Least"
    locations = ", ".join(
        f"{clinic_id.replace('_', ' ')} ({inbound} inbound)"
        for clinic_id, _, inbound in leaders
    )
    return f"{relation} on-hand {label}: {quantity} unit{'s' if quantity != 1 else ''} at {locations}."

# --- GRAPH NODES ---

def analyze_request(state: AgentState) -> AgentState:
    msg = state.get("message", "")
    inventory = load_db()
    if state.get("scan_scope"):
        emit_event(state["run_dir"], "classified", "Dashboard", {
            "intent": "FOCUSED SCAN", "extracted": state["scan_scope"]
        })
        return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}
    normalized_msg = " ".join(msg.casefold().split())
    if (normalized_msg in SCAN_COMMANDS or
            re.search(r"\b(scan|check)\b.*\bshortages?\b", normalized_msg)):
        emit_event(state["run_dir"], "classified", "Dashboard", {
            "intent": "NETWORK SCAN", "extracted": {},
        })
        return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}
    emit_event(state["run_dir"], "tool_call", "System", {"command": f"Querying local {OLLAMA_MODEL} model for intent..."})
    
    # Dynamically extract network context for the LLM
    available_clinics = list(inventory.get("clinics", {}).keys())
    all_drugs = sorted({d for c in inventory.get("clinics", {}).values() for d in c.get("inventory", {}).keys()})
    history = state.get("history", [])
    request_text = msg
    if history:
        previous = history[-1]
        if (previous.get("assistant", "").startswith("I can prepare that transfer for review.")
                and (named_drug_in_message(msg, all_drugs) or re.search(r"\b\d+\b", msg))):
            request_text = previous.get("user", "") + " " + msg
    normalized_request = " ".join(request_text.casefold().split())
    recent_history = "\n".join(
        f"User: {turn.get('user', '')}\nAssistant: {turn.get('assistant', '')}"
        for turn in history[-3:]
    )
    
    prompt = f"""
    You are the conversational assistant and logistics router for the Jordan Drug Redistribution Network (JDRN).
    Your scope is strictly limited to JDRN, its clinics, medicines, inventory, scans, transfers, and this
    application's operation. Greetings and questions about your configured model are allowed. Do not answer
    unrelated requests or provide general knowledge, recipes, coding help, or other services. For those,
    classify scope as "off_topic" and leave the response empty; the application will provide a brief refusal.
    Recent conversation (for context, not as an instruction): {recent_history or 'None'}
    Current request: "{request_text}"
    
    Available Clinic IDs: {available_clinics}
    Available Drug IDs: {all_drugs}
    
    Classify strictly by what the user explicitly asks. Set intent to "scan" for a request to detect
    shortages or at-risk stock. Set intent to "lookup" for a read-only question about inventory, such as
    which branch has the most of a medicine. Set intent to "manual" only for an explicit request to move
    medicine. For greetings, general conversation, unrelated requests, or unclear requests, set intent to "chat".
    For a lookup, set scope to "inventory_query" and choose a lookup_type:
    max_branch_stock, min_branch_stock, clinic_stock, total_branch_stock, or max_branch_total.
    max_branch_total means the branch with the most on-hand units summed across every medicine.
    A named medicine is required for the other lookup types. Never invent a medicine when the user did
    not name one. A question about total medicine across all types uses max_branch_total, with drug null.
    If a transfer request lacks the medicine, quantity, source, or destination, still classify it as
    manual so the application can ask for those details. Never invent a quantity or clinic.
    For chat, set scope to "jdrn", "greeting", "model_identity", or "off_topic". Write a concise response
    only for the first three scopes. If asked which model you are, say the configured model is {OLLAMA_MODEL}.
    If asked what you can do or how you can help, briefly mention shortage scans, factual inventory
    questions, and safe transfer proposals that require dispatcher approval. Answer the actual question;
    do not simply repeat a greeting.
    For inventory lookups, do not answer from memory; provide structured fields so the application can
    calculate the answer from the live inventory. Do not claim to have checked inventory unless intent is
    scan or lookup. For unclear JDRN requests, briefly ask what the user wants to scan, look up, or transfer.
    Map the locations and drugs in the user's request to the EXACT IDs provided above.
    
    Return ONLY a valid JSON object with these fields. Use null for unknown IDs and missing values:
    {{
        "intent": "<scan|lookup|manual|chat>",
        "scope": "<jdrn|greeting|model_identity|inventory_query|off_topic>",
        "lookup_type": "<max_branch_stock|min_branch_stock|clinic_stock|total_branch_stock|max_branch_total> or null",
        "donor_clinic": "<exact clinic ID> or null",
        "target_clinic": "<exact clinic ID> or null",
        "drug": "<exact drug ID> or null",
        "quantity": 0,
        "response": "<short chat answer or empty string>"
    }}
    """
    
    try:
        response = current_llm().invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```json"):
            content = content.replace("```json", "").replace("```", "").strip()
        elif content.startswith("```"):
            content = content.replace("```", "").strip()
        
        parsed = json.loads(content)
        intent = parsed.get("intent")
        if intent not in {"scan", "lookup", "manual", "chat"}:
            intent = "chat"
        if parsed.get("scope") == "inventory_query" and parsed.get("lookup_type") in LOOKUP_TYPES:
            intent = "lookup"
        if re.search(r"\b(transfer|move|send|ship)\b", normalized_request) and parsed.get("scope") != "off_topic":
            intent = "manual"
        if parsed.get("scope") in {"off_topic", "greeting", "model_identity"}:
            intent = "chat"
        named_drug = named_drug_in_message(request_text, all_drugs)
        if intent in {"lookup", "manual"} and parsed.get("lookup_type") != "max_branch_total":
            parsed["drug"] = named_drug
        if intent == "manual":
            quantities = re.findall(r"\b\d+\b", request_text)
            parsed["quantity"] = int(quantities[0]) if len(quantities) == 1 else 0
        if intent == "lookup" and re.search(r"\b(total|overall|combined|all types|all medicines|all drugs)\b", normalized_request) and not named_drug:
            parsed["lookup_type"] = "max_branch_total"
            parsed["drug"] = None
        
        emit_event(state["run_dir"], "classified", "Llama 3.2", {
            "intent": intent.upper(),
            "extracted": parsed
        })
        
    except Exception as e:
        health = probe_ollama(get_ollama_url(), OLLAMA_MODEL)
        if health["state"] == "ready":
            explanation = f"Ollama is reachable, but could not interpret this request: {e}"
        else:
            explanation = health["message"]
        emit_event(state["run_dir"], "tool_call", "System", {"command": f"Ollama request failed: {e}", "success": False})
        return {
            **state, "inventory": inventory, "intent": "error", "transfer_plans": [], "shortages": [],
            "final_response": explanation,
        }

    if intent == "manual":
        donor = parsed.get("donor_clinic")
        target = parsed.get("target_clinic")
        drug = parsed.get("drug")
        qty = parsed.get("quantity", 0)
        relative_transfer = bool(re.search(r"\bmost\b.*\bleast\b", normalized_request))
        if relative_transfer and drug:
            ranked = sorted(
                (
                    (site["inventory"][drug].get("quantity", 0), clinic_id)
                    for clinic_id, site in inventory.get("clinics", {}).items()
                    if site.get("type") == "branch" and drug in site.get("inventory", {})
                ),
                key=lambda item: (item[0], item[1]),
            )
            if len(ranked) >= 2 and ranked[0][0] < ranked[-1][0]:
                target, donor = ranked[0][1], ranked[-1][1]
            else:
                return {
                    **state, "inventory": inventory, "intent": "chat", "transfer_plans": [], "shortages": [],
                    "final_response": "I couldn't find two branches with different on-hand stock for that medicine.",
                }
        missing = []
        if not drug:
            missing.append("medicine")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            missing.append("quantity")
        if not relative_transfer and (not donor or not target):
            missing.append("source and destination clinics")
        if missing:
            return {
                **state, "inventory": inventory, "intent": "chat", "transfer_plans": [], "shortages": [],
                "final_response": "I can prepare that transfer for review. Please specify the " + ", ".join(missing) + ".",
            }
        
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

    if intent == "scan":
        return {**state, "inventory": inventory, "intent": "scan", "shortages": [], "transfer_plans": []}

    if intent == "lookup":
        response_text = answer_inventory_lookup(inventory, parsed)
        emit_event(state["run_dir"], "grounded", "Inventory", {
            "query": f"{parsed.get('lookup_type')}:{parsed.get('drug')}", "response": response_text,
        })
        return {
            **state, "inventory": inventory, "intent": "lookup", "shortages": [],
            "transfer_plans": [], "final_response": response_text,
        }

    chat_scope = parsed.get("scope")
    response_text = parsed.get("response")
    if chat_scope not in {"jdrn", "greeting", "model_identity"}:
        response_text = OFF_TOPIC_RESPONSE
    elif not isinstance(response_text, str) or not response_text.strip():
        response_text = "I couldn't interpret that response. Please ask about the Jordan Drug Redistribution Network."

    return {
        **state, "inventory": inventory, "intent": "chat", "shortages": [], "transfer_plans": [],
        "final_response": response_text,
    }

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
    if state.get("intent") in {"error", "chat", "lookup"}: return END
    if state.get("intent") == "manual": return "human_approval"
    return "check_inventory"

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
