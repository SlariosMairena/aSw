// ============================================================================
// PANEL MÓVIL DE REPARTIDOR (FLOTA SAAS COMPARTIDA / PROPIO + PIN 4 DÍGITOS)
// ============================================================================
let repRefreshTimer = null;

async function loadRepartidorPanel() {
    const enCaminoBox = document.getElementById('repEnCaminoList');
    const dispBox = document.getElementById('repDisponiblesList');
    const entBox = document.getElementById('repEntregadosList');
    if (!enCaminoBox || !dispBox) return;

    try {
        const data = await apiFetch('/api/v1/delivery/panel');
        const rep = data.repartidor || {};
        document.getElementById('repNombreTitle').textContent = rep.nombre || 'Repartidor';
        document.getElementById('repSubInfo').textContent =
            `${rep.es_flota_saas ? 'Flota Compartida SaaS' : 'Repartidor Propio'} · ${rep.vehiculo || 'Moto'}${rep.telefono ? ' · ' + rep.telefono : ''}`;

        const cb = document.getElementById('repDisponibleToggle');
        if (cb) cb.checked = rep.disponible !== false;
        const lbl = document.getElementById('repDisponibleLabel');
        if (lbl) lbl.textContent = rep.disponible !== false ? 'Disponible (recibiendo avisos WhatsApp)' : 'En pausa (no disponible)';

        const gan = document.getElementById('repGananciaHoy');
        if (gan) gan.textContent = `C$${Number(data.ganancia_envios_hoy || 0).toFixed(2)}`;

        const enCamino = data.en_camino || [];
        const disponibles = data.disponibles || [];
        const entregados = data.entregados_hoy || [];

        document.getElementById('repCountEnCamino').textContent = String(enCamino.length);
        document.getElementById('repCountDisponibles').textContent = String(disponibles.length);
        document.getElementById('repCountEntregados').textContent = String(entregados.length);

        enCaminoBox.innerHTML = enCamino.length
            ? enCamino.map(renderRepartidorEnCaminoCard).join('')
            : `<div class="glass rounded-2xl p-5 text-center text-xs text-[#5C6B62]">No llevas entregas en camino en este momento.</div>`;

        dispBox.innerHTML = disponibles.length
            ? disponibles.map(renderRepartidorDisponibleCard).join('')
            : `<div class="glass rounded-2xl p-5 text-center text-xs text-[#5C6B62]">No hay pedidos listos para recoger ahora mismo. Recibirás un aviso por WhatsApp apenas salga uno.</div>`;

        if (entBox) {
            entBox.innerHTML = entregados.length
                ? entregados.map(it => `
                    <div class="p-3 rounded-xl bg-[#F5EFE4] border border-[#DFD5C3] flex items-center justify-between gap-2 text-xs">
                        <div class="min-w-0">
                            <div class="font-bold text-[#1A241E] truncate">#${escapeHtml(it.folio)} · ${escapeHtml(it.comercio_nombre)}</div>
                            <div class="text-[11px] text-[#5C6B62] truncate">${escapeHtml(it.cliente_nombre)} — ${escapeHtml(it.direccion_entrega)}</div>
                        </div>
                        <span class="font-extrabold text-[#1B4332] shrink-0">+${escapeHtml(it.simbolo_moneda)}${Number(it.costo_envio || 0).toFixed(2)}</span>
                    </div>
                `).join('')
                : `<div class="text-xs text-[#5C6B62] text-center py-2">Aún no has completado entregas hoy.</div>`;
        }

        if (!repRefreshTimer) {
            repRefreshTimer = setInterval(() => {
                const sec = document.getElementById('repartidorSection');
                if (sec && !sec.classList.contains('hidden')) {
                    loadRepartidorPanel();
                }
            }, 15000);
        }
    } catch (err) {
        showToast(err.message, true);
    }
}
window.loadRepartidorPanel = loadRepartidorPanel;

function renderRepartidorDisponibleCard(it) {
    const pagoBadge = it.estado_pago === 'PAGADO'
        ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#E4EFE9] text-[#1B4332]">PAGADO ONLINE</span>`
        : `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#FEF3C7] text-[#92400E]">COBRAR ${escapeHtml(it.simbolo_moneda)}${Number(it.total).toFixed(2)}</span>`;

    return `
        <div class="glass rounded-2xl p-4 space-y-2.5 text-xs">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                    <div class="font-extrabold text-sm text-[#1A241E]">#${escapeHtml(it.folio)} · ${escapeHtml(it.comercio_nombre)}</div>
                    <div class="text-[11px] text-[#5C6B62]">Recoger en: <strong class="text-[#1A241E]">${escapeHtml(it.sucursal_nombre)}</strong></div>
                </div>
                ${pagoBadge}
            </div>
            <div class="p-2.5 rounded-xl bg-[#F5EFE4] border border-[#DFD5C3] space-y-1">
                <div><i class="fa-solid fa-location-dot text-[#D6402B] mr-1"></i><strong>Entregar en:</strong> ${escapeHtml(it.direccion_entrega)}</div>
                <div class="text-[11px] text-[#5C6B62]">${escapeHtml((it.items_resumen || []).join(', '))}</div>
            </div>
            <div class="flex items-center justify-between gap-2 pt-1">
                <span class="font-extrabold text-[#1B4332]">Envío: ${escapeHtml(it.simbolo_moneda)}${Number(it.costo_envio || 0).toFixed(2)}</span>
                <button type="button" onclick="tomarPedidoRepartidor(${it.pedido_id})"
                    class="px-4 py-2 rounded-xl bg-[#D6402B] hover:bg-[#B8321F] text-white keep-white font-bold text-xs shadow-sm transition flex items-center gap-1.5">
                    <i class="fa-solid fa-motorcycle"></i> <span>Tomar Pedido / Voy en Camino</span>
                </button>
            </div>
        </div>
    `;
}

function renderRepartidorEnCaminoCard(it) {
    const pagoInfo = it.estado_pago === 'PAGADO'
        ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#E4EFE9] text-[#1B4332]"><i class="fa-solid fa-circle-check mr-1"></i>PAGADO ONLINE (No cobrar)</span>`
        : `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[#FEF3C7] text-[#92400E]">COBRAR ${escapeHtml(it.simbolo_moneda)}${Number(it.total).toFixed(2)}${it.cambio_a_entregar ? ` · Llevar cambio ${escapeHtml(it.simbolo_moneda)}${it.cambio_a_entregar}` : ''}</span>`;

    return `
        <div class="glass rounded-2xl p-4 space-y-3 text-xs border-2 border-[#1B4332]/40">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                    <div class="font-extrabold text-sm text-[#1A241E]">#${escapeHtml(it.folio)} · ${escapeHtml(it.comercio_nombre)}</div>
                    <div class="text-[11px] text-[#5C6B62]">Cliente: <strong class="text-[#1A241E]">${escapeHtml(it.cliente_nombre)}</strong></div>
                </div>
                ${pagoInfo}
            </div>

            <div class="p-2.5 rounded-xl bg-[#F5EFE4] border border-[#DFD5C3] space-y-1">
                <div><i class="fa-solid fa-location-dot text-[#D6402B] mr-1"></i><strong>Dirección:</strong> ${escapeHtml(it.direccion_entrega)}</div>
                <div class="text-[11px] text-[#5C6B62]">${escapeHtml((it.items_resumen || []).join(', '))}</div>
            </div>

            <!-- 3 Botones Directos de Contacto con el Cliente -->
            <div class="grid grid-cols-3 gap-2">
                <a href="${escapeHtml(it.whatsapp_cliente_url)}" target="_blank" rel="noopener noreferrer"
                    class="py-2 px-2 rounded-xl bg-[#E4EFE9] hover:bg-[#D2E6DC] border border-[#B7D4C5] text-[#1B4332] font-bold text-[11px] flex items-center justify-center gap-1 transition">
                    <i class="fa-brands fa-whatsapp text-sm"></i> <span>WhatsApp</span>
                </a>
                <a href="tel:+${escapeHtml(it.cliente_telefono)}"
                    class="py-2 px-2 rounded-xl bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] font-bold text-[11px] flex items-center justify-center gap-1 transition">
                    <i class="fa-solid fa-phone text-[#1B4332]"></i> <span>Llamar</span>
                </a>
                <a href="${escapeHtml(it.google_maps_url)}" target="_blank" rel="noopener noreferrer"
                    class="py-2 px-2 rounded-xl bg-[#FDECE9] hover:bg-[#F9D7D0] border border-[#F6C2B9] text-[#B8321F] font-bold text-[11px] flex items-center justify-center gap-1 transition">
                    <i class="fa-solid fa-map-location-dot"></i> <span>Mapa</span>
                </a>
            </div>

            <!-- Confirmar Entrega con PIN de 4 dígitos -->
            <div class="flex items-center gap-2 pt-2 border-t border-[#EDE5D7]">
                ${it.requiere_pin ? `
                    <input id="repPinInput_${it.pedido_id}" type="text" inputmode="numeric" maxlength="4" placeholder="PIN 4 dígitos"
                        class="w-32 px-3 py-2 rounded-xl border text-center font-mono font-extrabold text-sm tracking-widest">
                ` : ''}
                <button type="button" onclick="confirmarEntregaRepartidor(${it.pedido_id}, ${it.requiere_pin ? 'true' : 'false'})"
                    class="flex-1 py-2.5 px-3 rounded-xl bg-[#1B4332] hover:bg-[#143326] text-white keep-white font-bold text-xs shadow-sm transition flex items-center justify-center gap-1.5">
                    <i class="fa-solid fa-check-double"></i> <span>Confirmar Entrega</span>
                </button>
            </div>

            <!-- Botón de Emergencia: Falla mecánica / Accidente / Liberar pedido -->
            <div class="pt-1 flex justify-end">
                <button type="button" onclick="liberarPedidoRepartidor(${it.pedido_id})"
                    class="px-3 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/35 text-rose-400 font-bold text-[11px] transition flex items-center gap-1.5">
                    <i class="fa-solid fa-triangle-exclamation"></i> <span>Reportar falla / Liberar pedido</span>
                </button>
            </div>
        </div>
    `;
}

async function toggleRepartidorDisponible(disponible) {
    try {
        await apiFetch('/api/v1/delivery/disponibilidad', {
            method: 'PATCH',
            body: { disponible: !!disponible }
        });
        showToast(disponible ? 'Ahora estás disponible para envíos.' : 'Modo pausa activado.');
        await loadRepartidorPanel();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.toggleRepartidorDisponible = toggleRepartidorDisponible;

async function tomarPedidoRepartidor(pedidoId) {
    try {
        await apiFetch(`/api/v1/delivery/pedidos/${pedidoId}/tomar`, { method: 'POST' });
        showToast('Pedido asignado. El cliente fue notificado que vas en camino.');
        await loadRepartidorPanel();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.tomarPedidoRepartidor = tomarPedidoRepartidor;

async function confirmarEntregaRepartidor(pedidoId, requierePin) {
    const pinEl = document.getElementById(`repPinInput_${pedidoId}`);
    const pin = pinEl ? pinEl.value.trim() : '';
    if (requierePin && pin.length !== 4) {
        showToast('Ingresa el código PIN de 4 dígitos que tiene el cliente en su WhatsApp.', true);
        pinEl?.focus();
        return;
    }
    try {
        await apiFetch(`/api/v1/delivery/pedidos/${pedidoId}/entregar`, {
            method: 'POST',
            body: { pin }
        });
        showToast('¡Entrega confirmada exitosamente!');
        await loadRepartidorPanel();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.confirmarEntregaRepartidor = confirmarEntregaRepartidor;

async function liberarPedidoRepartidor(pedidoId) {
    const motivo = window.prompt(
        'Indica el motivo para liberar este pedido (se alertará de urgencia a otro repartidor y pasarás a modo Pausa):',
        'Falla mecánica en moto'
    );
    if (!motivo || !motivo.trim()) return;
    try {
        const res = await apiFetch(`/api/v1/delivery/pedidos/${pedidoId}/liberar`, {
            method: 'POST',
            body: { motivo: motivo.trim(), desconectar_repartidor: true }
        });
        showToast(`Pedido liberado. Se alertó a ${res.repartidores_alertados || 0} repartidores disponibles.`);
        await loadRepartidorPanel();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.liberarPedidoRepartidor = liberarPedidoRepartidor;

// =========================================================
// GESTIÓN DE REPARTIDORES EN NAVEGACIÓN (COMERCIO & SUPERADMIN)
// =========================================================
function _renderListaRepartidores(targetId, lista, esFlotaSaas) {
    const box = document.getElementById(targetId);
    if (!box) return;
    if (!lista || lista.length === 0) {
        box.innerHTML = `<div class="text-center text-slate-400 py-5">No hay repartidores registrados todavía.</div>`;
        return;
    }
    const isSA = currentUser && currentUser.rol === 'SUPER_ADMIN';
    box.innerHTML = lista.map(r => `
        <div class="p-3.5 rounded-xl bg-slate-900 border border-slate-800 flex flex-wrap items-center justify-between gap-3">
            <div class="space-y-0.5 min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                    <span class="font-bold text-sm text-white">${escapeHtml(r.nombre_completo)}</span>
                    ${r.comercio_nombre ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30"><i class="fa-solid fa-store mr-1"></i>${escapeHtml(r.comercio_nombre)}</span>` : ''}
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${r.activo ? (r.disponible ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-amber-500/20 text-amber-300 border border-amber-500/30') : 'bg-rose-500/20 text-rose-300 border border-rose-500/30'}">
                        ${!r.activo ? 'CUENTA PAUSADA' : (r.disponible ? 'EN LÍNEA' : 'DESCONECTADO')}
                    </span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-slate-800 text-slate-300">${escapeHtml(r.vehiculo || 'Moto')}</span>
                </div>
                <p class="text-xs text-slate-400 truncate">${escapeHtml(r.email)} · WhatsApp: <span class="font-mono">${escapeHtml(r.telefono_whatsapp || '—')}</span></p>
            </div>
            ${isSA ? `
                <div class="flex items-center gap-1.5 shrink-0">
                    <button type="button" onclick="resetRepartidorPass(${r.id}, ${esFlotaSaas ? 'true' : 'false'})"
                        class="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition flex items-center gap-1">
                        <i class="fa-solid fa-key"></i> <span>Clave</span>
                    </button>
                    <button type="button" onclick="toggleRepartidorActivo(${r.id}, ${!r.activo}, ${esFlotaSaas ? 'true' : 'false'})"
                        class="px-2.5 py-1.5 rounded-lg text-xs font-bold transition ${r.activo ? 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-300' : 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300'}">
                        ${r.activo ? 'Pausar' : 'Activar'}
                    </button>
                    <button type="button" onclick="eliminarRepartidorAdmin(${r.id}, '${escapeHtml(r.nombre_completo).replace(/'/g, "\\'")}')"
                        class="px-2.5 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/35 text-rose-300 text-xs font-bold transition flex items-center gap-1" title="Eliminar repartidor del sistema">
                        <i class="fa-solid fa-trash-can"></i> <span>Eliminar</span>
                    </button>
                </div>
            ` : `
                <span class="text-[11px] text-slate-400 italic">Para eliminar o cambiarlo, contacta al administrador</span>
            `}
        </div>
    `).join('');
}

function _renderListaEnCamino(targetId, lista, esFlotaSaas) {
    const box = document.getElementById(targetId);
    if (!box) return;
    if (!lista || lista.length === 0) {
        box.innerHTML = `<div class="text-center text-slate-400 py-4">No hay entregas en camino en este momento.</div>`;
        return;
    }
    box.innerHTML = lista.map(p => `
        <div class="p-3.5 rounded-xl bg-slate-900 border border-amber-500/30 flex flex-wrap items-center justify-between gap-3">
            <div class="space-y-0.5 min-w-0">
                <div class="font-bold text-white flex flex-wrap items-center gap-2">
                    <span>#${escapeHtml(p.folio)} · ${escapeHtml(p.comercio_nombre)}</span>
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300">
                        <i class="fa-solid fa-motorcycle mr-1"></i>${escapeHtml(p.repartidor_nombre)}
                    </span>
                </div>
                <p class="text-xs text-slate-400 truncate">Cliente: ${escapeHtml(p.nombre_cliente)} · ${escapeHtml(p.direccion_entrega)}</p>
            </div>
            <button type="button" onclick="liberarPedidoDeliveryAdmin(${p.pedido_id}, ${esFlotaSaas ? 'true' : 'false'})"
                class="px-3 py-1.5 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/40 text-rose-300 font-bold text-xs transition flex items-center gap-1.5 shrink-0">
                <i class="fa-solid fa-triangle-exclamation"></i> <span>Liberar / Reasignar</span>
            </button>
        </div>
    `).join('');
}

async function cargarRepartidoresComercio() {
    if (!activeTenantId || !isComercioAdminOrSuper()) return;
    const modo = currentComercioData?.comercio?.delivery_config?.modo_delivery || 'FLOTA_SAAS';
    const badge = document.getElementById('repComercioModoBadge');
    if (badge) {
        const map = {
            FLOTA_SAAS: 'Modo actual: Flota Compartida SaaS',
            PROPIO: 'Modo actual: Repartidor Propio',
            DESACTIVADO: 'Modo actual: Delivery Desactivado'
        };
        badge.textContent = map[modo] || modo;
    }
    try {
        const data = await apiFetch(`/api/v1/delivery/repartidores?tenant_id=${activeTenantId}`);
        const reps = data.repartidores || [];
        const maxRep = Number(data.max_repartidores_propios || 1);
        const canPropio = !!(data.permite_repartidor_propio || currentUser?.rol === 'SUPER_ADMIN');
        const formEl = document.getElementById('formCrearRepartidorComercio');
        const limEl = document.getElementById('repComercioLimiteNotice');
        const noAuthEl = document.getElementById('repComercioNoAutorizadoNotice');
        const reached = reps.length >= maxRep && currentUser?.rol !== 'SUPER_ADMIN';

        if (!canPropio) {
            if (formEl) formEl.classList.add('hidden');
            if (limEl) limEl.classList.add('hidden');
            if (noAuthEl) noAuthEl.classList.remove('hidden');
        } else {
            if (noAuthEl) noAuthEl.classList.add('hidden');
            if (formEl) formEl.classList.toggle('hidden', reached);
            if (limEl) limEl.classList.toggle('hidden', !reached);
        }

        _renderListaRepartidores('repComercioList', reps, false);
        _renderListaEnCamino('repComercioEnCaminoList', data.en_camino || [], false);
    } catch (err) {
        showToast(err.message, true);
    }
}
window.cargarRepartidoresComercio = cargarRepartidoresComercio;

async function cargarFlotaSuperAdmin() {
    try {
        const data = await apiFetch('/api/v1/delivery/repartidores');
        const reps = data.repartidores || [];
        const propios = data.repartidores_propios || [];
        const permisos = data.comercios_permisos || [];
        const cntEl = document.getElementById('saTabCountRepartidores');
        if (cntEl) cntEl.textContent = String(reps.length + propios.length);

        const permBox = document.getElementById('saRepPermisosComerciosList');
        if (permBox) {
            permBox.innerHTML = permisos.map(c => `
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 flex items-center justify-between gap-2.5">
                    <div class="min-w-0">
                        <div class="font-bold text-white truncate">${escapeHtml(c.nombre_comercial)}</div>
                        <div class="text-[11px] text-slate-400">Repartidores propios: <strong>${c.repartidores_propios_count}/${c.max_repartidores_propios}</strong></div>
                    </div>
                    <button type="button" onclick="togglePermisoPropioComercio(${c.tenant_id}, ${!c.permite_repartidor_propio})"
                        class="px-3 py-1.5 rounded-xl text-[11px] font-bold transition shrink-0 ${c.permite_repartidor_propio
                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30'
                            : 'bg-slate-800 text-slate-300 border border-slate-700 hover:text-white'}">
                        <i class="fa-solid ${c.permite_repartidor_propio ? 'fa-toggle-on' : 'fa-toggle-off'} mr-1"></i>
                        <span>${c.permite_repartidor_propio ? 'Autorizado (1)' : 'No autorizado'}</span>
                    </button>
                </div>
            `).join('') || `<div class="text-slate-400 py-3">Sin comercios registrados.</div>`;
        }

        _renderListaRepartidores('saRepFlotaList', reps, true);
        _renderListaRepartidores('saRepPropiosList', propios, false);
        _renderListaEnCamino('saRepEnCaminoList', data.en_camino || [], true);
    } catch (err) {
        showToast(err.message, true);
    }
}
window.cargarFlotaSuperAdmin = cargarFlotaSuperAdmin;

async function togglePermisoPropioComercio(tenantId, habilitar) {
    try {
        await apiFetch(`/api/v1/delivery/comercio/${tenantId}/permiso-propio`, {
            method: 'PATCH',
            body: { permite_repartidor_propio: !!habilitar, max_repartidores_propios: 1 }
        });
        showToast(habilitar ? 'Permiso de Repartidor Propio (máx. 1) habilitado.' : 'Permiso revocado (comercio regresado a Flota SaaS).');
        await cargarFlotaSuperAdmin();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.togglePermisoPropioComercio = togglePermisoPropioComercio;

async function eliminarRepartidorAdmin(uid, nombre) {
    if (!window.confirm(`¿Eliminar definitivamente al repartidor "${nombre}" del sistema?`)) return;
    try {
        await apiFetch(`/api/v1/delivery/repartidores/${uid}`, { method: 'DELETE' });
        showToast('Repartidor eliminado del sistema.');
        await cargarFlotaSuperAdmin();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.eliminarRepartidorAdmin = eliminarRepartidorAdmin;

async function handleCrearRepartidor(e, esFlotaSaas) {
    e.preventDefault();
    const pfx = esFlotaSaas ? 'repSaas' : 'repCom';
    const nombre = document.getElementById(`${pfx}Nombre`)?.value.trim();
    const email = document.getElementById(`${pfx}Email`)?.value.trim();
    const password = document.getElementById(`${pfx}Pass`)?.value;
    const telefono = document.getElementById(`${pfx}Tel`)?.value.trim();
    const vehiculo = document.getElementById(`${pfx}Vehiculo`)?.value.trim() || 'Moto';

    try {
        await apiFetch('/api/v1/delivery/repartidores', {
            method: 'POST',
            body: {
                nombre_completo: nombre,
                email,
                password,
                telefono_whatsapp: telefono,
                vehiculo,
                es_flota_saas: !!esFlotaSaas,
                tenant_id: esFlotaSaas ? null : activeTenantId
            }
        });
        document.getElementById(`${pfx}Nombre`).value = '';
        document.getElementById(`${pfx}Email`).value = '';
        document.getElementById(`${pfx}Tel`).value = '';
        showToast('Repartidor registrado correctamente.');
        if (esFlotaSaas) await cargarFlotaSuperAdmin();
        else await cargarRepartidoresComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleCrearRepartidor = handleCrearRepartidor;

async function toggleRepartidorActivo(uid, nuevoActivo, esFlotaSaas) {
    try {
        await apiFetch(`/api/v1/delivery/repartidores/${uid}`, {
            method: 'PATCH',
            body: { activo: !!nuevoActivo }
        });
        showToast(nuevoActivo ? 'Repartidor activado.' : 'Repartidor pausado.');
        if (esFlotaSaas) await cargarFlotaSuperAdmin();
        else await cargarRepartidoresComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.toggleRepartidorActivo = toggleRepartidorActivo;

async function resetRepartidorPass(uid, esFlotaSaas) {
    const nueva = window.prompt('Nueva contraseña para el repartidor (mín. 6 caracteres):', 'Delivery#2026!');
    if (!nueva) return;
    try {
        await apiFetch(`/api/v1/delivery/repartidores/${uid}`, {
            method: 'PATCH',
            body: { nueva_password: nueva }
        });
        showToast('Contraseña del repartidor actualizada.');
        if (esFlotaSaas) await cargarFlotaSuperAdmin();
        else await cargarRepartidoresComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.resetRepartidorPass = resetRepartidorPass;

async function liberarPedidoDeliveryAdmin(pedidoId, esFlotaSaas) {
    const motivo = window.prompt(
        'Motivo de liberación/reasignación de emergencia:',
        'Incidencia de repartidor (reasignación manual)'
    );
    if (!motivo || !motivo.trim()) return;
    try {
        const res = await apiFetch(`/api/v1/delivery/pedidos/${pedidoId}/liberar`, {
            method: 'POST',
            body: { motivo: motivo.trim(), desconectar_repartidor: true }
        });
        showToast(`Entrega liberada. Alertados: ${res.repartidores_alertados || 0} repartidores.`);
        if (esFlotaSaas) await cargarFlotaSuperAdmin();
        else await cargarRepartidoresComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.liberarPedidoDeliveryAdmin = liberarPedidoDeliveryAdmin;

