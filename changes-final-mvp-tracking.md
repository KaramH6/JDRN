# Final MVP tracking changes

Branch: `feature/final-mvp-tracking`

Date: 3 October 2026

The earlier MVP already demonstrated low-stock detection, donor selection with a fixed reserve, approval, and inbound stock until receipt. This branch adds stock update times and inbound delivery estimates so the dashboard can demonstrate the two remaining tracking claims in the hackathon documents.

## Last updated time

Every medicine row now displays **Last updated** in Amman time. The backend stores `last_updated` as a UTC ISO timestamp when that stock record changes. This covers demo consumption and restock, donor deduction and recipient inbound stock at dispatch, and receipt. Only affected medicine records get a new timestamp.

Dashboard refreshes, scans, rejection, and failed approval do not make stock appear more recent. Older records show **Not recorded** until changed; missing dates are not filled with the server startup or refresh time. This records an application stock change, not a verified physical count. Direct JSON edits bypass this mechanism.

`backend/inventory_store.py` applies timestamps centrally inside the existing process lock and atomic file replacement. Reading legacy inventory supplies compatible default fields without rewriting the file.

## Inbound delivery estimates

On approval, the backend adds an `inbound_shipments` entry containing donor, quantity, dispatch time, ETA, and the `demo` estimate label. The estimate starts at approval, not at the earlier proposal. Both automatic and manual transfers use it.

The demonstration policy in `backend/delivery_tracking.py` is:

| Donor and recipient | Estimated delivery interval |
| --- | --- |
| Same known governorate | 60 minutes after approval |
| Different or unknown governorates | 180 minutes after approval |

These are explicit demo assumptions. They are not measured routes, courier commitments, or live travel predictions. The dashboard labels them **Demo ETA**, shows the expected arrival in Amman time and minutes remaining, and keeps separate estimates for separate inbound transfers. Countdown labels refresh approximately once per minute through the existing five-second polling.

After an estimate passes, the dashboard shows **Awaiting receipt**. Time passing never changes on-hand quantity. The existing **INBOUND** action confirms all inbound units for that medicine, adds them to on-hand stock, and clears its shipment estimates. Legacy inbound units without shipment records show **ETA not recorded**, including when mixed with newer tracked transfers.

## Files changed

- `backend/inventory_store.py`: compatible tracking defaults and timestamps on changed stock records.
- `backend/delivery_tracking.py`: demonstration delivery policy and shipment metadata.
- `backend/agent.py`: creates shipment records during the existing approval transaction.
- `backend/main.py`: clears inbound shipment estimates on receipt.
- `frontend/renderer.js`: timestamps, separate ETAs, countdowns, overdue and missing-data labels, and Amman timezone formatting.
- `backend/tests/test_inventory_flow.py`: tracking lifecycle and failure regression coverage.
- `frontend/tests/tracking.test.cjs`: dashboard rendering and countdown regression coverage.
- `readme.md`: demo behavior, data fields, assumptions, and verification commands.
- `.gitignore`: excludes runtime logs, Python bytecode, and local virtual environments. Previously tracked logs and bytecode are removed from the Git index; local copies remain available.
- `data/inventory.json`: includes the existing local demo quantities present before this work. Tests use temporary fixtures and do not consume or reset this file.

All existing application source is included through the parent branch. The 30/45/50 unit warning, target, and reserve rules still define the transfer policy. Expiry filtering, cold-chain and controlled-medicine checks, authentication, live inventory integrations, and real logistics predictions remain outside this MVP. Receipt still operates on the total inbound quantity per medicine, rather than signing for individual shipments. Pending approval tickets still live in memory.

## Validation

Verified:

- **30 backend tests passed**, including the original 26 and four new tracking tests.
- **Three frontend tests passed**, exercising actual dashboard rendering with a simulated clock and DOM elements.
- JavaScript syntax check passed.

New checks cover stock timestamps after consume/restock, unchanged timestamps on reads/scans/rejection, timestamps on donor and recipient after dispatch, persistent ETAs, repeated-approval protection, failed-approval rollback, multiple manual shipments with separate cross-governorate ETAs, and receipt cleanup. Frontend checks cover Amman time, countdown progression with unchanged inventory, overdue labels, missing legacy data, mixed tracked/untracked inbound stock, escaped donor names, and hiding ETAs after receipt.

Run from the project root:

```powershell
python -B -m unittest discover -s backend/tests -q
node --check frontend/renderer.js
node --test --test-isolation=none frontend/tests/tracking.test.cjs
```

The frontend tests use Node 24's in-process runner; this avoids subprocess restrictions in the development sandbox. They verify generated dashboard markup, not a full browser screenshot. Backend tests use isolated inventory and mock model calls where relevant.

For a manual demo, restart the backend, reset Amman East salbutamol, start the simulation, review and approve the alert, then return to the dashboard. The changed medicine records show updated times, and the inbound quantity shows a demo ETA. Click **INBOUND** after delivery to confirm receipt and clear the ETA.

## Replacement Task 2 prototype refinement paragraph

The prototype was therefore refined around safe redistribution rather than redistribution alone. It flags low stock, selects a donor while preserving a fixed 50-unit reserve, shows the donor's remaining stock, and requires human approval before dispatch. Each medicine displays its last stock update time, while approved transfers remain marked as inbound with a clearly labelled demo arrival estimate until receipt is confirmed. Stock is checked again at approval to prevent stale or unsafe transfers.
