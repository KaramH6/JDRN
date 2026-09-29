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

## Ollama startup follow-up

The natural-language terminal previously reported every Ollama failure as “cannot reach Llama 3.2.” A running server with the wrong model directory produced the same message as a stopped server. The backend launcher now checks the model list, uses a server that exposes `llama3.2`, or starts a local JDRN server on port 11435 using `OLLAMA_MODELS`. `GET /ollama/status` feeds the sidebar, and terminal failures show whether the server is offline, the model is missing, or a response could not be parsed. The exact network scan command runs through the existing deterministic inventory workflow without Ollama.

The first launcher version still cached port 11434 because it imported the Ollama configuration before choosing port 11435. Server selection is now read at runtime, and the agent refreshes its Ollama client when the selected URL changes. A regression test covers this ordering, and a live `python start.py` run reported `Ollama is ready with llama3.2` on the model-ready server.

## Chat scope guardrail

Chat replies are generated by the local model, but are limited to JDRN topics: the system, clinic inventory, scans, transfers, and app operation. The model must label a chat response's scope. The backend returns model-written text only for JDRN questions, greetings, and model-identity questions; for an `off_topic` or missing scope it replaces the model text with a short JDRN-only refusal. This keeps the model conversational while preventing the terminal from becoming a general-purpose assistant. A regression test verifies that a model-generated cake recipe is discarded.

## Read-only inventory questions

Natural-language inventory questions can now use a `lookup` intent separately from a shortage `scan`. For example, when asked which branch has the most of a medicine, the model maps the request to a medicine ID and lookup type using the live inventory IDs supplied in its prompt. Python then calculates the leading branch from current inventory and reports on-hand and inbound stock. The lookup path is read-only and does not create a transfer ticket or run the shortage detector. A regression test verifies the result is computed from changing fixture inventory.

## Conversation and inventory answer correction

Recent demo traces showed four response failures: a polite shortage request was refused, a vague transfer produced a generic error, a question about total medicine was answered for one invented medicine, and a follow-up quantity was lost. The router now recognizes explicit shortage requests even with conversational wording. Inventory queries support highest and lowest stock for a named medicine, stock at a clinic, totals for one medicine, and the branch with the most units summed across all medicines. A selected medicine must come from the user's words; the backend computes figures from the live inventory. Incomplete transfer requests ask for missing details, and a transfer from the highest-stock branch to the lowest-stock branch derives both facilities from live stock once the medicine and quantity are provided. Transfer safety checks and dispatcher approval still apply.

The terminal sends a conversation ID with each message, letting the backend keep the last four exchanges in memory for follow-up requests. Event logs now include the original request and returned response under that ID, so future conversations can be reconstructed. This history disappears when the backend restarts.

The router also preserves whole-inventory shortage requests when the model attaches inventory lookup fields to a `scan` classification. The exact phrase "all the entire inventory for shortages" now enters the shortage workflow instead of asking for a medicine name.
