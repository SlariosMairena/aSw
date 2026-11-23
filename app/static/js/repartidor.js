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

