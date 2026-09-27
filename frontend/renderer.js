const API_URL = "http://127.0.0.1:8000";
let currentSessionId = 'jdrn-' + Date.now();
let isProcessing = false; 

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
        const data = await res.json();
        
        grid.innerHTML = '';
        
        if (data.clinics) {
            for (const [key, clinic] of Object.entries(data.clinics)) {
                let inventoryHtml = '';
                for (const [drug, details] of Object.entries(clinic.inventory)) {
                    const isCritical = details.quantity <= 0;
                    const inTransitVal = details.in_transit || 0;
                    
                    const inTransitBadge = inTransitVal > 0 
                        ? `<button onclick="receiveShipment('${key}', '${drug}')" title="Sign for Delivery" class="bg-black text-white px-2 py-0.5 text-[10px] font-mono uppercase hover:bg-black/70 cursor-pointer ml-2">
                                +${inTransitVal} INBOUND
                           </button>` 
                        : '';
                        
                    inventoryHtml += `
                        <div class="flex justify-between items-center py-3 border-t border-black/10">
                            <span class="text-sm font-medium ${isCritical ? 'text-alert font-bold' : ''}">${drug.replace(/_/g, ' ')}</span>
                            <div class="flex items-center">
                                <span class="font-mono text-sm ${isCritical ? 'bg-alert text-white px-2 py-0.5' : ''}">${details.quantity}</span>
                                ${inTransitBadge}
                            </div>
                        </div>
                    `;
                }

                grid.innerHTML += `
                    <div class="bg-base brutalist-border p-6 flex flex-col justify-between">
                        <div class="mb-4">
                            <h3 class="font-bold text-xl uppercase tracking-tight">${clinic.location}</h3>
                            <p class="text-[10px] font-mono text-black/50 uppercase mt-1">NODE: ${key}</p>
                        </div>
                        <div class="mt-auto">${inventoryHtml}</div>
                    </div>
                `;
            }
        }
    } catch (err) {
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
    return (unsafe || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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

function appendMessage(role, content, treeData = null) {
    const id = "msg-" + Date.now() + Math.floor(Math.random() * 100);
    let html = '';
    
    if (role === 'user') {
        html = `
            <div class="border-t border-black/20 py-6 max-w-4xl">
                <div class="text-[10px] font-mono font-bold uppercase text-black/50 mb-2 tracking-widest">User Request</div>
                <div class="text-lg font-medium tracking-tight">> ${content}</div>
            </div>
        `;
    } else {
        const treeHtml = generateTreeHtml(treeData);
        html = `
            <div class="border-t border-black py-6 max-w-4xl bg-accent/20 px-6 my-4 brutalist-border">
                <div class="text-[10px] font-mono font-bold uppercase text-black/50 mb-2 tracking-widest">System Output</div>
                <div id="${id}-text" class="whitespace-pre-wrap text-sm leading-relaxed">${content}</div>
                ${treeHtml}
            </div>
        `;
    }
    
    chatContainer.insertAdjacentHTML('beforeend', html);
    chatContainer.scrollTop = chatContainer.scrollHeight;
    return id;
}

function appendActionCard(detail, treeData = null) {
    const treeHtml = generateTreeHtml(treeData);
    
    let deliverablesHtml = '';
    if (detail.deliverables && detail.deliverables.length > 0) {
        deliverablesHtml = detail.deliverables.map((d, i) => `
            <div class="font-mono text-sm border-b border-black/10 pb-2 mb-2 last:border-0 last:mb-0 last:pb-0">
                <span class="font-bold">ORD_${i + 1}:</span> ${escapeHtml(d.title)}
            </div>
        `).join('');
    } else {
        deliverablesHtml = '<div class="font-mono text-sm text-black/50">Awaiting transfer details...</div>';
    }

    const html = `
        <div class="max-w-4xl my-6 action-card brutalist-border bg-base p-0">
            <div class="bg-black text-white px-6 py-3">
                <h4 class="font-bold uppercase tracking-tight text-lg">Human Authorization Required</h4>
                <p class="text-xs font-mono opacity-70">${escapeHtml(detail.business_process)}</p>
            </div>
            <div class="p-6">
                <div class="bg-accent/50 p-4 brutalist-border mb-6">
                    ${deliverablesHtml}
                </div>
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
    
    const loaderId = appendMessage('agent', '<div class="flex items-center gap-3"><div class="loader"></div><span class="font-mono text-sm uppercase">Processing Request...</span></div>');
    
    try {
        const response = await fetch(`${API_URL}/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSessionId, message: text })
        });

        const data = await response.json();
        
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        
        if (data.is_parked) {
            appendActionCard(data.detail, data.tree_data);
        } else {
            appendMessage('agent', data.text, data.tree_data);
        }
    } catch (err) {
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        appendMessage('agent', 'SYSTEM ERROR: Connection to logistics API failed.');
    }
    
    isProcessing = false;
}

window.submitAction = async function(btn, actionType) {
    if (isProcessing) return;
    isProcessing = true;

    const card = btn.closest('.action-card');
    const interactionArea = card.querySelector('.interaction-area');
    interactionArea.innerHTML = `<span class="text-sm font-mono font-bold uppercase">Action Logged: [${actionType}]</span>`;

    const loaderId = appendMessage('agent', '<div class="flex items-center gap-3"><div class="loader"></div><span class="font-mono text-sm uppercase">Executing Dispatch...</span></div>');

    try {
        const response = await fetch(`${API_URL}/action`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: currentSessionId, decision: { decision: actionType } })
        });

        const data = await response.json();
        
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        
        if (data.text) {
            appendMessage('agent', data.text, data.tree_data);
        }
        
        loadDashboardData();
    } catch (err) {
        const loaderEl = document.getElementById(loaderId + '-text');
        if (loaderEl && loaderEl.parentElement) loaderEl.parentElement.remove();
        appendMessage('agent', 'SYSTEM ERROR: Execution sequence failed.');
    }
    
    isProcessing = false;
}

loadDashboardData();