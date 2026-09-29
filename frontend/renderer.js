const API_URL = "http://127.0.0.1:8000";
let isProcessing = false; 
let lastInventoryVersion = '';
let demoInventory = {};
let demoRunning = false;
let demoTimer = null;

function updateDemoMedicines() {
    const branch = document.getElementById('demo-branch').value;
    const drugSelect = document.getElementById('demo-drug');
    const previous = drugSelect.value;
    const medicines = Object.keys(demoInventory[branch]?.inventory || {});
    drugSelect.replaceChildren(...medicines.map(drug => new Option(drug.replace(/_/g, ' '), drug)));
    if (medicines.includes(previous)) drugSelect.value = previous;
    else if (medicines.includes('Salbutamol_Inhaler')) drugSelect.value = 'Salbutamol_Inhaler';
}

function updateDemoControls() {
    document.getElementById('demo-toggle').textContent = demoRunning ? 'Stop simulation' : 'Start simulation';
    document.getElementById('demo-branch').disabled = demoRunning;
    document.getElementById('demo-drug').disabled = demoRunning;
    document.getElementById('demo-restock').disabled = demoRunning;
}

function stopDemandSimulation(message) {
    demoRunning = false;
    clearTimeout(demoTimer);
    updateDemoControls();
    document.getElementById('demo-status').textContent = message;
}

async function consumeDemoStock() {
    if (!demoRunning) return;
    const clinicId = document.getElementById('demo-branch').value;
    const drug = document.getElementById('demo-drug').value;
    try {
        const response = await fetch(`${API_URL}/demo/consume`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({clinic_id: clinicId, drug})
        });
        if (!response.ok) throw new Error('Demand simulation request failed');
        const result = await response.json();
        await loadDashboardData();
        if (!demoRunning) return;
        if (result.quantity <= 30) {
            const status = result.quantity === 0 ? 'out of stock' : 'at risk';
            stopDemandSimulation(`${drug.replace(/_/g, ' ')} at ${clinicId.replace(/_/g, ' ')} is ${status} with ${result.quantity} units. Review its alert for a transfer plan.`);
        } else {
            document.getElementById('demo-status').textContent = `${clinicId.replace(/_/g, ' ')}: ${result.quantity} ${drug.replace(/_/g, ' ')} units remaining.`;
            demoTimer = setTimeout(consumeDemoStock, 2000);
        }
    } catch (error) {
        stopDemandSimulation('Simulation stopped: backend unavailable. Check the Python server.');
    }
}

function toggleDemandSimulation() {
    if (demoRunning) {
        stopDemandSimulation('Simulation stopped. Current stock is saved.');
        return;
    }
    if (!document.getElementById('demo-branch').value || !document.getElementById('demo-drug').value) return;
    demoRunning = true;
    updateDemoControls();
    document.getElementById('demo-status').textContent = 'Simulating demand...';
    consumeDemoStock();
}

async function restockDemoBranch() {
    const clinicId = document.getElementById('demo-branch').value;
    const drug = document.getElementById('demo-drug').value;
    if (demoRunning || !clinicId || !drug) return;
    try {
        const response = await fetch(`${API_URL}/demo/restock`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({clinic_id: clinicId, drug})
        });
        if (!response.ok) throw new Error('Restock request failed');
        const result = await response.json();
        document.getElementById('demo-status').textContent = `${clinicId.replace(/_/g, ' ')} reset to ${result.quantity} units of ${drug.replace(/_/g, ' ')} for another run.`;
        loadDashboardData();
    } catch (error) {
        document.getElementById('demo-status').textContent = 'Reset failed. Receive any inbound shipment first, then check the Python server.';
    }
}

function openShortageScan() {
    switchView('agent');
    promptInput.value = 'Scan for shortages in all branches';
    promptInput.focus();
}

function renderShortageAlerts(shortages) {
    const alertBox = document.getElementById('shortage-alerts');
    const badge = document.getElementById('nav-alert-count');
    badge.textContent = shortages.length;
    badge.classList.toggle('hidden', shortages.length === 0);
    alertBox.classList.toggle('hidden', shortages.length === 0);
    if (!shortages.length) {
        alertBox.innerHTML = '';
        return;
    }
    const stockouts = shortages.filter(item => item.status === 'out_of_stock').length;
    const atRisk = shortages.length - stockouts;
    const rows = shortages.map(item => `<li class="flex flex-wrap items-center justify-between gap-2 border-t border-black/10 py-2">
        <span><strong class="${item.status === 'out_of_stock' ? 'text-alert' : 'text-transit'}">${item.status === 'out_of_stock' ? 'OUT OF STOCK' : 'AT RISK'}</strong> · ${escapeHtml(item.clinic.replace(/_/g, ' '))}: ${escapeHtml(item.drug.replace(/_/g, ' '))} (${item.quantity} on hand${item.inTransit ? `, ${item.inTransit} inbound` : ''})</span>
        <button type="button" class="review-alert bg-black text-white px-3 py-1 font-mono text-xs font-bold uppercase" data-clinic="${escapeHtml(item.clinic)}" data-drug="${escapeHtml(item.drug)}">Review transfer</button>
    </li>`).join('');
    alertBox.innerHTML = `<div class="brutalist-border bg-white p-5">
        <div class="flex flex-wrap items-center justify-between gap-4">
            <div><h3 class="font-bold uppercase">${atRisk} at risk · ${stockouts} out of stock</h3>
                <p class="text-xs font-mono mt-1">Review a medicine before dispatch. Inbound stock is counted when planning.</p></div>
            <button type="button" onclick="openShortageScan()" class="brutalist-border px-4 py-2 font-mono text-xs font-bold uppercase">Scan all branches</button>
        </div><ul class="mt-3 text-sm font-mono">${rows}</ul></div>`;
}

document.getElementById('shortage-alerts').addEventListener('click', event => {
    const button = event.target.closest('.review-alert');
    if (button) reviewAlert(button.dataset.clinic, button.dataset.drug);
});

async function reviewAlert(clinicId, drug) {
    if (isProcessing) return;
    isProcessing = true;
    switchView('agent');
    appendMessage('user', `Review ${drug.replace(/_/g, ' ')} at ${clinicId.replace(/_/g, ' ')}`);
    const loaderId = appendMessage('agent', '<div class="loader"></div> Checking live inventory...', null, true);
    try {
        const response = await fetch(`${API_URL}/scan`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({clinic_id: clinicId, drug})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Focused scan failed');
        document.getElementById(loaderId + '-text')?.parentElement?.remove();
        if (data.is_parked) appendActionCard(data.detail, data.session_id, data.tree_data);
        else appendMessage('agent', data.text || 'No transfer needed.', data.tree_data);
    } catch (error) {
        document.getElementById(loaderId + '-text')?.parentElement?.remove();
        appendMessage('agent', `Scan failed: ${error.message}`);
    }
    isProcessing = false;
}

function switchView(viewName) {
    const dashView = document.getElementById('view-dashboard');
    const agentView = document.getElementById('view-agent');
    const navDash = document.getElementById('nav-dashboard');
    const navAgent = document.getElementById('nav-agent');

    const activeNav = ['nav-active', 'brutalist-border'];
    const inactiveNav = ['nav-inactive', 'border-transparent'];

    if (viewName === 'dashboard') {
        dashView.classList.remove('hidden-view');
        agentView.classList.add('hidden-view');
        navDash.classList.add(...activeNav);
        navDash.classList.remove(...inactiveNav);
        navAgent.classList.remove(...activeNav);
        navAgent.classList.add(...inactiveNav);
        loadDashboardData();
    } else {
        dashView.classList.add('hidden-view');
        agentView.classList.remove('hidden-view');
        navAgent.classList.add(...activeNav);
        navAgent.classList.remove(...inactiveNav);
        navDash.classList.remove(...activeNav);
        navDash.classList.add(...inactiveNav);
    }
}

async function loadDashboardData() {
    const grid = document.getElementById('clinics-grid');
    if (!grid) return;
    
    try {
        const res = await fetch(`${API_URL}/inventory`);
        if (!res.ok) throw new Error('Inventory API error');
        const data = await res.json();
        if (!data.clinics) throw new Error('Inventory unavailable');
        document.getElementById('backend-status').textContent = 'Inventory online';
        demoInventory = data.clinics;
        const branchSelect = document.getElementById('demo-branch');
        const selectedBranch = branchSelect.value;
        const branches = Object.entries(data.clinics).filter(([, clinic]) => clinic.type === 'branch');
        branchSelect.replaceChildren(...branches.map(([id]) => new Option(id.replace(/_/g, ' '), id)));
        branchSelect.value = branches.some(([id]) => id === selectedBranch) ? selectedBranch : (branches.some(([id]) => id === 'Amman_East') ? 'Amman_East' : branches[0]?.[0] || '');
        updateDemoMedicines();
        const version = JSON.stringify(data.clinics);
        if (version === lastInventoryVersion) return;
        lastInventoryVersion = version;
        const groups = {};
        const shortages = [];
        for (const [key, clinic] of Object.entries(data.clinics)) {
            const region = clinic.location || 'Other';
            (groups[region] ||= []).push([key, clinic]);
            for (const [drug, details] of Object.entries(clinic.inventory || {})) {
                if (clinic.type === 'branch' && details.quantity <= 30) shortages.push({
                    clinic: key, drug, quantity: details.quantity,
                    inTransit: details.in_transit || 0,
                    status: details.quantity <= 0 ? 'out_of_stock' : 'at_risk'
                });
            }
        }
        renderShortageAlerts(shortages);
        grid.innerHTML = Object.entries(groups).map(([region, sites]) => {
            sites.sort((a, b) => (a[1].type === 'hq' ? -1 : 1) - (b[1].type === 'hq' ? -1 : 1));
            const cards = sites.map(([key, clinic]) => {
                let inventoryHtml = '';
                for (const [drug, details] of Object.entries(clinic.inventory || {})) {
                    const isCritical = details.quantity <= 0 && clinic.type === 'branch';
                    const isAtRisk = details.quantity > 0 && details.quantity <= 30 && clinic.type === 'branch';
                    const inTransitVal = details.in_transit || 0;
                    
                    const inTransitBadge = inTransitVal > 0 
                        ? `<button onclick="receiveShipment('${key}', '${drug}')" title="Sign for Delivery" class="bg-black text-white px-2 py-0.5 text-[10px] font-mono uppercase hover:bg-black/70 cursor-pointer ml-2">
                                +${inTransitVal} INBOUND
                           </button>` 
                        : '';
                        
                    inventoryHtml += `
                        <div class="flex justify-between items-center py-3 border-t border-black/10">
                            <span class="text-sm font-medium ${isCritical ? 'text-alert font-bold' : isAtRisk ? 'text-transit font-bold' : ''}">${escapeHtml(drug.replace(/_/g, ' '))}</span>
                            <div class="flex items-center">
                                <span class="font-mono text-sm ${isCritical ? 'bg-alert text-white px-2 py-0.5' : isAtRisk ? 'bg-transit text-white px-2 py-0.5' : ''}">${details.quantity}</span>
                                ${inTransitBadge}
                            </div>
                        </div>
                    `;
                }

                return `
                    <div class="bg-base brutalist-border p-6 flex flex-col justify-between">
                        <div class="mb-4">
                            <h3 class="font-bold text-lg uppercase tracking-tight">${escapeHtml(key.replace(/_/g, ' '))}</h3>
                            <p class="text-[10px] font-mono text-black/50 uppercase mt-1">${clinic.type === 'hq' ? 'MAIN STORAGE HQ' : 'BRANCH'}</p>
                        </div>
                        <div class="mt-auto">${inventoryHtml}</div>
                    </div>
                `;
            }).join('');
            return `<section><h3 class="text-xl font-bold uppercase border-b border-black pb-2 mb-4">${escapeHtml(region)} · 1 HQ + ${sites.length - 1} branches</h3><div class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-4 gap-4">${cards}</div></section>`;
        }).join('');
    } catch (err) {
        lastInventoryVersion = '';
        document.getElementById('backend-status').textContent = 'Backend offline';
        grid.innerHTML = `<div class="col-span-full font-mono text-alert uppercase border border-alert p-6 text-center">Connection Error: Uvicorn Offline</div>`;
    }
}

window.receiveShipment = async function(clinicId, drug) {
    try {
        const response = await fetch(`${API_URL}/receive`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ clinic_id: clinicId, drug: drug })
        });
        if (response.ok) loadDashboardData();
    } catch (err) { console.error("Receipt failed:", err); }
}

function escapeHtml(unsafe) {
    return String(unsafe || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function generateTreeHtml(treeData) {
    if (!treeData || treeData.length === 0) return '';
    const id = "tree-" + Date.now() + Math.floor(Math.random() * 1000);
    
    let html = `
        <div class="mt-6 border-t border-black pt-4 w-full">
            <button type="button" onclick="document.getElementById('${id}').classList.toggle('hidden')" class="text-xs font-mono font-bold uppercase hover:bg-black hover:text-white px-2 py-1 transition-colors">
                [+] View System Trace
            </button>
            <div id="${id}" class="hidden mt-4">
                <div class="border-l-2 border-black space-y-4 pl-4">
    `;
    
    treeData.forEach(step => {
        let colorClass = "bg-black";
        if (step.status === "error") colorClass = "bg-alert";
        if (step.status === "warning") colorClass = "bg-transit";
        
        html += `
            <div class="relative">
                <div class="absolute -left-[21px] top-1.5 w-2 h-2 ${colorClass}"></div>
                <div class="text-[10px] font-mono text-black/50 uppercase tracking-widest">${escapeHtml(step.node)}</div>
                <div class="text-sm font-bold uppercase tracking-tight">${escapeHtml(step.title)}</div>
                <div class="text-xs font-mono bg-accent/50 p-3 mt-1 brutalist-border">${escapeHtml(step.detail)}</div>
            </div>
        `;
    });
    
    html += `</div></div></div>`;
    return html;
}

const chatContainer = document.getElementById('chat-container');
const promptInput = document.getElementById('prompt-input');

function appendMessage(role, content, treeData = null, trustedHtml = false) {
    const id = "msg-" + Date.now() + Math.floor(Math.random() * 100);
    let html = '';
    
    if (role === 'user') {
        html = `
            <div class="border-t border-black/20 py-6 max-w-4xl">
                <div class="text-[10px] font-mono font-bold uppercase text-black/50 mb-2 tracking-widest">User Request</div>
                <div class="text-lg font-medium tracking-tight">> ${escapeHtml(content)}</div>
            </div>
        `;
    } else {
        const treeHtml = generateTreeHtml(treeData);
        html = `
            <div class="border-t border-black py-6 max-w-4xl bg-accent/20 px-6 my-4 brutalist-border">
                <div class="text-[10px] font-mono font-bold uppercase text-black/50 mb-2 tracking-widest">System Output</div>
                <div id="${id}-text" class="whitespace-pre-wrap text-sm leading-relaxed">${trustedHtml ? content : escapeHtml(content)}</div>
                ${treeHtml}
            </div>
        `;
    }
    
    chatContainer.insertAdjacentHTML('beforeend', html);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return id;
}

function appendActionCard(detail, sessionId, treeData = null) {
    const treeHtml = generateTreeHtml(treeData);
    
    let deliverablesHtml = '';
    if (detail.deliverables && detail.deliverables.length > 0) {
        deliverablesHtml = detail.deliverables.map((d, i) => `
            <div class="font-mono text-sm border-b border-black/10 pb-2 mb-2 last:border-0 last:mb-0 last:pb-0">
                <span class="font-bold">ORD_${i + 1}:</span> ${escapeHtml(d.title)}
                <div class="text-xs mt-1">${escapeHtml(d.detail)}</div>
                <div class="text-xs text-black/60 mt-1">Why: ${escapeHtml(d.reason)}</div>
            </div>
        `).join('');
    } else {
        deliverablesHtml = '<div class="font-mono text-sm text-black/50">Awaiting transfer details...</div>';
    }

    const html = `
        <div class="max-w-4xl my-6 action-card brutalist-border bg-base p-0" data-session-id="${escapeHtml(sessionId)}">
            <div class="bg-black text-white px-6 py-3">
                <h4 class="font-bold uppercase tracking-tight text-lg">Human Authorization Required</h4>
                <p class="text-xs font-mono opacity-70">${escapeHtml(detail.business_process)}</p>
            </div>
            <div class="p-6">
                <div class="bg-accent/50 p-4 brutalist-border mb-6">
                    ${deliverablesHtml}
                </div>
                ${detail.unresolved?.length ? `<p class="text-xs font-mono text-alert mb-4">${escapeHtml(detail.unresolved.join(' '))}</p>` : ''}
                <div class="flex gap-4 interaction-area border-t border-black pt-6">
                    <button type="button" onclick="submitAction(this, 'approve')" class="px-6 py-3 bg-black text-white font-mono text-sm font-bold uppercase hover:bg-black/80 transition-colors">
                        Approve Transfer
                    </button>
                    <button type="button" onclick="submitAction(this, 'reject')" class="px-6 py-3 bg-white text-black brutalist-border font-mono text-sm font-bold uppercase hover:bg-accent transition-colors">
                        Reject
                    </button>
                </div>
                ${treeHtml}
            </div>
        </div>
    `;
    chatContainer.insertAdjacentHTML('beforeend', html);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function sendMessage() {
    if (isProcessing) return;
    
    const text = promptInput.value.trim();
    if (!text) return;

    appendMessage('user', text);
    promptInput.value = "";
    isProcessing = true;
    
    const loaderId = appendMessage('agent', '<div class="flex items-center gap-3"><div class="loader"></div><span class="font-mono text-sm uppercase">Processing Request...</span></div>', null, true);
    
    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Terminal request failed');
        
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        
        if (data.is_parked) {
            appendActionCard(data.detail, data.session_id, data.tree_data);
        } else {
            appendMessage('agent', data.text, data.tree_data);
        }
    } catch (err) {
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        appendMessage('agent', `Terminal request failed: ${err.message}`);
    }
    
    isProcessing = false;
}

window.submitAction = async function(btn, actionType) {
    if (isProcessing) return;
    isProcessing = true;

    const card = btn.closest('.action-card');
    const interactionArea = card.querySelector('.interaction-area');
    const previousButtons = interactionArea.innerHTML;
    interactionArea.innerHTML = `<span class="text-sm font-mono font-bold uppercase">Action Logged: [${actionType}]</span>`;

    const loaderId = appendMessage('agent', '<div class="flex items-center gap-3"><div class="loader"></div><span class="font-mono text-sm uppercase">Executing Dispatch...</span></div>', null, true);

    try {
        const response = await fetch(`${API_URL}/action`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: card.dataset.sessionId, decision: { decision: actionType } })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Decision failed');
        
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        
        if (data.text) {
            appendMessage('agent', data.text, data.tree_data);
        }
        
        loadDashboardData();
    } catch (err) {
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        interactionArea.innerHTML = previousButtons;
        appendMessage('agent', `Decision failed: ${err.message}`);
    }
    
    isProcessing = false;
}

async function refreshOllamaStatus() {
    const label = document.getElementById('ollama-status');
    try {
        const response = await fetch(`${API_URL}/ollama/status`);
        if (!response.ok) throw new Error('Backend unavailable');
        const result = await response.json();
        label.textContent = result.state === 'ready' ? 'Ollama ready' :
            result.state === 'model_missing' ? 'Ollama model missing' : 'Ollama offline';
        label.title = result.message;
    } catch (error) {
        label.textContent = 'Ollama status unavailable';
        label.title = 'Start the JDRN backend to check Ollama.';
    }
}

loadDashboardData();
refreshOllamaStatus();
setInterval(loadDashboardData, 5000);
setInterval(refreshOllamaStatus, 15000);
