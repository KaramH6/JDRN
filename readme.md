# Jordan Drug Redistribution Network (JDRN)
Jordan 2076 Hackathon

## MVP stockout demo

`data/inventory.json` contains four governorates (Amman, Zarqa, Mafraq, and Al-Karak). Each has one main storage HQ and three branches. The dashboard reads the file through the Python API every five seconds while the page is open. A stockout means `quantity` is `0` or below; `in_transit` is shown separately until a shipment is received.

For the judging demo, open the dashboard and use **Demo mode: simulate demand**. Choose a branch and medicine, then click **Start simulation**. The backend consumes 15 units every two seconds and stops at zero. The dashboard will show a stockout notice and a **Review in terminal** button. In the terminal, submit the prefilled scan request, review the proposed transfers, and approve or reject them. Approval subtracts stock from the donor and adds it to the recipient's `in_transit`; click the recipient's **INBOUND** button to receive it. The stockout notice clears after receipt.

Keep the backend and Ollama running during the demo. Use **Restock selected** to give the chosen branch medicine 45 units for another run. You can still edit `data/inventory.json` manually; changes are picked up on the next poll. Simulation changes are saved to that file. No external notification service is needed for this MVP.

## Conceptual Overview (Had men el proposal)

**The Problem:** While Jordan allocates a significant portion of its GDP to healthcare, its medical supply chain suffers from a "last-mile" logistics gap[cite: 1]. Fragmented systems mean a central hospital in Amman or Zarqa might sit on surplus inventory while a rural clinic in Mafraq faces critical, life-threatening stockouts of sensitive items like insulin or specialized infant formula.

**The Solution:** JDRN is an AI-driven, early-warning supply chain overlay[cite: 1]. It acts as an intelligent bridge between existing clinic databases (simulating Jordan's *Hakeem* system)[cite: 1]. Instead of waiting for a rural clinic to run out of medicine, the system proactively scans for localized shortages, finds the nearest surplus, and automatically drafts a redistribution transfer ticket for a human dispatcher to approve.

---

## Technical Design & Architecture

We built this as a MVP (Minimum Viable Product) to have a flawless offline capable demo without the ghalabeh of cloud deployments.

* **The Brain:** We use **Ollama** running the lightweight **Llama 3.2** model locally. This ensures absolute data privacy and zero API costs. The LLM parses natural language commands (e.g., *"Transfer 50 insulin to Mafraq"*) and extracts the precise intent, locations, and quantities.
* **The Logic Engine (LangGraph & Python):** The backend is built with **FastAPI** and **LangGraph**. LangGraph manages the agent's workflow state: it checks inventory, queries the AI, drafts the logistical route, parks the process for human approval, and finally executes the transfer.
* **The Database:** To keep things agile, we bypassed SQL and used a mock `inventory.json` file. It acts as our simulated national database, updating in real-time as transfers are approved and shipments are received. (Bne7ki ino had limitaiton lal MVP w for real deployments we use mongodb or sm)
* **The Interface (Vanilla JS & HTML):** A high-performance, single-page application styled with **Tailwind CSS**. We chose a strict, functional "brutalist" design pattern tailored for enterprise logistics, featuring a live global dashboard and an AI dispatch terminal.

---


## Prerequisites (had elkom to try the project 3ala your laptops)

Before running the project, ensure you have the following installed:
* Python 3.10+
* Ollama (Download from [ollama.com](https://ollama.com/))

**Installing the AI Model:**  
Open your terminal/command prompt and run:
```bash
ollama run llama3.2
```

Wait for the download to finish and verify it says "success". You can type /bye to exit the prompt. The Ollama engine will keep running in your system background.
How to Run the System

You will need to open two separate terminal windows to run the backend and frontend simultaneously.

Step 1: Start the Backend (API & Agent)

Open Terminal 1, navigate to the root of the JDRN project, and set up the Python environment:
```bash
# 1. Navigate into the backend folder
cd backend

# 2. Create an isolated virtual environment
python -m venv .venv

# 3. Activate the environment
# On Windows:
.\.venv\Scripts\activate
# On Mac/Linux:
source .venv/bin/activate

# 4. Install required libraries
python -m pip install -r requirements.txt

# 5. Start the FastAPI server
uvicorn main:app --reload
```

Leave this terminal open. It will show a continuous feed of the AI's internal processing logs.

Step 2: Start the Frontend (User Interface)

Open Terminal 2, navigate to the root of the JDRN project, and start the local web server:

```bash
# 1. Navigate into the frontend folder
cd frontend

# 2. Start Python's built-in HTTP server
python -m http.server 3000

Step 3: Access the Dashboard

Open your web browser (Chrome, Edge, Safari) and navigate to:

http://localhost:3000
```
