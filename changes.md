# Proactive stockout prevention change

## Purpose

The proposal describes preventing shortages before they reach patients. The earlier demo noticed a branch only after its on-hand quantity reached zero. This change lets a dispatcher act while 1–30 units remain and shows why a proposed donor can safely help. It keeps the decision with a person and uses the simulated network already in the app.

## What changed

- **Earlier, clearer alerts:** Branch stock is marked **At risk** at 1–30 units and **Out of stock** at zero. The dashboard shows on-hand and inbound quantities separately. The demo pauses at the first warning so judges can follow the intervention before a stockout.
- **Focused review:** Each alert has a **Review transfer** action. `POST /scan` accepts clinic and medicine IDs, checks live JSON inventory, and returns a transfer ticket or an explanation when no transfer is needed. This path is deterministic and works without Ollama. The terminal's natural-language commands remain available.
- **Explainable safe transfer:** Automatic plans aim for 45 total on-hand plus inbound units. A donor must keep 50 units. A source that can cover the target ranks first, followed by same governorate, HQ, then most spare stock. If no one source can cover the target, a partial transfer can still be proposed. The ticket shows current recipient stock, inbound stock, donor stock before and after dispatch, and the selection reason. These rules do not claim a measured route or travel time.
- **Reliable authorization:** Each scan gets its own ticket ID. Approval uses current inventory, rejects stale or unsafe proposals, and cannot run twice for one ticket. Rejection changes no stock. Approval places the shipment in `in_transit`; receipt moves it to on-hand stock. JSON updates use a process lock and atomic file replacement for the single-process local demo.
- **Repeatable demo:** Amman East salbutamol resets to 32 units. One simulated 15-unit consumption step produces a 17-unit warning. Demo reset refuses to erase an inbound shipment.
- **Documentation and verification:** The README gives the exact demo, run steps, rules, interfaces, and MVP limits. Integration tests cover warning, approval, receipt, rejection, stale stock, repeat approval, donor reserve, inbound coverage, and a mocked natural-language transfer.

## Business need → technical behavior

| Business need | What the dispatcher sees | Technical connection | What can be demonstrated |
| --- | --- | --- | --- |
| Act before a clinic runs out | A distinct at-risk alert while on-hand stock remains | A 30-unit threshold in the dashboard and backend scan logic | Amman East changes from 32 to 17 units and raises a warning before zero. |
| Find medicine without creating another shortage | A proposed donor and its balance after dispatch | Deterministic donor ranking, a 50-unit reserve, and a 45-unit recipient target | Judges can see the exact quantity moved and that donor stock stays above reserve. |
| Keep clinical staff in control | A ticket with **Approve** and **Reject** actions | A human approval gate and one-use ticket ID | Rejection leaves stock untouched; approval creates an inbound shipment. |
| Make delivery visible | An **INBOUND** receipt action and updated stock | Separate `quantity` and `in_transit` values in JSON | The warning clears after receipt if the clinic has over 30 units. |
| Avoid misleading recommendations | A reason when no safe donor is available | Approval-time validation against current inventory | If a donor's stock drops after a scan, dispatch is declined and the dispatcher scans again. |

## Judging explanation

“A clinic does not need to wait until a medicine reaches zero. JDRN flags low stock, checks which simulated facility can spare enough while protecting its own patients, and presents a transparent transfer for a dispatcher to approve. The shipment stays inbound until the receiving clinic confirms it.”

The AI component interprets free-text terminal requests through local Ollama. The stock thresholds, donor choice, reserve, and approval checks are deterministic Python rules so the dispatch decision is explainable and repeatable. This MVP uses simulated JSON inventory rather than a live Hakeem integration. It provides early warning, not a statistically validated prediction of demand.

## Files and interfaces

- `frontend/renderer.js` and `frontend/index.html`: at-risk display, focused review, ticket details, and demo pause.
- `backend/inventory_logic.py`: alert thresholds, donor ranking, and transfer validation.
- `backend/inventory_store.py`: serialized local inventory reads and updates.
- `backend/agent.py` and `backend/main.py`: focused scan, approval, receipt, and terminal workflow.
- `backend/tests/test_inventory_flow.py`: isolated API and routing checks.
- New `POST /scan` body: `{ "clinic_id": "Amman_East", "drug": "Salbutamol_Inhaler" }`. It returns the same ticket response shape as `POST /chat`. `POST /action` accepts the returned `session_id` and a decision of `approve` or `reject`.

## Practical limits

The JSON file and in-memory ticket store support one local backend process. A restart loses pending tickets, and multiple backend workers need a shared transactional database. The 30/45/50 unit values are demo policy, not clinically validated thresholds. Expiry, actual travel distance, live facility feeds, roles, and audit-grade identity are outside this MVP. Frontend CDN assets need internet for full styling.
