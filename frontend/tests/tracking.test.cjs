const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function dashboard(stock, initialTime = '2026-10-03T10:00:00Z') {
    let now = new Date(initialTime).getTime();
    class Clock extends Date {
        static now() { return now; }
    }
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            value: '', innerHTML: '', textContent: '',
            classList: { toggle() {} }, addEventListener() {},
            replaceChildren() {}
        });
        return elements.get(id);
    };
    const context = vm.createContext({
        Date: Clock, Intl, console, window: {},
        document: { getElementById: element },
        Option: function(text, value) { this.text = text; this.value = value; },
        setInterval() {},
        fetch: async url => ({
            ok: true,
            json: async () => url.endsWith('/inventory') ? {
                clinics: { Amman_East: { location: 'Amman', type: 'branch', inventory: { Salbutamol_Inhaler: stock } } }
            } : { state: 'ready', message: 'Ready' }
        })
    });
    vm.runInContext(readFileSync(join(__dirname, '..', 'renderer.js'), 'utf8'), context);
    return {
        context, grid: element('clinics-grid'),
        render: () => vm.runInContext('loadDashboardData()', context),
        setTime: value => { now = new Date(value).getTime(); }
    };
}

test('dashboard displays stock update and demo ETA in Amman time', async () => {
    const view = dashboard({
        quantity: 17, in_transit: 28, last_updated: '2026-10-03T10:00:00Z',
        inbound_shipments: [{ from: 'Amman_Main', quantity: 28, eta: '2026-10-03T11:00:00Z' }]
    });
    await view.render();
    assert.match(view.grid.innerHTML, /Last updated:.*13:00 \(Amman\)/);
    assert.match(view.grid.innerHTML, /28 units from Amman Main/);
    assert.match(view.grid.innerHTML, /Demo ETA:.*14:00 \(Amman\).*in 60 min/);
    assert.match(view.grid.innerHTML, /INBOUND/);
    view.setTime('2026-10-03T10:30:00Z');
    await view.render();
    assert.match(view.grid.innerHTML, /in 30 min/);
    view.setTime('2026-10-03T11:01:00Z');
    await view.render();
    assert.match(view.grid.innerHTML, /Awaiting receipt.*demo ETA passed/);
    assert.match(view.grid.innerHTML, /Last updated:.*13:00 \(Amman\)/);
});

test('legacy stock and untracked inbound quantities have honest fallback labels', async () => {
    const view = dashboard({ quantity: 17, in_transit: 20 });
    await view.render();
    assert.match(view.grid.innerHTML, /Last updated: Not recorded/);
    assert.match(view.grid.innerHTML, /20 inbound units · ETA not recorded/);
    assert.equal(vm.runInContext("formatStockTime('invalid')", view.context), 'Not recorded');
    assert.match(vm.runInContext("renderInboundEstimates({in_transit: 2, inbound_shipments: [{quantity: 2, from: 'HQ', eta: 'invalid'}]})", view.context), /ETA not recorded/);
});

test('receipt hides estimates and multiple shipments retain separate estimates', async () => {
    const stock = {
        quantity: 17, in_transit: 35, last_updated: '2026-10-03T10:00:00Z',
        inbound_shipments: [
            { from: 'Amman_Main', quantity: 20, eta: '2026-10-03T11:00:00Z' },
            { from: '<Mafraq_HQ>', quantity: 10, eta: '2026-10-03T13:00:00Z' }
        ]
    };
    const view = dashboard(stock);
    await view.render();
    assert.match(view.grid.innerHTML, /in 60 min/);
    assert.match(view.grid.innerHTML, /in 180 min/);
    assert.match(view.grid.innerHTML, /5 inbound units · ETA not recorded/);
    assert.match(view.grid.innerHTML, /&lt;Mafraq HQ&gt;/);
    stock.quantity += stock.in_transit;
    stock.in_transit = 0;
    stock.inbound_shipments = [];
    await view.render();
    assert.doesNotMatch(view.grid.innerHTML, /Demo ETA|INBOUND|ETA not recorded/);
});
