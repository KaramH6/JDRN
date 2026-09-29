"""Explainable stock alerts and transfer proposals for the MVP."""

WARNING_THRESHOLD = 30
RESTOCK_TARGET = 45
DONOR_RESERVE = 50


def stock_status(quantity):
    if quantity <= 0:
        return "out_of_stock"
    if quantity <= WARNING_THRESHOLD:
        return "at_risk"
    return "stable"


def collect_alerts(inventory, scope=None):
    alerts = []
    for clinic_id, clinic in inventory.get("clinics", {}).items():
        if clinic.get("type") != "branch":
            continue
        if scope and clinic_id != scope[0]:
            continue
        for drug, stock in clinic.get("inventory", {}).items():
            if scope and drug != scope[1]:
                continue
            quantity = stock.get("quantity", 0)
            status = stock_status(quantity)
            if status != "stable":
                alerts.append({
                    "clinic": clinic_id,
                    "drug": drug,
                    "quantity": quantity,
                    "in_transit": stock.get("in_transit", 0),
                    "status": status,
                })
    return alerts


def propose_transfers(inventory, alerts):
    """Choose one eligible donor per alert, reserving stock for its own patients."""
    clinics = inventory["clinics"]
    available = {
        clinic_id: {drug: stock.get("quantity", 0) for drug, stock in clinic["inventory"].items()}
        for clinic_id, clinic in clinics.items()
    }
    plans = []
    unresolved = []

    for alert in alerts:
        clinic_id, drug = alert["clinic"], alert["drug"]
        needed = RESTOCK_TARGET - alert["quantity"] - alert["in_transit"]
        if needed <= 0:
            unresolved.append(f"{clinic_id}: {drug} already has enough stock inbound; receive it first.")
            continue

        region = clinics[clinic_id].get("location")
        candidates = []
        for donor_id, donor in clinics.items():
            if donor_id == clinic_id or drug not in donor.get("inventory", {}):
                continue
            spare = available[donor_id][drug] - DONOR_RESERVE
            if spare > 0:
                candidates.append((donor_id, donor, spare))
        if not candidates:
            unresolved.append(f"{clinic_id}: no donor can spare {drug} while keeping {DONOR_RESERVE} units.")
            continue

        # Prefer a single donor that can meet the target, then locality and HQ.
        candidates.sort(key=lambda item: (
            item[2] < needed,
            item[1].get("location") != region,
            item[1].get("type") != "hq",
            -item[2],
            item[0],
        ))
        donor_id, donor, spare = candidates[0]
        quantity = min(needed, spare)
        before = available[donor_id][drug]
        available[donor_id][drug] -= quantity
        proximity = "same governorate" if donor.get("location") == region else "another governorate"
        coverage = "covers the target" if spare >= needed else "offers a partial transfer"
        plans.append({
            "from": donor_id,
            "to": clinic_id,
            "drug": drug,
            "quantity_to_move": quantity,
            "donor_before": before,
            "donor_after": before - quantity,
            "recipient_before": alert["quantity"],
            "recipient_in_transit": alert["in_transit"],
            "reason": f"{coverage}; {proximity}; donor keeps at least {DONOR_RESERVE} units",
            "kind": "automatic",
        })
    return plans, unresolved


def validate_transfer(inventory, plan):
    """Return an explanation if a ticket is no longer safe to dispatch."""
    clinics = inventory.get("clinics", {})
    donor = clinics.get(plan["from"], {}).get("inventory", {}).get(plan["drug"])
    recipient = clinics.get(plan["to"], {}).get("inventory", {}).get(plan["drug"])
    quantity = plan["quantity_to_move"]
    if donor is None or recipient is None or not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return "The ticket contains an invalid clinic, medicine, or quantity. Scan again."
    if donor.get("quantity", 0) - quantity < DONOR_RESERVE:
        return f"{plan['from']} can no longer keep its {DONOR_RESERVE}-unit reserve. Scan again."
    if plan.get("kind") == "automatic":
        on_hand = recipient.get("quantity", 0)
        if stock_status(on_hand) == "stable":
            return f"{plan['to']} is no longer at risk. Scan again."
        if on_hand + recipient.get("in_transit", 0) + quantity > RESTOCK_TARGET:
            return f"{plan['to']} now has more stock or inbound medicine. Scan again."
    return None
