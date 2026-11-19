// =========================================================================
// SIMULADOR INTERACTIVO DE BOT WHATSAPP + DIAGNOSTICO EVOLUTION / META
// =========================================================================

const WA_SIM_PHONE = '50588001234';

function formatWhatsappTextHtml(text) {
    const safe = escapeHtml(text || '');
    return safe
        .replace(/\*([^*]+)\*/g, '<strong>$1</strong>')
        .replace(/_([^_]+)_/g, '<em class="text-slate-300">$1</em>')
        .replace(/\n/g, '<br>');
}

function appendWaSimBubble(remitente, text, meta = '') {
    const box = document.getElementById('waSimMessagesBox');
    if (!box) return;
    if (box.querySelector('.wa-sim-empty-placeholder')) {
        box.innerHTML = '';
    }
    const isClient = remitente === 'CLIENTE';
    const hora = new Date().toLocaleTimeString('es-NI', { hour: '2-digit', minute: '2-digit' });
    const div = document.createElement('div');
    div.className = `flex ${isClient ? 'justify-end' : 'justify-start'}`;
    div.innerHTML = `
        <div class="max-w-[85%] rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed shadow ${
            isClient
                ? 'bg-emerald-700/80 text-white rounded-br-sm'
                : 'bg-slate-800 border border-slate-700 text-slate-100 rounded-bl-sm'
        }">
            <div class="text-[10px] font-bold opacity-75 mb-1 flex items-center justify-between gap-3">
                <span>${isClient ? '<i class="fa-solid fa-user mr-1"></i> Tu (Cliente)' : '<i class="fa-solid fa-robot text-emerald-400 mr-1"></i> Bot WhatsApp'}${meta ? ` · ${escapeHtml(meta)}` : ''}</span>
                <span>${hora}</span>
            </div>
            <div>${formatWhatsappTextHtml(text)}</div>
        </div>
    `;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
}

function renderWaSimQuickReplies(replies = []) {
    const bar = document.getElementById('waSimQuickReplies');
    if (!bar) return;
    if (!Array.isArray(replies) || replies.length === 0) {
        bar.innerHTML = `<span class="text-[11px] text-slate-500">Escribe un mensaje o pulsa Simular QR para ver el menu.</span>`;
        return;
    }
    bar.innerHTML = replies.map(r => `
        <button type="button" onclick="enviarMensajeSimulador('${String(r).replace(/'/g, "\\'")}')"
            class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-emerald-600/30 text-emerald-300 border border-slate-700 hover:border-emerald-500/50 text-[11px] font-bold transition">
            ${escapeHtml(r)}
        </button>
    `).join('');
}

function openModalWhatsappSim(sucursalId = null) {
    if (!currentComercioData || !activeTenantId) {
        showToast('Primero carga el panel de un comercio.', true);
        return;
    }
    const sucSel = document.getElementById('waSimSucursalSelect');
    const sucursales = currentComercioData.sucursales || [];
    if (sucSel) {
        sucSel.innerHTML = sucursales.map(s => `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)} [${escapeHtml(s.codigo_whatsapp)}]</option>`).join('');
        if (sucursalId) {
            sucSel.value = String(sucursalId);
        } else if (currentUser && currentUser.rol === 'SUCURSAL_STAFF' && currentUser.sucursal_id) {
            sucSel.value = String(currentUser.sucursal_id);
            sucSel.disabled = true;
        } else {
            sucSel.disabled = false;
        }
    }

    document.getElementById('waSimPedidoBanner')?.classList.add('hidden');
    const box = document.getElementById('waSimMessagesBox');
    if (box) {
        box.innerHTML = `<div class="wa-sim-empty-placeholder text-center text-xs text-slate-500 py-10">
            Pulsa <strong class="text-emerald-400">Simular QR</strong> o escribe <strong class="text-white">Hola</strong> para iniciar una prueba con el Bot.
        </div>`;
    }
    renderWaSimQuickReplies(['MENU', 'VER', 'HUMANO']);
    document.getElementById('modalWhatsappSim')?.classList.remove('hidden');
    cargarEstadoConexionWhatsapp(false);
}
window.openModalWhatsappSim = openModalWhatsappSim;

function cerrarModalWhatsappSim() {
    document.getElementById('modalWhatsappSim')?.classList.add('hidden');
}
window.cerrarModalWhatsappSim = cerrarModalWhatsappSim;

async function iniciarSaludoSimulador() {
    const sucId = Number(document.getElementById('waSimSucursalSelect')?.value || 0);
    const suc = (currentComercioData?.sucursales || []).find(s => s.id === sucId);
    const comercioNombre = currentComercioData?.comercio?.nombre_comercial || 'Comercio';
    const saludo = suc
        ? `Hola! Quiero hacer un pedido en ${comercioNombre} [${suc.codigo_whatsapp}]`
        : 'Hola';
    await enviarMensajeSimulador(saludo);
}
window.iniciarSaludoSimulador = iniciarSaludoSimulador;

async function reiniciarChatSimulador(silent = false) {
    const sucId = Number(document.getElementById('waSimSucursalSelect')?.value || 0);
    if (!sucId) return;
    try {
        await apiFetch('/api/v1/whatsapp/simulador/reiniciar', {
            method: 'POST',
            body: { sucursal_id: sucId, telefono_simulado: WA_SIM_PHONE }
        });
        document.getElementById('waSimPedidoBanner')?.classList.add('hidden');
        const box = document.getElementById('waSimMessagesBox');
        if (box) {
            box.innerHTML = `<div class="wa-sim-empty-placeholder text-center text-xs text-slate-500 py-10">
                Sesion reiniciada. Pulsa <strong class="text-emerald-400">Simular QR</strong> para comenzar un pedido nuevo.
            </div>`;
        }
        renderWaSimQuickReplies(['MENU']);
        if (!silent) showToast('Chat simulado reiniciado.');
    } catch (err) {
        if (!silent) showToast(err.message, true);
    }
}
window.reiniciarChatSimulador = reiniciarChatSimulador;

function handleSubmitSimulador(e) {
    e.preventDefault();
    const input = document.getElementById('waSimInput');
    const texto = (input?.value || '').trim();
    if (!texto) return;
    input.value = '';
    enviarMensajeSimulador(texto);
}
window.handleSubmitSimulador = handleSubmitSimulador;

async function enviarMensajeSimulador(texto) {
    const sucId = Number(document.getElementById('waSimSucursalSelect')?.value || 0);
    const modoBot = document.getElementById('waSimModoSelect')?.value || 'MENU_GUIADO';
    if (!sucId || !texto) return;

    appendWaSimBubble('CLIENTE', texto);
    const btn = document.getElementById('waSimSendBtn');
    if (btn) btn.disabled = true;

    try {
        const res = await apiFetch('/api/v1/whatsapp/simulador/mensaje', {
            method: 'POST',
            body: {
                sucursal_id: sucId,
                mensaje: texto,
                telefono_simulado: WA_SIM_PHONE,
                nombre_cliente: 'Cliente Prueba Panel',
                modo_bot: modoBot
            }
        });

        if (res.modo_atencion === 'HUMANO_ACTIVO' && !res.reply_text) {
            appendWaSimBubble('BOT', 'Chat en modo HUMANO ACTIVO: el bot esta pausado para esta conversacion. Puedes responder desde el modal de chat del pedido o reiniciar el simulador.', 'Pausado');
            renderWaSimQuickReplies([]);
        } else if (res.reply_text) {
            const meta = res.ai_used ? `IA: ${res.ai_used}` : (modoBot === 'MENU_GUIADO' ? 'Menu Guiado' : 'Hibrido');
            appendWaSimBubble('BOT', res.reply_text, meta);
            renderWaSimQuickReplies(res.quick_replies || []);
        }

        if (res.pedido_creado) {
            const banner = document.getElementById('waSimPedidoBanner');
            const bannerTxt = document.getElementById('waSimPedidoBannerText');
            if (banner && bannerTxt) {
                bannerTxt.innerHTML = `<i class="fa-solid fa-circle-check mr-1.5 text-emerald-400"></i> Pedido <strong>#${escapeHtml(res.pedido_creado.folio)}</strong> creado y enviado a tu tablero Kanban.`;
                banner.classList.remove('hidden');
            }
            if (typeof reloadComercioDashboard === 'function') {
                await reloadComercioDashboard(false);
            }
        }
    } catch (err) {
        showToast(err.message, true);
    } finally {
        if (btn) btn.disabled = false;
        document.getElementById('waSimInput')?.focus();
    }
}
window.enviarMensajeSimulador = enviarMensajeSimulador;

async function cargarEstadoConexionWhatsapp(checkEvolution = false) {
    const box = document.getElementById('waConnStatusBox');
    const urlEv = document.getElementById('waWebhookEvolutionUrl');
    const urlMeta = document.getElementById('waWebhookMetaUrl');
    if (!box) return;

    if (checkEvolution) {
        box.innerHTML = `<div class="text-slate-400"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Probando conexion con Evolution API...</div>`;
    }

    try {
        const data = await apiFetch(`/api/v1/whatsapp/estado-conexion?check_evolution=${checkEvolution ? 'true' : 'false'}`);
        if (urlEv) urlEv.textContent = data.webhook_evolution_url || '---';
        if (urlMeta) urlMeta.textContent = data.webhook_meta_url || '---';

        const aiList = (data.ai_providers_available || []).length > 0
            ? data.ai_providers_available.join(', ')
            : 'Ninguna (usando Menu Guiado costo cero)';
        const ev = data.evolution || {};
        let evBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">Modo Simulado Local</span>`;
        if (ev.connected) {
            evBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">Conectado (${escapeHtml(ev.state || 'OPEN')})</span>`;
        } else if (ev.configured) {
            evBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">Configurado (${escapeHtml(ev.state || 'LISTO')})</span>`;
        }

        box.innerHTML = `
            <div class="flex items-center justify-between">
                <span class="text-slate-400">Proveedor activo:</span>
                <strong class="text-white uppercase">${escapeHtml(data.provider || 'evolution_qr')}</strong>
            </div>
            <div class="flex items-center justify-between">
                <span class="text-slate-400">Evolution API:</span>
                ${evBadge}
            </div>
            <div class="flex items-center justify-between">
                <span class="text-slate-400">IAs activas:</span>
                <span class="text-emerald-300 font-semibold text-right">${escapeHtml(aiList)}</span>
            </div>
            ${ev.detail ? `<div class="text-[11px] text-amber-300 pt-1 border-t border-slate-800">${escapeHtml(ev.detail)}</div>` : ''}
        `;
    } catch (err) {
        box.innerHTML = `<div class="text-rose-400">${escapeHtml(err.message)}</div>`;
    }
}
window.cargarEstadoConexionWhatsapp = cargarEstadoConexionWhatsapp;

async function generarQrRealEvolution() {
    const qrBox = document.getElementById('waEvolutionQrBox');
    const qrStatus = document.getElementById('waEvolutionQrStatus');
    const qrWrap = document.getElementById('waEvolutionQrImgWrap');
    if (!qrBox || !qrStatus || !qrWrap) return;

    qrBox.classList.remove('hidden');
    qrStatus.textContent = 'Solicitando codigo QR real a Evolution API...';
    qrWrap.innerHTML = '<span class="text-xs text-slate-500 p-4"><i class="fa-solid fa-spinner fa-spin mr-1"></i> Generando...</span>';

    try {
        const res = await apiFetch('/api/v1/whatsapp/evolution/conectar-qr', { method: 'POST' });
        if (res.connected) {
            qrStatus.innerHTML = '<i class="fa-solid fa-circle-check text-emerald-400 mr-1"></i> WhatsApp conectado y listo para recibir pedidos reales.';
            qrWrap.innerHTML = '';
            qrWrap.classList.add('hidden');
            showToast('WhatsApp real ya esta conectado.');
            cargarEstadoConexionWhatsapp(true);
            return;
        }
        if (res.qr_base64) {
            qrWrap.classList.remove('hidden');
            qrStatus.textContent = 'Escanea con tu celular: WhatsApp -> Dispositivos vinculados -> Vincular';
            qrWrap.innerHTML = `<img src="${res.qr_base64}" alt="QR Real WhatsApp" class="w-48 h-48 object-contain">`;
            showToast('QR real generado. Escanealo con tu WhatsApp.');
        } else {
            qrStatus.textContent = res.detail || 'No se recibio QR.';
            qrWrap.innerHTML = '';
        }
    } catch (err) {
        qrStatus.innerHTML = `<span class="text-rose-400">${escapeHtml(err.message)}</span>`;
        qrWrap.innerHTML = '';
        showToast(err.message, true);
    }
}
window.generarQrRealEvolution = generarQrRealEvolution;

