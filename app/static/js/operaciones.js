// =========================================================================
// MÓDULO OPERACIONES AVANZADAS:
// A.1 Sucursales/Zonas/Mesas/Reservaciones · A.3 2FA · A.4 Kardex/Agotado
// B.1 POS Mostrador · B.4 Clientes Frecuentes & Recetas Producto <-> Inventario
// =========================================================================

let posCart = []; // [{ plato_id, nombre, precio_base, cantidad }]
let clientesDirectoryCache = [];
let editingSucursalZonasId = null;
let lastKnownOrderIds = new Set();

function getCurSym() {
    return (currentComercioData && currentComercioData.comercio && currentComercioData.comercio.simbolo_moneda) || '$';
}

// =========================================================================
// A.3 — SEGURIDAD 2FA (TOTP / GOOGLE AUTHENTICATOR)
// =========================================================================
function update2FABadgeNavbar() {
    const btn = document.getElementById('btnOpen2FA');
    const lbl = document.getElementById('lblBtn2FA');
    if (!btn || !lbl || !currentUser) return;

    if (currentUser.is_2fa_enabled) {
        btn.className = 'px-3 py-1.5 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/40 font-semibold transition flex items-center gap-1.5';
        lbl.textContent = '2FA Activo';
    } else {
        btn.className = 'px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 font-semibold transition flex items-center gap-1.5';
        lbl.textContent = 'Seguridad 2FA';
    }
}
window.update2FABadgeNavbar = update2FABadgeNavbar;

async function openModal2FA() {
    const modal = document.getElementById('modalSeguridad2FA');
    if (!modal) return;
    modal.classList.remove('hidden');

    const statusBanner = document.getElementById('twofaStatusBanner');
    const statusText = document.getElementById('twofaStatusText');
    const qrWrap = document.getElementById('twofaQrWrapper');
    const secCode = document.getElementById('twofaManualSecret');
    const inputCode = document.getElementById('twofaTotpCode');

    if (inputCode) inputCode.value = '';
    if (qrWrap) qrWrap.innerHTML = '<span class="text-xs text-slate-500"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Generando QR...</span>';

    if (currentUser && currentUser.is_2fa_enabled) {
        statusBanner.className = 'mb-4 p-3 rounded-xl border text-xs flex items-center gap-2 bg-emerald-950/60 border-emerald-500/40 text-emerald-200';
        statusText.textContent = 'Tu cuenta ya tiene 2FA activo. Puedes re-vincular otro dispositivo escaneando un nuevo codigo abajo.';
    } else {
        statusBanner.className = 'mb-4 p-3 rounded-xl border text-xs flex items-center gap-2 bg-amber-950/60 border-amber-500/40 text-amber-200';
        statusText.textContent = '2FA inactivo. Escanea el QR e ingresa el codigo de 6 digitos para proteger tu cuenta.';
    }

    try {
        const res = await apiFetch('/api/v1/auth/2fa/setup', { method: 'POST' });
        if (qrWrap) {
            qrWrap.innerHTML = `<img src="${res.qr_data_uri}" alt="QR 2FA" class="w-40 h-40 object-contain">`;
        }
        if (secCode) secCode.textContent = res.secret_manual || '---';
    } catch (err) {
        if (qrWrap) qrWrap.innerHTML = `<span class="text-xs text-rose-500">${escapeHtml(err.message)}</span>`;
        showToast(err.message, true);
    }
}
window.openModal2FA = openModal2FA;

function cerrarModal2FA() {
    document.getElementById('modalSeguridad2FA')?.classList.add('hidden');
}
window.cerrarModal2FA = cerrarModal2FA;

async function handleActivar2FA(e) {
    e.preventDefault();
    const code = (document.getElementById('twofaTotpCode')?.value || '').trim();
    if (!code) return;
    try {
        const res = await apiFetch('/api/v1/auth/2fa/enable', {
            method: 'POST',
            body: { totp_code: code }
        });
        if (currentUser) currentUser.is_2fa_enabled = Boolean(res.is_2fa_enabled);
        update2FABadgeNavbar();
        showToast('Autenticacion en dos pasos (2FA) activada correctamente.');
        cerrarModal2FA();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleActivar2FA = handleActivar2FA;

// =========================================================================
// B.1 — MODAL POS: NUEVO PEDIDO EN LOCAL / MOSTRADOR
// =========================================================================
function openModalPosPedido(prefillCli = null) {
    if (!currentComercioData || !activeTenantId) {
        showToast('Primero carga el panel del comercio.', true);
        return;
    }

    const sucursales = currentComercioData.sucursales || [];
    const sucSel = document.getElementById('posSucursalSelect');
    if (sucSel) {
        sucSel.innerHTML = sucursales.map(s => `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)}</option>`).join('');
        if (currentUser && currentUser.rol === 'SUCURSAL_STAFF' && currentUser.sucursal_id) {
            sucSel.value = String(currentUser.sucursal_id);
            sucSel.disabled = true;
        } else {
            sucSel.disabled = false;
            const filterVal = document.getElementById('filtroSucursalSelect')?.value;
            if (filterVal) sucSel.value = filterVal;
        }
    }

    posCart = [];
    document.getElementById('posBuscarProductoInput').value = '';
    document.getElementById('posPagoCon').value = '';
    document.getElementById('posNotas').value = '';

    if (prefillCli) {
        document.getElementById('posClienteNombre').value = prefillCli.nombre_cliente || 'Cliente Mostrador';
        document.getElementById('posClienteTelefono').value = prefillCli.telefono_whatsapp || '';
        document.getElementById('posDireccion').value = prefillCli.ultima_direccion || '';
        document.getElementById('posTipoEntrega').value = prefillCli.ultima_direccion ? 'DOMICILIO' : 'RECOGER_LOCAL';
    } else {
        document.getElementById('posClienteNombre').value = 'Cliente Mostrador';
        document.getElementById('posClienteTelefono').value = '';
        document.getElementById('posDireccion').value = '';
        document.getElementById('posTipoEntrega').value = 'RECOGER_LOCAL';
    }

    document.getElementById('posEstadoInicial').value = 'RECIBIDO';
    document.getElementById('posMetodoPago').value = 'EFECTIVO';

    onPosTipoEntregaChange();
    onPosSucursalChange();
    renderPosCarrito();
    document.getElementById('modalPosPedido')?.classList.remove('hidden');
}
window.openModalPosPedido = openModalPosPedido;

function cerrarModalPosPedido() {
    document.getElementById('modalPosPedido')?.classList.add('hidden');
}
window.cerrarModalPosPedido = cerrarModalPosPedido;

function onPosSucursalChange() {
    const sucId = Number(document.getElementById('posSucursalSelect')?.value || 0);
    const sucursales = (currentComercioData && currentComercioData.sucursales) || [];
    const suc = sucursales.find(s => s.id === sucId) || sucursales[0];
    const zonaSel = document.getElementById('posZonaSelect');
    if (zonaSel) {
        const baseCosto = suc ? Number(suc.costo_envio_base || 0) : 0;
        let opts = `<option value="" data-costo="${baseCosto}">Tarifa base (${getCurSym()}${baseCosto.toFixed(2)})</option>`;
        if (suc && Array.isArray(suc.zonas_envio)) {
            opts += suc.zonas_envio.map(z => `<option value="${z.id}" data-costo="${z.costo_envio}">${escapeHtml(z.nombre_zona)} (+${getCurSym()}${Number(z.costo_envio).toFixed(2)})</option>`).join('');
        }
        zonaSel.innerHTML = opts;
    }
    renderPosCatalogo();
    recalcularTotalPos();
}
window.onPosSucursalChange = onPosSucursalChange;

function onPosTipoEntregaChange() {
    const tipo = document.getElementById('posTipoEntrega')?.value || 'RECOGER_LOCAL';
    const dirBox = document.getElementById('posDireccionBox');
    if (dirBox) dirBox.classList.toggle('hidden', tipo !== 'DOMICILIO');
    recalcularTotalPos();
}
window.onPosTipoEntregaChange = onPosTipoEntregaChange;

function renderPosCatalogo() {
    const grid = document.getElementById('posCatalogoGrid');
    if (!grid || !currentComercioData) return;

    const q = (document.getElementById('posBuscarProductoInput')?.value || '').toLowerCase().trim();
    const sucId = Number(document.getElementById('posSucursalSelect')?.value || 0);
    const sym = getCurSym();
    const platos = currentComercioData.platos || [];

    const filtered = platos.filter(p => {
        const matchQ = !q || (p.nombre || '').toLowerCase().includes(q) || (p.categoria || '').toLowerCase().includes(q);
        return matchQ;
    });

    if (filtered.length === 0) {
        grid.innerHTML = `<div class="col-span-full text-center py-8 text-xs text-slate-500">Sin productos que coincidan con la busqueda.</div>`;
        return;
    }

    grid.innerHTML = filtered.map(p => {
        const agotadoSuc = Array.isArray(p.agotado_sucursal_ids) && p.agotado_sucursal_ids.includes(sucId);
        const disabled = !p.disponible_global || agotadoSuc;
        return `
            <button type="button" ${disabled ? 'disabled' : `onclick="posAddItem(${p.id})"`}
                class="text-left p-3 rounded-xl border transition flex flex-col justify-between gap-1 ${
                    disabled
                        ? 'bg-slate-950/60 border-rose-900/40 opacity-50 cursor-not-allowed'
                        : 'bg-slate-800/80 hover:bg-slate-800 border-slate-700 hover:border-emerald-500/60 cursor-pointer'
                }">
                <div class="flex items-start justify-between gap-2">
                    <span class="font-bold text-xs text-white leading-snug">${escapeHtml(p.nombre)}</span>
                    <span class="text-xs font-extrabold text-emerald-400 shrink-0">${sym}${Number(p.precio_base).toFixed(2)}</span>
                </div>
                <div class="flex items-center justify-between text-[10px] text-slate-400">
                    <span>${escapeHtml(p.categoria || 'General')}</span>
                    ${disabled
                        ? '<span class="text-rose-400 font-bold">AGOTADO</span>'
                        : '<span class="text-emerald-300 font-semibold"><i class="fa-solid fa-plus mr-0.5"></i> Agregar</span>'}
                </div>
            </button>
        `;
    }).join('');
}
window.renderPosCatalogo = renderPosCatalogo;

function posAddItem(platoId) {
    const plato = (currentComercioData?.platos || []).find(p => p.id === platoId);
    if (!plato) return;
    const existing = posCart.find(i => i.plato_id === platoId);
    if (existing) {
        existing.cantidad += 1;
    } else {
        posCart.push({
            plato_id: plato.id,
            nombre: plato.nombre,
            precio_base: Number(plato.precio_base || 0),
            cantidad: 1
        });
    }
    renderPosCarrito();
}
window.posAddItem = posAddItem;

function posUpdateQty(platoId, delta) {
    const idx = posCart.findIndex(i => i.plato_id === platoId);
    if (idx === -1) return;
    posCart[idx].cantidad += delta;
    if (posCart[idx].cantidad <= 0) posCart.splice(idx, 1);
    renderPosCarrito();
}
window.posUpdateQty = posUpdateQty;

function limpiarCarritoPos() {
    posCart = [];
    renderPosCarrito();
}
window.limpiarCarritoPos = limpiarCarritoPos;

function renderPosCarrito() {
    const list = document.getElementById('posCarritoList');
    const sym = getCurSym();
    if (!list) return;

    if (posCart.length === 0) {
        list.innerHTML = `<div class="text-slate-500 text-center py-6">Agrega productos desde el catalogo</div>`;
    } else {
        list.innerHTML = posCart.map(it => `
            <div class="flex items-center justify-between gap-2 bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-2">
                <div class="min-w-0 flex-1">
                    <div class="text-white font-semibold truncate">${escapeHtml(it.nombre)}</div>
                    <div class="text-[10px] text-slate-400">${sym}${it.precio_base.toFixed(2)} c/u</div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    <button type="button" onclick="posUpdateQty(${it.plato_id}, -1)" class="w-5 h-5 rounded bg-slate-800 hover:bg-rose-500/30 text-slate-200 font-bold">-</button>
                    <span class="w-5 text-center font-bold text-white">${it.cantidad}</span>
                    <button type="button" onclick="posUpdateQty(${it.plato_id}, 1)" class="w-5 h-5 rounded bg-slate-800 hover:bg-emerald-500/30 text-slate-200 font-bold">+</button>
                </div>
                <div class="text-emerald-400 font-bold w-16 text-right shrink-0">${sym}${(it.precio_base * it.cantidad).toFixed(2)}</div>
            </div>
        `).join('');
    }
    recalcularTotalPos();
}

function recalcularTotalPos() {
    const sym = getCurSym();
    const subtotal = posCart.reduce((acc, it) => acc + it.precio_base * it.cantidad, 0);
    const tipo = document.getElementById('posTipoEntrega')?.value || 'RECOGER_LOCAL';

    let envio = 0;
    if (tipo === 'DOMICILIO') {
        const zSel = document.getElementById('posZonaSelect');
        const selOpt = zSel && zSel.selectedOptions && zSel.selectedOptions[0];
        envio = selOpt ? Number(selOpt.getAttribute('data-costo') || 0) : 0;
    }

    const total = subtotal + envio;
    const elSub = document.getElementById('posSubtotalText');
    const elEnv = document.getElementById('posEnvioText');
    const elTot = document.getElementById('posTotalText');
    if (elSub) elSub.textContent = `${sym}${subtotal.toFixed(2)}`;
    if (elEnv) elEnv.textContent = `${sym}${envio.toFixed(2)}`;
    if (elTot) elTot.textContent = `${sym}${total.toFixed(2)}`;

    const pagoCon = parseFloat(document.getElementById('posPagoCon')?.value || '');
    const cambioRow = document.getElementById('posCambioRow');
    const cambioTxt = document.getElementById('posCambioText');
    const metodo = document.getElementById('posMetodoPago')?.value;
    if (cambioRow && cambioTxt && metodo === 'EFECTIVO' && !isNaN(pagoCon) && pagoCon >= total && total > 0) {
        cambioRow.classList.remove('hidden');
        cambioTxt.textContent = `${sym}${(pagoCon - total).toFixed(2)}`;
    } else if (cambioRow) {
        cambioRow.classList.add('hidden');
    }
}
window.recalcularTotalPos = recalcularTotalPos;

async function handleCrearPedidoManual(e) {
    e.preventDefault();
    if (!activeTenantId) return;
    if (posCart.length === 0) {
        showToast('Agrega al menos un producto antes de registrar el pedido.', true);
        return;
    }

    const sucId = Number(document.getElementById('posSucursalSelect')?.value || 0);
    const tipoEntrega = document.getElementById('posTipoEntrega')?.value || 'RECOGER_LOCAL';
    const zVal = document.getElementById('posZonaSelect')?.value;
    const pagaConRaw = document.getElementById('posPagoCon')?.value;
    const pagaCon = pagaConRaw ? parseFloat(pagaConRaw) : null;

    const payload = {
        sucursal_id: sucId,
        tipo_entrega: tipoEntrega,
        nombre_cliente: (document.getElementById('posClienteNombre')?.value || '').trim() || 'Cliente Mostrador',
        telefono_cliente: (document.getElementById('posClienteTelefono')?.value || '').trim() || '00000000',
        direccion_entrega: (document.getElementById('posDireccion')?.value || '').trim() || null,
        zona_envio_id: zVal ? Number(zVal) : null,
        metodo_pago: document.getElementById('posMetodoPago')?.value || 'EFECTIVO',
        marcar_pagado: true,
        paga_con_monto: isNaN(pagaCon) ? null : pagaCon,
        estado_inicial: document.getElementById('posEstadoInicial')?.value || 'RECIBIDO',
        notas_especiales: (document.getElementById('posNotas')?.value || '').trim() || null,
        items: posCart.map(it => ({ plato_id: it.plato_id, cantidad: it.cantidad }))
    };

    try {
        const res = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/pedidos/manual`, {
            method: 'POST',
            body: payload
        });
        showToast(`Pedido #${res.folio} registrado (${getCurSym()}${Number(res.total).toFixed(2)}).`);
        cerrarModalPosPedido();
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleCrearPedidoManual = handleCrearPedidoManual;

// =========================================================================
// A.1 — GESTION COMPLETA DE SUCURSALES, ZONAS, MESAS Y RESERVACIONES
// =========================================================================
function renderSucursalesYMesasV2(sucursales, comercio, reservaciones = []) {
    const esAdmin = typeof isComercioAdminOrSuper === 'function' ? isComercioAdminOrSuper() : false;
    const sym = (comercio && comercio.simbolo_moneda) || '$';

    const gridSuc = document.getElementById('sucursalesCardsGrid');
    if (gridSuc) {
        gridSuc.innerHTML = (sucursales || []).map(s => `
            <div class="p-4 rounded-xl bg-slate-900 border ${s.abierto_ahora ? 'border-slate-800' : 'border-rose-500/40'} flex flex-col gap-3">
                <div class="flex flex-col sm:flex-row items-start sm:items-center gap-4">
                    <img src="${s.qr_data_uri}" alt="QR WhatsApp ${escapeHtml(s.nombre_sucursal)}" class="w-24 h-24 rounded-xl bg-white p-1.5 shadow-md shrink-0">
                    <div class="space-y-1 text-xs flex-1 min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="font-bold text-base text-white">${escapeHtml(s.nombre_sucursal)}</span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${
                                s.abierto_ahora
                                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                    : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                            }">
                                ${s.abierto_ahora ? 'ABIERTA' : 'CERRADA'}
                            </span>
                        </div>
                        <div class="text-emerald-300 font-semibold">Codigo Opcion B: [${escapeHtml(s.codigo_whatsapp)}]</div>
                        <div class="text-slate-400"><i class="fa-solid fa-location-dot text-rose-400 mr-1"></i> ${escapeHtml(s.direccion_fisica || 'Sin direccion')}</div>
                        <div class="text-slate-300">
                            <i class="fa-regular fa-clock text-slate-400 mr-1"></i> ${escapeHtml(s.horario_apertura)} - ${escapeHtml(s.horario_cierre)}
                            <span class="mx-1 text-slate-600">|</span>
                            Entrega: <strong>${s.tiempo_estimado_entrega_min} min</strong>
                            <span class="mx-1 text-slate-600">|</span>
                            Ticket: <strong>${escapeHtml(s.ancho_ticket_termico)}</strong>
                        </div>
                    </div>
                </div>

                <!-- Zonas de Envio -->
                <div class="flex flex-wrap gap-1.5 pt-2 border-t border-slate-800/80">
                    ${(s.zonas_envio || []).length > 0
                        ? s.zonas_envio.map(z => `<span class="text-[11px] px-2 py-0.5 rounded-lg bg-slate-800 text-amber-200 border border-slate-700">${escapeHtml(z.nombre_zona)} (${sym}${Number(z.costo_envio).toFixed(2)})</span>`).join('')
                        : `<span class="text-[11px] text-slate-500">Envio tarifa base: ${sym}${Number(s.costo_envio_base || 0).toFixed(2)}</span>`}
                </div>

                <div class="flex flex-wrap items-center justify-between gap-2 pt-2 border-t border-slate-800/80">
                    <a href="${s.deep_link_whatsapp}" target="_blank"
                        class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-bold text-xs transition">
                        <i class="fa-brands fa-whatsapp"></i> Probar Link WhatsApp
                    </a>
                    <div class="flex items-center gap-1.5">
                        <button type="button" onclick="toggleSucursalAbiertaRapida(${s.id}, ${!s.abierto_ahora}, ${s.tiempo_estimado_entrega_min || 30}, '${escapeHtml(s.ancho_ticket_termico || '80mm')}')"
                            class="px-2.5 py-1.5 rounded-lg text-xs font-semibold border transition ${
                                s.abierto_ahora
                                    ? 'bg-rose-500/15 text-rose-300 border-rose-500/30 hover:bg-rose-500/25'
                                    : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/25'
                            }">
                            <i class="fa-solid fa-power-off mr-1"></i> ${s.abierto_ahora ? 'Pausar / Cerrar' : 'Abrir Sucursal'}
                        </button>
                        ${esAdmin ? `
                            <button type="button" onclick="openModalEditarSucursal(${s.id})"
                                class="px-2.5 py-1.5 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 text-xs font-bold transition">
                                <i class="fa-solid fa-sliders mr-1"></i> Editar / Zonas
                            </button>
                        ` : ''}
                    </div>
                </div>
            </div>
        `).join('');
    }

    // Llenar selector sucursal para agregar mesa rapida
    const mesaSucSel = document.getElementById('mesaNuevaSucursalSelect');
    if (mesaSucSel) {
        mesaSucSel.innerHTML = (sucursales || []).map(s => `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)}</option>`).join('');
    }

    const badgeRes = document.getElementById('badgeEstadoReservaciones');
    if (badgeRes && comercio) {
        if (comercio.reservaciones_habilitadas) {
            badgeRes.className = 'text-xs px-3 py-1 rounded-full font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
            badgeRes.innerHTML = '<i class="fa-solid fa-circle-check mr-1"></i> RESERVACIONES ACTIVAS';
        } else {
            badgeRes.className = 'text-xs px-3 py-1 rounded-full font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40';
            badgeRes.innerHTML = '<i class="fa-solid fa-lock mr-1"></i> PAGO ANTICIPO EN ESPERA (MODO 2)';
        }
    }

    const todasLasMesas = [];
    (sucursales || []).forEach(s => {
        (s.mesas || []).forEach(m => todasLasMesas.push({ ...m, sucursal: s.nombre_sucursal }));
    });
    const mesasGrid = document.getElementById('mesasGridInfo');
    if (mesasGrid) {
        if (todasLasMesas.length === 0) {
            mesasGrid.innerHTML = `<div class="col-span-full text-xs text-slate-500 py-3">No hay mesas registradas todavia.</div>`;
        } else {
            mesasGrid.innerHTML = todasLasMesas.map(m => `
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 text-xs flex items-start justify-between gap-2">
                    <div>
                        <div class="font-bold text-white"><i class="fa-solid fa-chair text-indigo-400 mr-1.5"></i> ${escapeHtml(m.nombre_mesa)}</div>
                        <div class="text-slate-400">${escapeHtml(m.sucursal)} • ${m.capacidad_personas} personas</div>
                        <div class="text-indigo-300 font-semibold mt-1">Anticipo: ${sym}${Number(m.monto_anticipo_reserva || 0).toFixed(2)}</div>
                    </div>
                    ${esAdmin ? `
                        <button type="button" onclick="handleEliminarMesa(${m.id})" title="Eliminar mesa"
                            class="text-rose-400 hover:text-rose-300 p-1 rounded">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    ` : ''}
                </div>
            `).join('');
        }
    }

    const resGrid = document.getElementById('reservacionesListGrid');
    if (resGrid) {
        const listRes = reservaciones || (currentComercioData && currentComercioData.reservaciones_mesa) || [];
        if (listRes.length === 0) {
            resGrid.innerHTML = `<div class="col-span-full text-xs text-slate-500 py-2">Sin reservaciones registradas aun.</div>`;
        } else {
            resGrid.innerHTML = listRes.map(r => {
                const st = r.estado_reserva || 'PENDIENTE';
                const badgeCls = st === 'CONFIRMADA'
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                    : st === 'COMPLETADA'
                        ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/40'
                        : st === 'CANCELADA'
                            ? 'bg-rose-500/20 text-rose-300 border-rose-500/40'
                            : 'bg-amber-500/20 text-amber-300 border-amber-500/40';
                return `
                    <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 text-xs space-y-2">
                        <div class="flex items-center justify-between gap-2">
                            <span class="font-bold text-white">${escapeHtml(r.nombre_cliente)} <span class="text-slate-400 font-normal">(#${escapeHtml(r.folio || r.id)})</span></span>
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${badgeCls}">${escapeHtml(st)}</span>
                        </div>
                        <div class="text-slate-400">
                            <i class="fa-solid fa-chair mr-1 text-indigo-400"></i> ${escapeHtml(r.mesa_nombre)} • ${r.personas} pers. • ${escapeHtml(r.fecha_hora)}
                        </div>
                        <div class="flex flex-wrap gap-1.5 pt-1">
                            <button type="button" onclick="cambiarEstadoReservacion(${r.id}, 'CONFIRMADA')" class="px-2 py-1 rounded bg-emerald-600/25 hover:bg-emerald-600/40 text-emerald-300 font-semibold">Confirmar</button>
                            <button type="button" onclick="cambiarEstadoReservacion(${r.id}, 'COMPLETADA')" class="px-2 py-1 rounded bg-indigo-600/25 hover:bg-indigo-600/40 text-indigo-300 font-semibold">Completar</button>
                            <button type="button" onclick="cambiarEstadoReservacion(${r.id}, 'CANCELADA')" class="px-2 py-1 rounded bg-rose-600/25 hover:bg-rose-600/40 text-rose-300 font-semibold">Cancelar</button>
                        </div>
                    </div>
                `;
            }).join('');
        }
    }
}
window.renderSucursalesYMesasV2 = renderSucursalesYMesasV2;

async function toggleSucursalAbiertaRapida(sucId, nuevoEstado, tiempoMin, anchoTicket) {
    try {
        await apiFetch(`/api/v1/panel/comercio/sucursales/${sucId}/operacion`, {
            method: 'PATCH',
            body: {
                abierto_ahora: nuevoEstado,
                tiempo_estimado_entrega_min: tiempoMin || 30,
                ancho_ticket_termico: anchoTicket || '80mm'
            }
        });
        showToast(nuevoEstado ? 'Sucursal marcada como ABIERTA.' : 'Sucursal pausada / CERRADA temporalmente.');
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.toggleSucursalAbiertaRapida = toggleSucursalAbiertaRapida;

function openModalNuevaSucursal() {
    editingSucursalZonasId = null;
    document.getElementById('sucEditId').value = '';
    document.getElementById('modalSucursalTitle').innerHTML = '<i class="fa-solid fa-store text-cyan-400"></i> Nueva Sucursal';
    document.getElementById('sucEditNombre').value = '';
    document.getElementById('sucEditTelefono').value = '';
    document.getElementById('sucEditDireccion').value = '';
    document.getElementById('sucEditTiempo').value = '30';
    document.getElementById('sucEditTicketMm').value = '80';
    document.getElementById('sucEditAbierta').checked = true;
    document.getElementById('sucEditDomicilio').checked = true;
    document.getElementById('sucEditRecoger').checked = true;
    document.getElementById('sucZonasSection').classList.add('hidden');
    document.getElementById('modalSucursalCrud')?.classList.remove('hidden');
}
window.openModalNuevaSucursal = openModalNuevaSucursal;

function openModalEditarSucursal(sucursalId) {
    const s = (currentComercioData?.sucursales || []).find(x => x.id === sucursalId);
    if (!s) return;
    editingSucursalZonasId = s.id;
    document.getElementById('sucEditId').value = String(s.id);
    document.getElementById('modalSucursalTitle').innerHTML = `<i class="fa-solid fa-store text-cyan-400"></i> Editar: ${escapeHtml(s.nombre_sucursal)}`;
    document.getElementById('sucEditNombre').value = s.nombre_sucursal || '';
    document.getElementById('sucEditTelefono').value = s.telefono_sucursal || '';
    document.getElementById('sucEditDireccion').value = s.direccion_fisica || '';
    document.getElementById('sucEditTiempo').value = s.tiempo_estimado_entrega_min || 30;
    document.getElementById('sucEditTicketMm').value = String(s.ancho_ticket_termico || '80mm').startsWith('58') ? '58' : '80';
    document.getElementById('sucEditAbierta').checked = Boolean(s.abierto_ahora);
    document.getElementById('sucEditDomicilio').checked = s.permite_domicilio !== false;
    document.getElementById('sucEditRecoger').checked = s.permite_recoger_local !== false;

    renderZonasModalList(s);
    document.getElementById('sucZonasSection').classList.remove('hidden');
    document.getElementById('modalSucursalCrud')?.classList.remove('hidden');
}
window.openModalEditarSucursal = openModalEditarSucursal;

function renderZonasModalList(sucursal) {
    const wrap = document.getElementById('sucZonasList');
    if (!wrap) return;
    const sym = getCurSym();
    const zonas = sucursal.zonas_envio || [];
    if (zonas.length === 0) {
        wrap.innerHTML = `<div class="text-xs text-slate-500 py-2">Sin zonas especificas aun. Agrega una abajo.</div>`;
        return;
    }
    wrap.innerHTML = zonas.map(z => `
        <div class="flex items-center justify-between bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs">
            <div>
                <span class="font-bold text-white">${escapeHtml(z.nombre_zona)}</span>
                <span class="text-amber-300 ml-2">${sym}${Number(z.costo_envio).toFixed(2)}</span>
                <span class="text-slate-400 ml-2">(+${z.tiempo_extra_min || 0} min)</span>
            </div>
            <button type="button" onclick="handleEliminarZonaSucursal(${z.id})" class="text-rose-400 hover:text-rose-300 px-2 py-1">
                <i class="fa-solid fa-trash"></i>
            </button>
        </div>
    `).join('');
}

function cerrarModalSucursalCrud() {
    document.getElementById('modalSucursalCrud')?.classList.add('hidden');
}
window.cerrarModalSucursalCrud = cerrarModalSucursalCrud;

async function handleGuardarSucursal(e) {
    e.preventDefault();
    if (!activeTenantId) return;

    const editId = document.getElementById('sucEditId')?.value;
    const nombre = (document.getElementById('sucEditNombre')?.value || '').trim();
    const dir = (document.getElementById('sucEditDireccion')?.value || '').trim() || 'Direccion por definir';
    const tel = (document.getElementById('sucEditTelefono')?.value || '').trim();
    const tiempo = parseInt(document.getElementById('sucEditTiempo')?.value || '30', 10);
    const ticketMm = `${document.getElementById('sucEditTicketMm')?.value || '80'}mm`;

    try {
        if (editId) {
            await apiFetch(`/api/v1/panel/comercio/sucursales/${editId}`, {
                method: 'PUT',
                body: {
                    nombre_sucursal: nombre,
                    direccion_fisica: dir,
                    telefono_sucursal: tel || null,
                    tiempo_estimado_entrega_min: tiempo,
                    ancho_ticket_termico: ticketMm,
                    abierto_ahora: document.getElementById('sucEditAbierta').checked,
                    permite_domicilio: document.getElementById('sucEditDomicilio').checked,
                    permite_recoger_local: document.getElementById('sucEditRecoger').checked
                }
            });
            showToast('Sucursal actualizada correctamente.');
        } else {
            const slugCode = `${(currentComercioData?.comercio?.slug || 'suc').toUpperCase().slice(0, 6)}-${Math.floor(100 + Math.random() * 899)}`;
            await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/sucursales`, {
                method: 'POST',
                body: {
                    nombre_sucursal: nombre,
                    codigo_whatsapp: slugCode,
                    direccion_fisica: dir,
                    tiempo_estimado_entrega_min: tiempo,
                    ancho_ticket_termico: ticketMm
                }
            });
            showToast('Nueva sucursal creada con su codigo QR de WhatsApp.');
        }
        cerrarModalSucursalCrud();
        await reloadComercioDashboard(true);
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleGuardarSucursal = handleGuardarSucursal;

async function handleAgregarZonaSucursal() {
    if (!editingSucursalZonasId) return;
    const nombre = (document.getElementById('nuevaZonaNombre')?.value || '').trim();
    const costo = parseFloat(document.getElementById('nuevaZonaCosto')?.value || '0');
    const tiempoExtra = parseInt(document.getElementById('nuevaZonaTiempo')?.value || '15', 10);
    if (!nombre) {
        showToast('Escribe el nombre de la zona o colonia.', true);
        return;
    }
    try {
        await apiFetch(`/api/v1/panel/comercio/sucursales/${editingSucursalZonasId}/zonas`, {
            method: 'POST',
            body: { nombre_zona: nombre, costo_envio: costo, tiempo_extra_min: tiempoExtra }
        });
        document.getElementById('nuevaZonaNombre').value = '';
        document.getElementById('nuevaZonaCosto').value = '';
        showToast('Zona de envio agregada.');
        await reloadComercioDashboard();
        const updatedSuc = (currentComercioData?.sucursales || []).find(s => s.id === editingSucursalZonasId);
        if (updatedSuc) renderZonasModalList(updatedSuc);
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleAgregarZonaSucursal = handleAgregarZonaSucursal;

async function handleEliminarZonaSucursal(zonaId) {
    try {
        await apiFetch(`/api/v1/panel/comercio/zonas/${zonaId}`, { method: 'DELETE' });
        showToast('Zona eliminada.');
        await reloadComercioDashboard();
        const updatedSuc = (currentComercioData?.sucursales || []).find(s => s.id === editingSucursalZonasId);
        if (updatedSuc) renderZonasModalList(updatedSuc);
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleEliminarZonaSucursal = handleEliminarZonaSucursal;

async function handleCrearMesaRapida() {
    const sucId = Number(document.getElementById('mesaNuevaSucursalSelect')?.value || 0);
    const nombre = (document.getElementById('mesaNuevaNombre')?.value || '').trim();
    const cap = parseInt(document.getElementById('mesaNuevaCapacidad')?.value || '4', 10);
    const anticipo = parseFloat(document.getElementById('mesaNuevaAnticipo')?.value || '0');
    if (!sucId || !nombre) {
        showToast('Selecciona una sucursal y escribe el nombre de la mesa.', true);
        return;
    }
    try {
        await apiFetch(`/api/v1/panel/comercio/sucursales/${sucId}/mesas`, {
            method: 'POST',
            body: { nombre_mesa: nombre, capacidad_personas: cap, monto_anticipo_reserva: anticipo }
        });
        document.getElementById('mesaNuevaNombre').value = '';
        showToast(`Mesa "${nombre}" agregada.`);
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleCrearMesaRapida = handleCrearMesaRapida;

async function handleEliminarMesa(mesaId) {
    try {
        await apiFetch(`/api/v1/panel/comercio/mesas/${mesaId}`, { method: 'DELETE' });
        showToast('Mesa eliminada.');
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleEliminarMesa = handleEliminarMesa;

async function cambiarEstadoReservacion(resId, nuevoEstado) {
    try {
        await apiFetch(`/api/v1/panel/comercio/reservaciones/${resId}/estado`, {
            method: 'PATCH',
            body: { estado_reserva: nuevoEstado }
        });
        showToast(`Reservacion actualizada a ${nuevoEstado}.`);
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.cambiarEstadoReservacion = cambiarEstadoReservacion;

// =========================================================================
// A.4 & B.4 — KARDEX INVENTARIO, AGOTADO POR SUCURSAL Y RECETAS PRODUCTO
// =========================================================================
async function openModalKardexInventario(itemId = null, itemNombre = '') {
    if (!activeTenantId) return;
    const modal = document.getElementById('modalKardexInventario');
    const title = document.getElementById('kardexModalTitle');
    const tbody = document.getElementById('kardexTableBody');
    if (title) title.textContent = itemNombre ? `Kardex: ${itemNombre}` : 'Kardex e Historial Completo de Inventario';
    if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Cargando movimientos...</td></tr>`;
    modal?.classList.remove('hidden');

    try {
        const params = new URLSearchParams();
        if (itemId) params.set('item_id', String(itemId));
        const sucFilter = document.getElementById('filtroSucursalSelect')?.value;
        if (sucFilter) params.set('sucursal_id', sucFilter);

        const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/inventario/movimientos?${params.toString()}`);
        const movs = data.movimientos || [];
        if (movs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-slate-500">Sin movimientos registrados todavia.</td></tr>`;
            return;
        }
        tbody.innerHTML = movs.map(m => {
            const esEntrada = m.tipo_movimiento === 'ENTRADA';
            const fechaTxt = m.fecha ? new Date(m.fecha).toLocaleString('es-NI') : '-';
            return `
                <tr class="hover:bg-slate-800/40">
                    <td class="px-3 py-2 text-slate-400 whitespace-nowrap">${escapeHtml(fechaTxt)}</td>
                    <td class="px-3 py-2 font-semibold text-white">${escapeHtml(m.nombre_insumo)}</td>
                    <td class="px-3 py-2 text-slate-300">${escapeHtml(m.sucursal_nombre || 'General')}</td>
                    <td class="px-3 py-2">
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            esEntrada ? 'bg-emerald-500/20 text-emerald-300' : 'bg-rose-500/20 text-rose-300'
                        }">${escapeHtml(m.tipo_movimiento)}</span>
                    </td>
                    <td class="px-3 py-2 text-right font-mono font-bold ${esEntrada ? 'text-emerald-400' : 'text-rose-400'}">
                        ${esEntrada ? '+' : '-'}${Number(m.cantidad || 0).toFixed(2)} ${escapeHtml(m.unidad_medida || '')}
                    </td>
                    <td class="px-3 py-2 text-slate-300">${escapeHtml(m.motivo || '-')}</td>
                    <td class="px-3 py-2 text-slate-400">${escapeHtml(m.usuario_responsable || '-')}</td>
                </tr>
            `;
        }).join('');
    } catch (err) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="text-center py-6 text-rose-400">${escapeHtml(err.message)}</td></tr>`;
    }
}
window.openModalKardexInventario = openModalKardexInventario;

function cerrarModalKardex() {
    document.getElementById('modalKardexInventario')?.classList.add('hidden');
}
window.cerrarModalKardex = cerrarModalKardex;

async function toggleAgotadoEnSucursal(platoId) {
    const sucursales = (currentComercioData && currentComercioData.sucursales) || [];
    let sucId = Number(document.getElementById('filtroSucursalSelect')?.value || 0);
    if (!sucId && currentUser && currentUser.sucursal_id) sucId = Number(currentUser.sucursal_id);
    if (!sucId && sucursales.length === 1) sucId = sucursales[0].id;

    if (!sucId) {
        showToast('Selecciona arriba la sucursal especifica para agotar/habilitar solo en esa sede.', true);
        return;
    }

    try {
        const res = await apiFetch(`/api/v1/panel/comercio/platos/${platoId}/toggle-agotado-sucursal`, {
            method: 'POST',
            body: { sucursal_id: sucId }
        });
        showToast(res.agotado_hoy ? 'Marcado AGOTADO solo en la sucursal seleccionada.' : 'Habilitado nuevamente en la sucursal seleccionada.');
        await reloadComercioDashboard();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.toggleAgotadoEnSucursal = toggleAgotadoEnSucursal;

async function openModalRecetaProducto(platoId, platoNombre) {
    if (!activeTenantId) return;
    document.getElementById('recetaPlatoId').value = String(platoId);
    document.getElementById('recetaModalTitle').textContent = `Vincular Stock: ${platoNombre}`;
    const list = document.getElementById('recetaRowsList');
    list.innerHTML = `<div class="text-xs text-slate-400 py-4 text-center"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Cargando inventario y receta...</div>`;
    document.getElementById('modalRecetaProducto')?.classList.remove('hidden');

    try {
        if (typeof inventarioItems === 'undefined' || !inventarioItems.length) {
            const invData = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/inventario`);
            if (typeof inventarioItems !== 'undefined') inventarioItems = invData.items || [];
        }
        const recData = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/recetas`);
        const rows = (recData.recetas_por_plato && recData.recetas_por_plato[String(platoId)]) || [];
        list.innerHTML = '';
        if (rows.length === 0) {
            agregarFilaReceta();
        } else {
            rows.forEach(r => agregarFilaReceta(r.item_inventario_id, r.cantidad_por_unidad));
        }
    } catch (err) {
        list.innerHTML = `<div class="text-xs text-rose-400 py-3">${escapeHtml(err.message)}</div>`;
    }
}
window.openModalRecetaProducto = openModalRecetaProducto;

function cerrarModalReceta() {
    document.getElementById('modalRecetaProducto')?.classList.add('hidden');
}
window.cerrarModalReceta = cerrarModalReceta;

function agregarFilaReceta(selectedItemId = '', qty = 1) {
    const list = document.getElementById('recetaRowsList');
    if (!list) return;
    const items = (typeof inventarioItems !== 'undefined' && inventarioItems) || [];
    const optionsHtml = items.length > 0
        ? items.map(it => `<option value="${it.id}" ${Number(selectedItemId) === it.id ? 'selected' : ''}>${escapeHtml(it.nombre_insumo)} (${escapeHtml(it.unidad_medida)})</option>`).join('')
        : '<option value="">Sin insumos en inventario</option>';

    const row = document.createElement('div');
    row.className = 'receta-row grid grid-cols-12 gap-2 items-center bg-slate-950 border border-slate-800 rounded-xl p-2';
    row.innerHTML = `
        <select class="receta-item-select col-span-7 bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white outline-none">
            ${optionsHtml}
        </select>
        <input type="number" step="0.01" min="0.01" value="${qty}"
            class="receta-qty-input col-span-3 bg-slate-900 border border-slate-700 rounded-lg px-2.5 py-1.5 text-xs text-white text-right outline-none" title="Cantidad a descontar por cada unidad vendida">
        <button type="button" onclick="this.parentElement.remove()" class="col-span-2 py-1.5 rounded-lg bg-rose-500/15 hover:bg-rose-500/30 text-rose-300 text-xs font-bold">
            <i class="fa-solid fa-xmark"></i>
        </button>
    `;
    list.appendChild(row);
}
window.agregarFilaReceta = agregarFilaReceta;

async function handleGuardarRecetaProducto() {
    const platoId = Number(document.getElementById('recetaPlatoId')?.value || 0);
    if (!platoId) return;

    const payloadItems = [];
    document.querySelectorAll('#recetaRowsList .receta-row').forEach(row => {
        const itemId = Number(row.querySelector('.receta-item-select')?.value || 0);
        const qty = parseFloat(row.querySelector('.receta-qty-input')?.value || '0');
        if (itemId > 0 && qty > 0) {
            payloadItems.push({ item_inventario_id: itemId, cantidad_por_unidad: qty });
        }
    });

    try {
        await apiFetch(`/api/v1/panel/comercio/platos/${platoId}/receta`, {
            method: 'PUT',
            body: { items: payloadItems }
        });
        showToast('Vinculacion producto <-> inventario guardada. Se descontara automaticamente al entregar pedidos.');
        cerrarModalReceta();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleGuardarRecetaProducto = handleGuardarRecetaProducto;

// =========================================================================
// B.4 — DIRECTORIO DE CLIENTES FRECUENTES
// =========================================================================
function toggleNuevoClienteForm() {
    document.getElementById('formNuevoClienteRapido')?.classList.toggle('hidden');
}
window.toggleNuevoClienteForm = toggleNuevoClienteForm;

async function cargarClientesComercio() {
    if (!activeTenantId) return;
    const tbody = document.getElementById('clientesTableBody');
    if (!tbody) return;
    const q = (document.getElementById('clientesSearchInput')?.value || '').trim();
    const qs = q ? `?q=${encodeURIComponent(q)}` : '';

    try {
        const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/clientes${qs}`);
        clientesDirectoryCache = data.clientes || [];
        if (clientesDirectoryCache.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center py-8 text-slate-500">Sin clientes registrados con este filtro.</td></tr>`;
            return;
        }
        tbody.innerHTML = clientesDirectoryCache.map((c, idx) => `
            <tr class="hover:bg-slate-800/40">
                <td class="px-4 py-3 font-bold text-white">${escapeHtml(c.nombre_cliente)}</td>
                <td class="px-4 py-3 text-emerald-300 font-mono">${escapeHtml(c.telefono_whatsapp)}</td>
                <td class="px-4 py-3 text-slate-300">${escapeHtml(c.ultima_direccion || 'Sin direccion guardada')}${c.ultima_zona_envio ? ` <span class="text-amber-300">(${escapeHtml(c.ultima_zona_envio)})</span>` : ''}</td>
                <td class="px-4 py-3 text-center">
                    <span class="px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-bold">${c.total_pedidos_realizados || 0}</span>
                </td>
                <td class="px-4 py-3 text-right">
                    <button type="button" onclick="enviarClienteAPos(${idx})" class="px-3 py-1.5 rounded-lg bg-emerald-600/25 hover:bg-emerald-600/40 text-emerald-300 font-bold text-xs transition">
                        <i class="fa-solid fa-cash-register mr-1"></i> Nuevo Pedido POS
                    </button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-6 text-rose-400">${escapeHtml(err.message)}</td></tr>`;
    }
}
window.cargarClientesComercio = cargarClientesComercio;

function enviarClienteAPos(idx) {
    const cli = clientesDirectoryCache[idx];
    if (cli) openModalPosPedido(cli);
}
window.enviarClienteAPos = enviarClienteAPos;

async function handleCrearClienteRapido(e) {
    e.preventDefault();
    if (!activeTenantId) return;
    try {
        await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/clientes`, {
            method: 'POST',
            body: {
                nombre_cliente: document.getElementById('cliNuevoNombre').value.trim(),
                telefono_whatsapp: document.getElementById('cliNuevoTelefono').value.trim(),
                ultima_direccion: document.getElementById('cliNuevoDireccion').value.trim() || null
            }
        });
        document.getElementById('cliNuevoNombre').value = '';
        document.getElementById('cliNuevoTelefono').value = '';
        document.getElementById('cliNuevoDireccion').value = '';
        document.getElementById('formNuevoClienteRapido')?.classList.add('hidden');
        showToast('Cliente guardado en el directorio.');
        await cargarClientesComercio();
    } catch (err) {
        showToast(err.message, true);
    }
}
window.handleCrearClienteRapido = handleCrearClienteRapido;
