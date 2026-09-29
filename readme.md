# Jordan Drug Redistribution Network (JDRN)

JDRN is a local hackathon MVP for spotting medicine shortages at clinics and preparing safe stock transfers for a dispatcher to approve. It uses simulated inventory for 16 sites across Amman, Zarqa, Mafraq, and Al-Karak. It does not connect to a live health record system.

## What the demo shows

1. The dashboard reads `data/inventory.json` every five seconds. A branch with **1–30 units** is **At risk**; a branch with **0 units** is **Out of stock**. Inbound stock remains separate until received.
2. Select **Amman East** and **Salbutamol Inhaler** in Demo mode. Use **Restock selected** if you need a fresh run; this pair resets to 32 units. Start the simulation. One 15-unit step leaves 17 units and pauses with an early warning.
3. Choose **Review transfer** on that alert. The focused scan checks live inventory and proposes a donor, transfer quantity, donor balance after dispatch, and a short reason. Focused scans do not require Ollama.
4. Approve or reject the ticket. Approval checks current inventory again, deducts donor stock, and records the quantity as inbound at the recipient. The same ticket cannot dispatch twice.
5. Return to the dashboard and click **INBOUND** to record receipt. The quantity moves into on-hand stock and the warning clears if stock rises above 30.

The terminal also supports a network-wide scan and natural-language transfer requests through a local Ollama `llama3.2` model. Keep Ollama running for those commands.

## Transfer rules

- Only branch medicines generate low-stock alerts. The alert threshold is 30 units and the proposed target is 45 on-hand plus inbound units.
- An automatic proposal chooses one eligible donor: a source that can meet the 45-unit target first, then the same governorate, then an HQ, then the site with the most spare stock. If no single donor can meet the target, it proposes the safest available partial transfer. This ranks simulated sites; it does not calculate travel distance.
- Every donor must retain at least 50 on-hand units after dispatch. If no donor can do that, the system explains why it cannot propose a transfer. Existing inbound stock is counted to avoid repeat proposals.
- A dispatcher must approve a proposal. Approval rechecks latest inventory; a stale or unsafe ticket is declined. Rejection leaves inventory unchanged.
- Manual terminal transfers use the same 50-unit donor reserve.

## Run locally

Use Python 3.10+ and two terminals. From the project root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn main:app --reload
```

In a second terminal:

```powershell
cd frontend
python -m http.server 3000
```

Open <http://localhost:3000>. For the natural-language terminal, install Ollama and pull `llama3.2` with `ollama pull llama3.2`. The dashboard and focused alert review work without it. The page loads Tailwind and fonts from CDNs, so styling needs internet access unless those assets are hosted locally.

## How the pieces fit

| Operational step | MVP implementation |
| --- | --- |
| Clinic stock becomes low | `GET /inventory` supplies the dashboard; focused and network scans apply the same threshold. |
| Dispatcher reviews a transfer | `POST /scan` creates a focused ticket; `POST /chat` handles terminal commands. Python rules check donor stock and choose a source. |
| Dispatcher authorizes movement | `POST /action` revalidates the ticket and records donor deduction plus recipient inbound stock in `data/inventory.json`. |
| Clinic confirms delivery | `POST /receive` moves inbound units to on-hand stock. |

`backend/agent.py` runs the LangGraph workflow. `backend/inventory_logic.py` holds alert and routing rules. `backend/inventory_store.py` serializes local JSON changes and replaces the file after a successful update. Ticket state lives in memory, so a backend restart clears pending approvals.

## Verify

From the project root:

```powershell
python -B -m unittest discover -s backend/tests -v
node --check frontend/renderer.js
```

The tests use isolated inventory files; they do not change `data/inventory.json`.

## MVP boundary

The current warning is a stock threshold, not a validated burn-rate forecast. The system does not estimate travel time, inspect medicine expiry when routing, authenticate dispatchers, or integrate with Hakeem. Those need real operational data and a production design. See `changes.md` for the feature rationale and its business-to-technical mapping.
