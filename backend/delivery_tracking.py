"""Persist explicitly simulated delivery estimates when a transfer is approved."""

from datetime import timedelta

SAME_GOVERNORATE_MINUTES = 60
OTHER_GOVERNORATE_MINUTES = 180


def record_inbound_shipment(inventory, plan, dispatched_at):
    donor = inventory["clinics"][plan["from"]]
    recipient = inventory["clinics"][plan["to"]]
    same_governorate = bool(donor.get("location")) and donor.get("location") == recipient.get("location")
    minutes = SAME_GOVERNORATE_MINUTES if same_governorate else OTHER_GOVERNORATE_MINUTES
    stock = recipient["inventory"][plan["drug"]]
    stock.setdefault("inbound_shipments", []).append({
        "from": plan["from"],
        "quantity": plan["quantity_to_move"],
        "dispatched_at": dispatched_at.isoformat(),
        "eta": (dispatched_at + timedelta(minutes=minutes)).isoformat(),
        "estimate_kind": "demo",
    })
