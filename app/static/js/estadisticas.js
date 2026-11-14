// =========================================================
// MÓDULO ESTADÍSTICAS DE VENTAS & GESTIÓN DE EQUIPO STAFF
// =========================================================
let estPeriodoActual = '30d';

function setEstPeriodo(periodo) {
    estPeriodoActual = periodo || '30d';
    document.querySelectorAll('.est-periodo-btn').forEach(btn => {
        const isActive = btn.getAttribute('data-est-periodo') === estPeriodoActual;
        btn.className = isActive
            ? 'est-periodo-btn px-3 py-1.5 rounded-lg bg-emerald-500 text-slate-950 font-bold transition'
            : 'est-periodo-btn px-3 py-1.5 rounded-lg text-slate-300 hover:text-white transition';
    });
    cargarEstadisticasComercio();
}

async function cargarEstadisticasComercio() {
    if (!activeTenantId || !isComercioAdminOrSuper()) return;

    const sucursalId = document.getElementById('filtroSucursalSelect')?.value || '';
    const params = new URLSearchParams({ periodo: estPeriodoActual });
    if (sucursalId) params.set('sucursal_id', sucursalId);

    const kpiGrid = document.getElementById('estKpiGrid');
    if (kpiGrid) {
        kpiGrid.innerHTML = `<div class="col-span-full glass rounded-2xl p-6 text-center text-slate-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Calculando estadísticas de ventas...</div>`;
    }

    try {
        const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/estadisticas?${params.toString()}`);
        renderEstadisticasComercio(data);
    } catch (err) {
        showToast('Error cargando estadísticas: ' + err.message, true);
    }
}

async function descargarEstadisticasCSV() {
    if (!activeTenantId || !isComercioAdminOrSuper()) return;
    const sucursalId = document.getElementById('filtroSucursalSelect')?.value || '';
    const params = new URLSearchParams({ periodo: estPeriodoActual });
    if (sucursalId) params.set('sucursal_id', sucursalId);

    try {
        const res = await fetch(`/api/v1/panel/comercio/${activeTenantId}/estadisticas/exportar.csv?${params.toString()}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        if (!res.ok) {
            let detail = 'Error al exportar CSV';
            try { const d = await res.json(); detail = d.detail || detail; } catch (_) {}
            throw new Error(detail);
        }
        const blob = await res.blob();
        const slug = (currentComercioData?.comercio?.slug || `comercio_${activeTenantId}`).replace(/[^a-zA-Z0-9_-]/g, '_');
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `estadisticas_${slug}_${estPeriodoActual}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
        showToast('Archivo CSV descargado listo para abrir en Excel.');
    } catch (err) {
        showToast(err.message, true);
    }
}
window.descargarEstadisticasCSV = descargarEstadisticasCSV;

function imprimirReporteEstadisticas() {
    window.print();
}
window.imprimirReporteEstadisticas = imprimirReporteEstadisticas;

function renderEstadisticasComercio(data) {
    const c = data.comercio || {};
    const sym = c.simbolo_moneda || 'C$';
    const r = data.resumen || {};

    const perBadge = document.getElementById('estPeriodoBadge');
    if (perBadge && data.periodo) perBadge.textContent = data.periodo.etiqueta || estPeriodoActual;

    const kpiGrid = document.getElementById('estKpiGrid');
    if (kpiGrid) {
        kpiGrid.innerHTML = `
            <div class="glass rounded-2xl p-4 space-y-1">
                <div class="flex items-center justify-between text-xs text-slate-400">
                    <span>Ventas Totales</span>
                    <i class="fa-solid fa-sack-dollar text-emerald-400"></i>
                </div>
                <p class="text-2xl font-bold text-emerald-400">${sym}${Number(r.ventas_totales || 0).toLocaleString('es-NI', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
                <p class="text-[11px] text-slate-400">Productos: ${sym}${Number(r.subtotal_productos || 0).toFixed(2)} · Envíos: ${sym}${Number(r.ingresos_envio || 0).toFixed(2)}</p>
            </div>
            <div class="glass rounded-2xl p-4 space-y-1">
                <div class="flex items-center justify-between text-xs text-slate-400">
                    <span>Pedidos Válidos</span>
                    <i class="fa-solid fa-bag-shopping text-indigo-400"></i>
                </div>
                <p class="text-2xl font-bold text-white">${r.pedidos_validos || 0}</p>
                <p class="text-[11px] text-slate-400">Cancelados: <span class="text-rose-400 font-semibold">${r.pedidos_cancelados || 0}</span> · Total: ${r.total_pedidos || 0}</p>
            </div>
            <div class="glass rounded-2xl p-4 space-y-1">
                <div class="flex items-center justify-between text-xs text-slate-400">
                    <span>Ticket Promedio</span>
                    <i class="fa-solid fa-receipt text-amber-400"></i>
                </div>
                <p class="text-2xl font-bold text-amber-300">${sym}${Number(r.ticket_promedio || 0).toFixed(2)}</p>
                <p class="text-[11px] text-slate-400">Promedio por pedido válido</p>
            </div>
            <div class="glass rounded-2xl p-4 space-y-1">
                <div class="flex items-center justify-between text-xs text-slate-400">
                    <span>Cobrado vs Pendiente</span>
                    <i class="fa-solid fa-circle-check text-sky-400"></i>
                </div>
                <p class="text-2xl font-bold text-sky-300">${sym}${Number(r.ventas_cobradas || 0).toFixed(2)}</p>
                <p class="text-[11px] text-slate-400">${r.pedidos_pagados || 0} pagado(s) · Por cobrar: ${sym}${Number(r.ventas_por_cobrar || 0).toFixed(2)}</p>
            </div>
        `;
    }

    // Tendencia diaria
    const chartEl = document.getElementById('estTendenciaChart');
    if (chartEl) {
        const dias = data.tendencia_diaria || [];
        if (dias.length === 0) {
            chartEl.innerHTML = `<div class="text-center py-8 text-slate-500 text-xs">No hay ventas registradas en el período seleccionado.</div>`;
        } else {
            const maxVal = Math.max(...dias.map(d => Number(d.total || 0)), 1);
            chartEl.innerHTML = dias.map(d => {
                const pct = Math.max(4, Math.round((Number(d.total || 0) / maxVal) * 100));
                return `
                    <div class="grid grid-cols-12 items-center gap-2 text-xs">
                        <span class="col-span-2 font-mono text-slate-400">${escapeHtml(d.etiqueta)}</span>
                        <div class="col-span-6 h-3 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
                            <div class="h-full bg-gradient-to-r from-emerald-500 to-indigo-500 rounded-full" style="width:${pct}%"></div>
                        </div>
                        <span class="col-span-2 text-right text-slate-400 text-[11px]">${d.pedidos} ped.</span>
                        <span class="col-span-2 text-right font-bold text-emerald-400">${sym}${Number(d.total || 0).toFixed(0)}</span>
                    </div>
                `;
            }).join('');
        }
    }

    // Métodos de pago
    const metEl = document.getElementById('estMetodosPagoList');
    if (metEl) {
        const metodos = data.por_metodo_pago || [];
        metEl.innerHTML = metodos.length === 0
            ? `<p class="text-xs text-slate-500">Sin datos en este período.</p>`
            : metodos.map(m => `
                <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-900/90 border border-slate-800 text-xs">
                    <div>
                        <span class="font-bold text-white">${escapeHtml(m.metodo)}</span>
                        <span class="text-slate-400 ml-1.5">(${m.pedidos} ped.)</span>
                    </div>
                    <span class="font-bold text-emerald-400">${sym}${Number(m.total || 0).toFixed(2)}</span>
                </div>
            `).join('');
    }

    // Modalidad de entrega
    const entEl = document.getElementById('estTipoEntregaList');
    if (entEl) {
        const entregas = data.por_tipo_entrega || [];
        entEl.innerHTML = entregas.length === 0
            ? `<p class="text-xs text-slate-500">Sin datos en este período.</p>`
            : entregas.map(e => `
                <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-900/90 border border-slate-800 text-xs">
                    <div>
                        <span class="font-bold text-white">${escapeHtml(e.tipo)}</span>
                        <span class="text-slate-400 ml-1.5">(${e.pedidos} ped.)</span>
                    </div>
                    <span class="font-bold text-sky-400">${sym}${Number(e.total || 0).toFixed(2)}</span>
                </div>
            `).join('');
    }

    // Top productos
    const topEl = document.getElementById('estTopProductosList');
    if (topEl) {
        const tops = data.top_productos || [];
        topEl.innerHTML = tops.length === 0
            ? `<p class="text-xs text-slate-500">Sin productos vendidos en este período.</p>`
            : tops.map((p, idx) => `
                <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-900/90 border border-slate-800 text-xs">
                    <div class="flex items-center gap-2.5 min-w-0">
                        <span class="w-6 h-6 rounded-lg bg-amber-500/15 text-amber-300 font-bold flex items-center justify-center shrink-0">${idx + 1}</span>
                        <span class="font-semibold text-white truncate">${escapeHtml(p.nombre)}</span>
                    </div>
                    <div class="flex items-center gap-3 shrink-0">
                        <span class="text-slate-300 font-mono">${p.cantidad} uds.</span>
                        <span class="font-bold text-emerald-400">${sym}${Number(p.ingresos || 0).toFixed(2)}</span>
                    </div>
                </div>
            `).join('');
    }

    // Por sucursal y estado
    const sucEl = document.getElementById('estSucursalesList');
    if (sucEl) {
        const sucs = data.por_sucursal || [];
        sucEl.innerHTML = sucs.length === 0
            ? `<p class="text-xs text-slate-500">Sin sucursales con ventas en el período.</p>`
            : sucs.map(s => `
                <div class="flex items-center justify-between p-2.5 rounded-xl bg-slate-900/90 border border-slate-800 text-xs">
                    <div>
                        <span class="font-bold text-white">${escapeHtml(s.sucursal_nombre)}</span>
                        <span class="text-slate-400 ml-1.5">(${s.pedidos} ped.)</span>
                    </div>
                    <span class="font-bold text-indigo-300">${sym}${Number(s.total || 0).toFixed(2)}</span>
                </div>
            `).join('');
    }

    const estList = document.getElementById('estEstadosList');
    if (estList) {
        const estados = data.por_estado || [];
        estList.innerHTML = estados.map(st => `
            <span class="text-[11px] px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-slate-200">
                <strong>${escapeHtml(st.estado)}</strong>: ${st.pedidos}
            </span>
        `).join('');
    }
}

// =========================================================
// GESTIÓN DE EQUIPO STAFF POR EL DUEÑO (COMERCIO_ADMIN)
// =========================================================
async function openModalEquipoComercio() {
    if (!activeTenantId || !isComercioAdminOrSuper()) return;
    document.getElementById('eqUsuariosList').innerHTML = `<div class="text-center text-slate-400 text-xs py-4"><i class="fa-solid fa-spinner fa-spin mr-1.5"></i>Cargando equipo...</div>`;
    document.getElementById('modalEquipoComercio')?.classList.remove('hidden');

    try {
        const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/usuarios`);
        const sel = document.getElementById('eqNuevaSucursal');
        if (sel) {
            sel.innerHTML = `<option value="">Todas las sucursales</option>` + (data.sucursales || []).map(s =>
                `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)}</option>`
            ).join('');
        }
        renderEquipoComercioList(data.usuarios || []);
    } catch (err) {
        showToast(err.message, true);
    }
}

function closeModalEquipoComercio() {
    document.getElementById('modalEquipoComercio')?.classList.add('hidden');
}

function renderEquipoComercioList(usuarios) {
    const list = document.getElementById('eqUsuariosList');
    if (!list) return;
    if (usuarios.length === 0) {
        list.innerHTML = `<div class="text-center text-slate-500 text-xs py-4">No hay usuarios en este comercio.</div>`;
        return;
    }

    list.innerHTML = usuarios.map(u => {
        const isAdmin = u.rol === 'COMERCIO_ADMIN';
        return `
            <div class="bg-slate-950 border border-slate-800 rounded-xl p-3 flex flex-wrap items-center justify-between gap-3">
                <div class="space-y-0.5">
                    <div class="flex items-center gap-2 flex-wrap">
                        <span class="text-sm font-bold text-white">${escapeHtml(u.nombre_completo)}</span>
                        <span class="text-[10px] font-semibold px-2 py-0.5 rounded ${isAdmin ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30' : 'bg-sky-500/15 text-sky-300 border border-sky-500/30'}">${isAdmin ? 'ADMIN (DUEÑO)' : 'STAFF (OPERATIVO)'}</span>
                        <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${u.activo ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}">${u.activo ? 'ACTIVO' : 'PAUSADO'}</span>
                    </div>
                    <p class="text-xs text-slate-400">${escapeHtml(u.email)} · ${escapeHtml(u.sucursal_nombre || 'Todas las sucursales')}</p>
                </div>
                ${isAdmin ? `<span class="text-[11px] text-slate-500 italic">Propietario</span>` : `
                    <div class="flex items-center gap-2">
                        <button type="button" onclick="resetStaffComercioPass(${u.id})" class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-2.5 py-1.5 rounded-lg flex items-center gap-1 transition">
                            <i class="fa-solid fa-key"></i><span>Clave</span>
                        </button>
                        <button type="button" onclick="toggleStaffComercioActivo(${u.id}, ${!u.activo})" class="${u.activo ? 'bg-rose-500/15 hover:bg-rose-500/25 text-rose-300' : 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300'} text-xs font-semibold px-2.5 py-1.5 rounded-lg transition">
                            ${u.activo ? 'Pausar' : 'Activar'}
                        </button>
                    </div>
                `}
            </div>
        `;
    }).join('');
}

async function handleCrearStaffComercio(e) {
    e.preventDefault();
    if (!activeTenantId) return;
    const sucVal = document.getElementById('eqNuevaSucursal')?.value;
    try {
        await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/usuarios`, {
            method: 'POST',
            body: {
                nombre_completo: document.getElementById('eqNuevoNombre').value.trim(),
                email: document.getElementById('eqNuevoEmail').value.trim(),
                password: document.getElementById('eqNuevoPass').value,
                sucursal_id: sucVal ? Number(sucVal) : null
            }
        });
        document.getElementById('eqNuevoNombre').value = '';
        document.getElementById('eqNuevoEmail').value = '';
        showToast('Personal Staff agregado correctamente.');
        await openModalEquipoComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function toggleStaffComercioActivo(usuarioId, nuevoActivo) {
    if (!activeTenantId) return;
    try {
        await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/usuarios/${usuarioId}`, {
            method: 'PATCH',
            body: { activo: nuevoActivo }
        });
        showToast(nuevoActivo ? 'Usuario Staff activado.' : 'Usuario Staff pausado.');
        await openModalEquipoComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}

async function resetStaffComercioPass(usuarioId) {
    if (!activeTenantId) return;
    const nueva = window.prompt('Nueva contraseña para el personal Staff (mín. 6 caracteres):', 'Staff#2026!');
    if (!nueva) return;
    try {
        await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/usuarios/${usuarioId}`, {
            method: 'PATCH',
            body: { nueva_password: nueva }
        });
        showToast('Contraseña actualizada.');
        await openModalEquipoComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}

