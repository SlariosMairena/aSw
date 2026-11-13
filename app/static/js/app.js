// =========================================================
// PANEL SAAS WHATSAPP ORDERS - LOGICA PRINCIPAL
// =========================================================
let authToken = (function() {
    try {
        return localStorage.getItem('saas_jwt_token') || '';
    } catch (e) {
        return '';
    }
})();
let currentUser = null;
let activeTenantId = null;
let currentComercioData = null;
let activeChatPedido = null;

function setLocalToken(token) {
    authToken = token || '';
    try {
        if (authToken) localStorage.setItem('saas_jwt_token', authToken);
        else localStorage.removeItem('saas_jwt_token');
    } catch (e) {}
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

        function showToast(msg, isError = false) {
            const box = document.getElementById('toastBox');
            box.textContent = msg;
            box.className = `fixed bottom-5 right-5 z-50 max-w-md px-4 py-3 rounded-xl shadow-2xl border text-sm font-medium transition-all ${
                isError
                    ? 'bg-rose-950/95 border-rose-500 text-rose-200'
                    : 'bg-emerald-950/95 border-emerald-500 text-emerald-200'
            }`;
            box.classList.remove('hidden');
            setTimeout(() => box.classList.add('hidden'), 4200);
        }

        function playKitchenBell() {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(880, ctx.currentTime);
                osc.frequency.setValueAtTime(1174.66, ctx.currentTime + 0.15);
                gain.gain.setValueAtTime(0.25, ctx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.65);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.65);
                showToast('Sonido de alerta de cocina reproducido.');
            } catch (e) {
                showToast('Alerta visual activada.');
            }
        }

        function fillDemoLogin(email, pass) {
            document.getElementById('loginEmail').value = email;
            document.getElementById('loginPassword').value = pass;
            const form = document.querySelector('#loginSection form');
            if (form) {
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    handleLogin({ preventDefault: () => {} });
                }
            }
        }

        async function apiFetch(url, options = {}) {
            const headers = options.headers || {};
            if (authToken && !url.includes('/auth/login')) {
                headers['Authorization'] = `Bearer ${authToken}`;
            }
            if (options.body && typeof options.body === 'object') {
                headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify(options.body);
            }
            const res = await fetch(url, { ...options, headers });
            if (res.status === 401 && !url.includes('/auth/login')) {
                logout();
                throw new Error('Sesión expirada. Inicia sesión nuevamente.');
            }
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || 'Error en la solicitud');
            }
            return data;
        }

        async function handleLogin(e) {
            if (e && typeof e.preventDefault === 'function') e.preventDefault();
            const email = (document.getElementById('loginEmail').value || '').trim();
            const password = document.getElementById('loginPassword').value || '';
            const btnSubmit = document.querySelector('#loginSection form button[type="submit"]');
            const originalBtnText = btnSubmit ? btnSubmit.textContent : '';
            if (btnSubmit) {
                btnSubmit.disabled = true;
                btnSubmit.textContent = 'Ingresando...';
            }
            try {
                const data = await apiFetch('/api/v1/auth/login', {
                    method: 'POST',
                    body: { email, password }
                });
                authToken = data.access_token;
                setLocalToken(authToken);
                currentUser = data.user;
                showToast(`¡Bienvenido ${currentUser.nombre_completo}!`);
                await renderAppByRole();
            } catch (err) {
                showToast(err.message || 'Error al iniciar sesión', true);
            } finally {
                if (btnSubmit) {
                    btnSubmit.disabled = false;
                    btnSubmit.textContent = originalBtnText;
                }
            }
        }

        function logout() {
            setLocalToken('');
            currentUser = null;
            currentComercioData = null;
            activeTenantId = null;
            if (typeof saOverviewData !== 'undefined') saOverviewData = null;
            const sucSel = document.getElementById('filtroSucursalSelect');
            if (sucSel) {
                sucSel.disabled = false;
                sucSel.innerHTML = '<option value="">Todas las sucursales</option>';
            }
            document.getElementById('userHeaderBox').classList.add('hidden');
            document.getElementById('superAdminSection').classList.add('hidden');
            document.getElementById('comercioSection').classList.add('hidden');
            document.getElementById('loginSection').classList.remove('hidden');
        }

        function isComercioAdminOrSuper() {
            return Boolean(currentUser && (currentUser.rol === 'COMERCIO_ADMIN' || currentUser.rol === 'SUPER_ADMIN'));
        }
        window.isComercioAdminOrSuper = isComercioAdminOrSuper;

        function getGiroProfile(giroRaw) {
            const g = String(giroRaw || 'RESTAURANTE').toUpperCase().trim();
            if (g.includes('CAFETER') || g === 'CAFETERIA') {
                return {
                    code: 'CAFETERIA',
                    label: 'Cafetería / Repostería',
                    icon: 'fa-mug-hot',
                    tabMenuLabel: 'Menú de Bebidas y Repostería',
                    catalogTitle: 'Catálogo de Bebidas y Repostería',
                    itemSingular: 'Producto',
                    itemPlural: 'productos',
                    tabSucLabel: 'WhatsApp por Sucursal & Mesas',
                    soundLabel: 'Probar Aviso de Barra',
                    enProcesoLabel: '2. Preparando',
                    usesRemovableIngredients: true,
                    usesTables: true,
                    defaultCategories: ['Café', 'Leche Deslactosada', 'Leche de Almendra', 'Hielo', 'Canela'],
                    defaultExtras: [
                        { nombre: 'Tamaño Grande', tipo: 'VARIANTE', precio: 25.00 },
                        { nombre: 'Shot Extra Espresso', tipo: 'EXTRA', precio: 20.00 },
                        { nombre: 'Leche de Almendra', tipo: 'EXTRA', precio: 25.00 }
                    ]
                };
            }
            if (g.includes('FARMAC') || g === 'FARMACIA') {
                return {
                    code: 'FARMACIA',
                    label: 'Farmacia / Salud',
                    icon: 'fa-pills',
                    tabMenuLabel: 'Catálogo de Medicamentos',
                    catalogTitle: 'Catálogo Farmacéutico',
                    itemSingular: 'Medicamento',
                    itemPlural: 'productos',
                    tabSucLabel: 'WhatsApp por Sucursal & Retiros',
                    soundLabel: 'Probar Aviso Despacho',
                    enProcesoLabel: '2. Alistando',
                    usesRemovableIngredients: false,
                    usesTables: false,
                    defaultCategories: [],
                    defaultExtras: [
                        { nombre: 'Caja Completa', tipo: 'VARIANTE', precio: 120.00 },
                        { nombre: 'Blíster x10', tipo: 'VARIANTE', precio: 0.00 }
                    ]
                };
            }
            if (g.includes('TIENDA') || g.includes('RETAIL')) {
                return {
                    code: 'TIENDA',
                    label: 'Tienda / Retail',
                    icon: 'fa-bag-shopping',
                    tabMenuLabel: 'Catálogo de Productos',
                    catalogTitle: 'Catálogo de Tienda',
                    itemSingular: 'Artículo',
                    itemPlural: 'artículos',
                    tabSucLabel: 'WhatsApp por Sucursal & Tiendas',
                    soundLabel: 'Probar Aviso Pedido',
                    enProcesoLabel: '2. Empacando',
                    usesRemovableIngredients: false,
                    usesTables: false,
                    defaultCategories: [],
                    defaultExtras: [
                        { nombre: 'Talla L / Grande', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Empaque para Regalo', tipo: 'EXTRA', precio: 35.00 }
                    ]
                };
            }
            if (g.includes('SUPER') || g.includes('MINI')) {
                return {
                    code: 'SUPERMERCADO',
                    label: 'Minisúper / Conveniencia',
                    icon: 'fa-basket-shopping',
                    tabMenuLabel: 'Catálogo de Abarrotes',
                    catalogTitle: 'Catálogo del Minisúper',
                    itemSingular: 'Producto',
                    itemPlural: 'productos',
                    tabSucLabel: 'WhatsApp por Sucursal & Zonas',
                    soundLabel: 'Probar Aviso Surtido',
                    enProcesoLabel: '2. Surtiendo',
                    usesRemovableIngredients: false,
                    usesTables: false,
                    defaultCategories: [],
                    defaultExtras: [
                        { nombre: 'Presentación Familiar / Six-Pack', tipo: 'VARIANTE', precio: 80.00 },
                        { nombre: 'Bolsa Térmica / Hielo', tipo: 'EXTRA', precio: 25.00 }
                    ]
                };
            }
            return {
                code: 'RESTAURANTE',
                label: 'Restaurante / Comida Rápida',
                icon: 'fa-utensils',
                tabMenuLabel: 'Menú, Precios y Extras',
                catalogTitle: 'Catálogo del Menú',
                itemSingular: 'Plato',
                itemPlural: 'platos',
                tabSucLabel: 'WhatsApp por Sucursal & Mesas',
                soundLabel: 'Probar Sonido Cocina',
                enProcesoLabel: '2. En Cocina',
                usesRemovableIngredients: true,
                usesTables: true,
                defaultCategories: ['Cebolla', 'Tomate', 'Lechuga', 'Mayonesa', 'Pepinillos', 'Mostaza', 'Salsa BBQ', 'Picante', 'Queso', 'Aguacate'],
                defaultExtras: [
                    { nombre: 'Extra Queso', tipo: 'EXTRA', precio: 30.00 },
                    { nombre: 'Porción Extra', tipo: 'EXTRA', precio: 40.00 },
                    { nombre: 'Tamaño Grande', tipo: 'VARIANTE', precio: 60.00 }
                ]
            };
        }

        function applyComercioRoleVisibility() {
            const isAdmin = isComercioAdminOrSuper();
            document.querySelectorAll('.comercio-admin-only').forEach(el => {
                el.classList.toggle('hidden', !isAdmin);
            });
            const menuCatCol = document.getElementById('menuCatalogoCol');
            if (menuCatCol) {
                menuCatCol.classList.toggle('xl:col-span-7', isAdmin);
                menuCatCol.classList.toggle('xl:col-span-12', !isAdmin);
            }
            const badgeRole = document.getElementById('userBadgeRole');
            if (badgeRole && currentUser) {
                if (currentUser.rol === 'COMERCIO_ADMIN') badgeRole.textContent = 'ADMIN (DUEÑO)';
                else if (currentUser.rol === 'SUCURSAL_STAFF') badgeRole.textContent = 'STAFF (OPERATIVO)';
                else badgeRole.textContent = currentUser.rol;
            }
        }

        function applyGiroAdaptation(comercio) {
            const profile = getGiroProfile(comercio?.giro);
            const giroBadge = document.getElementById('comercioGiroBadge');
            if (giroBadge) giroBadge.innerHTML = `<i class="fa-solid ${profile.icon} mr-1"></i>${profile.label}`;

            const tabMenuLbl = document.getElementById('tabLabel_menu');
            if (tabMenuLbl) tabMenuLbl.textContent = profile.tabMenuLabel;
            const tabMenuIcon = document.getElementById('tabIcon_menu');
            if (tabMenuIcon) tabMenuIcon.className = `fa-solid ${profile.icon} pointer-events-none`;

            const tabSucLbl = document.getElementById('tabLabel_sucursales');
            if (tabSucLbl) tabSucLbl.textContent = profile.tabSucLabel;
            const soundLbl = document.getElementById('btnSoundLabel');
            if (soundLbl) soundLbl.textContent = profile.soundLabel;

            const catTitle = document.getElementById('catalogTitleText');
            if (catTitle) catTitle.textContent = profile.catalogTitle;
            const catIcon = document.getElementById('catalogTitleIcon');
            if (catIcon) catIcon.className = `fa-solid ${profile.icon} text-indigo-400`;

            const lblProd = document.getElementById('lblNombreProductoMenu');
            if (lblProd) lblProd.textContent = `Nombre del ${profile.itemSingular}`;
            if (!document.getElementById('platoEditId')?.value) {
                const formTitle = document.getElementById('formPlatoTitle');
                if (formTitle) formTitle.textContent = `Nuevo ${profile.itemSingular}`;
                const btnSub = document.getElementById('btnSubmitPlatoText');
                if (btnSub) btnSub.textContent = `Guardar ${profile.itemSingular} en Catálogo`;
            }

            const bloqueIng = document.getElementById('bloqueIngBaseForm');
            if (bloqueIng) bloqueIng.classList.toggle('hidden', !profile.usesRemovableIngredients);
            const bloqueMesas = document.getElementById('bloqueReservacionMesas');
            if (bloqueMesas) bloqueMesas.classList.toggle('hidden', !profile.usesTables);
        }

        async function renderAppByRole() {
            document.getElementById('loginSection').classList.add('hidden');
            const headerBox = document.getElementById('userHeaderBox');
            headerBox.classList.remove('hidden');
            headerBox.classList.add('flex');
            document.getElementById('userBadgeName').textContent = currentUser.nombre_completo;
            applyComercioRoleVisibility();

            if (currentUser.rol === 'SUPER_ADMIN') {
                document.getElementById('btnVolverSuperAdmin').classList.add('hidden');
                document.getElementById('comercioSection').classList.add('hidden');
                document.getElementById('superAdminSection').classList.remove('hidden');
                await loadSuperAdminDashboard();
            } else {
                activeTenantId = currentUser.tenant_id;
                document.getElementById('superAdminSection').classList.add('hidden');
                document.getElementById('comercioSection').classList.remove('hidden');
                switchComercioTab('pedidos');
                await reloadComercioDashboard(true);
            }
        }

        async function inspectComercioAsSuperAdmin(tenantId) {
            activeTenantId = tenantId;
            document.getElementById('superAdminSection').classList.add('hidden');
            document.getElementById('comercioSection').classList.remove('hidden');
            document.getElementById('btnVolverSuperAdmin').classList.remove('hidden');
            applyComercioRoleVisibility();
            switchComercioTab('pedidos');
            await reloadComercioDashboard(true);
        }

        async function switchBackToSuperAdmin() {
            document.getElementById('btnVolverSuperAdmin').classList.add('hidden');
            document.getElementById('comercioSection').classList.add('hidden');
            document.getElementById('superAdminSection').classList.remove('hidden');
            await loadSuperAdminDashboard();
        }

        // =========================================================
        // PANEL SUPER_ADMIN (implementado en /static/js/superadmin.js)
        // =========================================================

        // =========================================================
        // CARGA DEL PANEL DEL RESTAURANTE / COCINA
        // =========================================================
        function switchComercioTab(tabName) {
            if (tabName === 'estadisticas' && !isComercioAdminOrSuper()) {
                tabName = 'pedidos';
            }
            console.log('[switchComercioTab] Activando pestaña:', tabName);
            const tabs = ['pedidos', 'menu', 'inventario', 'sucursales', 'estadisticas'];
            
            tabs.forEach(t => {
                const el = document.getElementById(`tabContent_${t}`);
                if (el) {
                    if (t === tabName) {
                        el.classList.remove('hidden');
                    } else {
                        el.classList.add('hidden');
                    }
                }
                const btn = document.getElementById(`tabBtn_${t}`);
                if (btn) {
                    const adminOnlyCls = btn.classList.contains('comercio-admin-only') ? ' comercio-admin-only' : '';
                    if (t === tabName) {
                        btn.className = 'px-4 py-2 rounded-xl text-sm font-bold bg-emerald-500 text-slate-950 transition flex items-center gap-1.5 shadow-md shadow-emerald-500/20 cursor-pointer' + adminOnlyCls;
                    } else {
                        btn.className = 'px-4 py-2 rounded-xl text-sm font-semibold bg-slate-800 text-slate-300 hover:bg-slate-700 transition flex items-center gap-1.5 cursor-pointer' + adminOnlyCls;
                    }
                }
            });
            applyComercioRoleVisibility();

            if (tabName === 'inventario') {
                if (typeof cargarInventario === 'function') {
                    try {
                        cargarInventario();
                    } catch (err) {
                        console.error('[switchComercioTab] Error ejecutando cargarInventario:', err);
                    }
                }
            } else if (tabName === 'estadisticas') {
                if (typeof cargarEstadisticasComercio === 'function') {
                    cargarEstadisticasComercio();
                }
            }
        }
        window.switchComercioTab = switchComercioTab;

        async function reloadComercioDashboard(populateFilter = false) {
            const sucursalFilter = document.getElementById('filtroSucursalSelect').value;
            const query = sucursalFilter ? `?sucursal_id=${sucursalFilter}` : '';
            try {
                const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/dashboard${query}`);
                currentComercioData = data;
                const c = data.comercio;
                const sym = c.simbolo_moneda || 'C$';
                const profile = getGiroProfile(c.giro);

                document.getElementById('comercioTitle').innerHTML = `<i class="fa-solid ${profile.icon} text-emerald-400 mr-2"></i>${escapeHtml(c.nombre_comercial)}`;
                document.getElementById('comercioPlanBadge').textContent = `${c.plan_nombre} • ${c.estado_suscripcion}`;
                document.getElementById('comercioSubInfo').textContent = isComercioAdminOrSuper()
                    ? `Pedidos del mes: ${c.pedidos_mes_actual} • Datos transferencia Modo 1: ${c.datos_bancarios_texto}`
                    : `Vista operativa de sucursal • Pedidos activos y control de disponibilidad`;

                applyGiroAdaptation(c);
                applyComercioRoleVisibility();

                const badge = document.getElementById('headerMonedaBadge');
                if (badge) badge.textContent = `${sym} (${c.moneda || 'NIO'})`;
                document.querySelectorAll('.currencySymbolSpan').forEach(el => el.textContent = sym);

                if (populateFilter) {
                    const sel = document.getElementById('filtroSucursalSelect');
                    if (currentUser && currentUser.rol === 'SUCURSAL_STAFF' && currentUser.sucursal_id) {
                        sel.innerHTML = data.sucursales.map(s => `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)} [${escapeHtml(s.codigo_whatsapp)}]</option>`).join('');
                        sel.value = String(currentUser.sucursal_id);
                        sel.disabled = true;
                    } else {
                        sel.disabled = false;
                        sel.innerHTML = `<option value="">Todas las sucursales (${data.sucursales.length})</option>` +
                            data.sucursales.map(s => `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)} [${escapeHtml(s.codigo_whatsapp)}]</option>`).join('');
                    }
                }

                renderKanbanPedidos(data.pedidos, sym);
                renderMenuPlatos(data.platos, sym);
                renderSucursalesYMesas(data.sucursales, c);
                renderIngredientBuilderUI();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        function renderKanbanPedidos(pedidos, moneda) {
            const recibidos = pedidos.filter(p => p.estado_pedido === 'RECIBIDO');
            const enProceso = pedidos.filter(p => p.estado_pedido === 'EN_PROCESO');
            const listos = pedidos.filter(p => p.estado_pedido === 'LISTO' || p.estado_pedido === 'ENTREGADO');

            document.getElementById('countRecibido').textContent = recibidos.length;
            document.getElementById('countEnProceso').textContent = enProceso.length;
            document.getElementById('countListo').textContent = listos.length;

            document.getElementById('colRecibido').innerHTML = recibidos.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Sin pedidos pendientes en esta columna</p>`;
            document.getElementById('colEnProceso').innerHTML = enProceso.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Ningún pedido en preparación</p>`;
            document.getElementById('colListo').innerHTML = listos.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Ningún pedido marcado como listo</p>`;
        }

        function buildPedidoCardHTML(p, moneda) {
            const itemsHTML = p.items.map(it => `
                <div class="p-2 rounded-lg bg-slate-950/80 border border-slate-800 text-xs">
                    <div class="flex justify-between font-bold text-white">
                        <span>${it.cantidad}x ${it.nombre_plato}</span>
                        <span>${moneda}${it.subtotal_linea.toFixed(2)}</span>
                    </div>
                    ${it.sin_ingredientes && it.sin_ingredientes.length > 0 ? `
                        <div class="mt-1">
                            <span class="inline-block px-2 py-0.5 rounded bg-rose-500/25 border border-rose-500/50 text-rose-200 font-bold text-[11px]">
                                <i class="fa-solid fa-circle-xmark mr-1"></i> SIN: ${it.sin_ingredientes.join(', ')}
                            </span>
                        </div>
                    ` : ''}
                    ${it.extras && it.extras.length > 0 ? `
                        <div class="mt-1">
                            ${it.extras.map(ex => `
                                <span class="inline-block mr-1 px-2 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 font-semibold text-[11px]">
                                    <i class="fa-solid fa-circle-plus mr-1"></i> ${ex.nombre} (+${moneda}${Number(ex.precio).toFixed(2)})
                                </span>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
            `).join('');

            const chatModeBadge = p.modo_atencion_chat === 'HUMANO_ACTIVO'
                ? `<span class="px-2 py-0.5 rounded bg-amber-500/25 text-amber-300 border border-amber-500/40 text-[10px] font-bold"><i class="fa-solid fa-hand mr-1"></i> CONTROL HUMANO ACTIVO</span>`
                : `<span class="px-2 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-bold"><i class="fa-solid fa-robot mr-1"></i> BOT ACTIVO</span>`;

            return `
                <div id="cardPedido_${p.id}" class="p-4 rounded-xl bg-slate-900 border border-slate-700/80 space-y-3 shadow-lg transition-all duration-300 relative overflow-hidden group">
                    <div class="flex items-center justify-between">
                        <div>
                            <span class="font-extrabold text-base text-white">#${p.folio}</span>
                            <span class="text-[11px] text-slate-400 ml-1">• ${p.sucursal_nombre}</span>
                        </div>
                        ${chatModeBadge}
                    </div>

                    <div class="text-xs text-slate-300 space-y-0.5">
                        <div><i class="fa-solid fa-user text-slate-400 mr-1 text-[11px]"></i> <strong>${p.nombre_cliente}</strong> (+${p.telefono_cliente})</div>
                        <div><i class="fa-solid fa-motorcycle text-slate-400 mr-1 text-[11px]"></i> <strong>${p.tipo_entrega}:</strong> ${p.direccion_entrega || 'Recoger en sucursal'}</div>
                        ${p.google_maps_link ? `
                            <div>
                                <a href="${p.google_maps_link}" target="_blank" class="text-sky-400 hover:underline font-semibold">
                                    <i class="fa-solid fa-location-dot text-rose-400 mr-1"></i> Abrir Pin GPS de WhatsApp en Google Maps <i class="fa-solid fa-arrow-right text-[10px] ml-1"></i>
                                </a>
                            </div>
                        ` : ''}
                    </div>

                    <div class="space-y-1.5">${itemsHTML}</div>

                    <!-- Resumen del Chat con IA directo en la tarjeta -->
                    <div class="p-2.5 rounded-lg bg-indigo-950/40 border border-indigo-500/30 text-[11px] text-indigo-200">
                        <strong><i class="fa-solid fa-wand-magic-sparkles text-indigo-400 mr-1"></i> Resumen del Chat (IA):</strong> ${p.resumen_chat_ia}
                    </div>

                    <div class="flex items-center justify-between pt-2 border-t border-slate-800 text-xs">
                        <div>
                            <div class="font-bold text-slate-300">Pago: ${p.metodo_pago}</div>
                            ${p.paga_con_monto ? `<div class="text-[11px] text-amber-300">Paga con ${moneda}${p.paga_con_monto} | Cambio: ${moneda}${p.cambio_a_entregar}</div>` : ''}
                        </div>
                        <div class="text-right">
                            <div class="text-[11px] text-slate-400">Total (+Envío ${moneda}${p.costo_envio})</div>
                            <div class="text-base font-extrabold text-emerald-400">${moneda}${p.total.toFixed(2)}</div>
                        </div>
                    </div>

                    <!-- Botones para cambiar estado del pedido (notifica al WhatsApp del cliente) -->
                    <div class="grid grid-cols-3 gap-1.5 pt-1">
                        <button onclick="cambiarEstadoPedido(${p.id}, 'RECIBIDO')"
                            class="py-1.5 rounded-lg text-[11px] font-bold border ${p.estado_pedido === 'RECIBIDO' ? 'bg-sky-500 text-slate-950 border-sky-400' : 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700'}">
                            <i class="fa-solid fa-inbox mr-1"></i> Recibido
                        </button>
                        <button onclick="cambiarEstadoPedido(${p.id}, 'EN_PROCESO')"
                            class="py-1.5 rounded-lg text-[11px] font-bold border ${p.estado_pedido === 'EN_PROCESO' ? 'bg-amber-500 text-slate-950 border-amber-400' : 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700'}">
                            <i class="fa-solid fa-fire-burner mr-1"></i> En Proceso
                        </button>
                        <button onclick="cambiarEstadoPedido(${p.id}, 'LISTO')"
                            class="py-1.5 rounded-lg text-[11px] font-bold border ${p.estado_pedido === 'LISTO' ? 'bg-emerald-500 text-slate-950 border-emerald-400' : 'bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-700'}">
                            <i class="fa-solid fa-circle-check mr-1"></i> Listo
                        </button>
                    </div>

                    <!-- Botones de Comanda Térmica, Pago Online y Control del Chat -->
                    <div class="flex flex-wrap gap-2 pt-1">
                        <button onclick="openChatModal(${p.id})"
                            class="flex-1 py-2 px-3 rounded-xl bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/40 text-indigo-200 font-bold text-xs transition whitespace-nowrap">
                            <i class="fa-solid fa-comments mr-1"></i> Chat
                        </button>
                        ${p.estado_pago === 'PAGADO' ? `
                            <span class="py-2 px-3 rounded-xl bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold text-xs flex items-center gap-1.5">
                                <i class="fa-solid fa-circle-check"></i> Pagado Online
                            </span>
                        ` : `
                            <button onclick="iniciarCheckoutSimulado(${p.id})"
                                title="Abrir link de pago online simulado de este pedido"
                                class="py-2 px-3 rounded-xl bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/35 text-emerald-300 font-bold text-xs transition flex items-center gap-1.5">
                                <i class="fa-solid fa-credit-card"></i> Pagar Online
                            </button>
                        `}
                        <a href="/ticket/${p.id}" target="_blank"
                            class="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 font-bold text-xs transition flex items-center gap-1">
                            <i class="fa-solid fa-print mr-1"></i> Ticket
                        </a>
                    </div>
                </div>
            `;
        }

        async function iniciarCheckoutSimulado(pedidoId) {
            try {
                const res = await apiFetch(`/api/v1/pagos/checkout-link/${pedidoId}`, {
                    method: 'POST'
                });
                if (res && res.checkout_url) {
                    window.open(res.checkout_url, '_blank');
                    showToast('✓ Ventana de pago online (Simulador Modo 2) abierta');
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function cambiarEstadoPedido(pedidoId, nuevoEstado) {
            const card = document.getElementById(`cardPedido_${pedidoId}`);
            let overlay = null;
            if (card) {
                card.style.pointerEvents = 'none';
                overlay = document.createElement('div');
                overlay.className = 'absolute inset-0 bg-slate-950/85 backdrop-blur-[2px] flex flex-col items-center justify-center gap-2 z-10 transition-all duration-200';
                
                let iconColor = 'text-sky-400';
                let stateLabel = '1. Recibido';
                if (nuevoEstado === 'EN_PROCESO') {
                    iconColor = 'text-amber-400';
                    stateLabel = '2. En Cocina';
                } else if (nuevoEstado === 'LISTO') {
                    iconColor = 'text-emerald-400';
                    stateLabel = '3. Listo';
                }

                overlay.innerHTML = `
                    <div class="w-10 h-10 rounded-full bg-slate-900 border border-slate-700 flex items-center justify-center shadow-lg">
                        <i class="fa-solid fa-spinner fa-spin ${iconColor} text-lg"></i>
                    </div>
                    <span class="text-xs font-bold text-white tracking-wide">Moviendo a ${stateLabel}...</span>
                    <span class="text-[10px] text-slate-400">Actualizando pedido & aviso WhatsApp...</span>
                `;
                card.appendChild(overlay);
            }

            try {
                const res = await apiFetch(`/api/v1/panel/comercio/pedidos/${pedidoId}/estado`, {
                    method: 'PATCH',
                    body: { nuevo_estado: nuevoEstado }
                });
                showToast(`✓ Estado actualizado a ${nuevoEstado}. Notificación enviada a WhatsApp.`);
                await reloadComercioDashboard();
            } catch (err) {
                if (overlay && card) {
                    overlay.remove();
                    card.style.pointerEvents = 'auto';
                }
                showToast(err.message, true);
            }
        }

        function openChatModal(pedidoId) {
            const p = currentComercioData.pedidos.find(x => x.id === pedidoId);
            if (!p) return;
            activeChatPedido = p;

            document.getElementById('modalChatTitle').textContent = `Conversación de WhatsApp — Pedido #${p.folio} (${p.nombre_cliente})`;
            document.getElementById('modalChatSubtitle').textContent = `Teléfono: +${p.telefono_cliente} • Sucursal: ${p.sucursal_nombre}`;
            document.getElementById('modalResumenIA').textContent = p.resumen_chat_ia;
            document.getElementById('modalComoSeRealizo').textContent = p.como_se_realizo_pedido;

            renderChatModalMessages(p);
            document.getElementById('chatModal').classList.remove('hidden');
        }

        function renderChatModalMessages(p) {
            const modeBadge = document.getElementById('modalChatModeBadge');
            if (p.modo_atencion_chat === 'HUMANO_ACTIVO') {
                modeBadge.innerHTML = `<span class="text-amber-400"><i class="fa-solid fa-hand mr-1"></i> HUMANO ACTIVO (El bot está pausado para este chat)</span>`;
            } else {
                modeBadge.innerHTML = `<span class="text-emerald-400"><i class="fa-solid fa-robot mr-1"></i> BOT ACTIVO (Respondiendo automáticamente en WhatsApp)</span>`;
            }

            const list = document.getElementById('modalMessagesList');
            list.innerHTML = (p.mensajes_chat || []).map(m => {
                const isClient = m.remitente === 'CLIENTE';
                const isHuman = m.remitente === 'HUMANO_COMERCIO';
                return `
                    <div class="flex ${isClient ? 'justify-start' : 'justify-end'}">
                        <div class="max-w-[82%] p-2.5 rounded-xl ${
                            isClient
                                ? 'bg-slate-800 text-slate-100'
                                : isHuman
                                    ? 'bg-amber-500/25 border border-amber-500/40 text-amber-100'
                                    : 'bg-emerald-500/20 border border-emerald-500/30 text-emerald-100'
                        }">
                            <div class="text-[10px] font-bold opacity-75 mb-0.5">
                                ${isClient ? '<i class="fa-solid fa-user mr-1"></i> Cliente' : isHuman ? '<i class="fa-solid fa-user-tie mr-1"></i> Restaurante' : '<i class="fa-solid fa-robot mr-1"></i> Bot WhatsApp'} • ${m.hora}
                            </div>
                            <div class="whitespace-pre-line">${m.contenido}</div>
                        </div>
                    </div>
                `;
            }).join('');
            list.scrollTop = list.scrollHeight;
        }

        function closeChatModal() {
            document.getElementById('chatModal').classList.add('hidden');
            activeChatPedido = null;
        }

        async function toggleChatControl(nuevoModo) {
            if (!activeChatPedido || !activeChatPedido.sesion_chat_id) return;
            try {
                await apiFetch(`/api/v1/panel/comercio/chats/${activeChatPedido.sesion_chat_id}/control`, {
                    method: 'POST',
                    body: { modo_atencion: nuevoModo }
                });
                showToast(
                    nuevoModo === 'HUMANO_ACTIVO'
                        ? 'Tomaste el control de este chat. El bot se pausó únicamente para este cliente.'
                        : 'Bot reactivado para este cliente.'
                );
                await reloadComercioDashboard();
                const updated = currentComercioData.pedidos.find(x => x.id === activeChatPedido.id);
                if (updated) {
                    activeChatPedido = updated;
                    renderChatModalMessages(updated);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function handleSendHumanMessage(e) {
            e.preventDefault();
            if (!activeChatPedido || !activeChatPedido.sesion_chat_id) return;
            const input = document.getElementById('inputHumanMessage');
            const mensaje = input.value.trim();
            if (!mensaje) return;

            try {
                await apiFetch(`/api/v1/panel/comercio/chats/${activeChatPedido.sesion_chat_id}/mensaje-humano`, {
                    method: 'POST',
                    body: { mensaje }
                });
                input.value = '';
                showToast('Mensaje enviado al WhatsApp del cliente (Control Humano Activo).');
                await reloadComercioDashboard();
                const updated = currentComercioData.pedidos.find(x => x.id === activeChatPedido.id);
                if (updated) {
                    activeChatPedido = updated;
                    renderChatModalMessages(updated);
                }
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // MENÚ E INGREDIENTES + EDITAR / ELIMINAR / "AGOTADO HOY"
        // =========================================================
        let allPlatosCache = [];
        let activeCatalogSearch = '';
        let activeCatalogCategory = '';

        function renderMenuPlatos(platos, moneda) {
            allPlatosCache = platos || [];
            
            // Poblar selector de categorías
            const categories = [...new Set(allPlatosCache.map(p => p.categoria || 'General'))].filter(Boolean);
            const catSelect = document.getElementById('filterCategoryMenuSelect');
            if (catSelect) {
                const currentVal = catSelect.value;
                catSelect.innerHTML = `<option value="">Todas las categorías (${categories.length})</option>` +
                    categories.map(c => `<option value="${c}" ${c === currentVal ? 'selected' : ''}>${c}</option>`).join('');
            }

            // Estadísticas rápidas
            const total = allPlatosCache.length;
            const disponibles = allPlatosCache.filter(p => p.disponible_global).length;
            const agotados = total - disponibles;

            const badgeTotal = document.getElementById('catalogTotalCountBadge');
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            if (badgeTotal) badgeTotal.textContent = `${total} ${profile.itemPlural}`;
            const elDisp = document.getElementById('statDisponiblesCount');
            if (elDisp) elDisp.textContent = disponibles;
            const elAgot = document.getElementById('statAgotadosCount');
            if (elAgot) elAgot.textContent = agotados;
            const elCat = document.getElementById('statCategoriasCount');
            if (elCat) elCat.textContent = categories.length;

            filterCatalogCards(moneda);
        }

        function filterCatalogCards(moneda = null) {
            const sym = moneda || (currentComercioData && currentComercioData.comercio ? currentComercioData.comercio.simbolo_moneda : '$');
            const searchInput = document.getElementById('searchMenuInput');
            const catSelect = document.getElementById('filterCategoryMenuSelect');
            
            const q = (searchInput ? searchInput.value : '').toLowerCase().trim();
            const cat = catSelect ? catSelect.value : '';

            const filtered = allPlatosCache.filter(pl => {
                const matchQ = !q || (pl.nombre || '').toLowerCase().includes(q) || (pl.descripcion || '').toLowerCase().includes(q);
                const matchCat = !cat || (pl.categoria || '') === cat;
                return matchQ && matchCat;
            });

            const grid = document.getElementById('menuPlatosGrid');
            if (!grid) return;

            if (filtered.length === 0) {
                grid.innerHTML = `
                    <div class="p-8 text-center rounded-2xl bg-slate-900/40 border border-slate-800">
                        <i class="fa-solid fa-magnifying-glass text-3xl block mb-2 text-slate-600"></i>
                        <p class="text-sm font-semibold text-slate-300">No se encontraron platos</p>
                        <p class="text-xs text-slate-500 mt-1">Prueba con otro término de búsqueda o agrega un nuevo plato a la izquierda.</p>
                    </div>
                `;
                return;
            }

            const esAdmin = isComercioAdminOrSuper();
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);

            grid.innerHTML = filtered.map(pl => {
                const isAgotado = !pl.disponible_global;
                return `
                    <div class="p-4 rounded-2xl transition-all duration-200 border ${
                        isAgotado
                            ? 'bg-slate-950/70 border-rose-900/40 opacity-80'
                            : 'bg-slate-900/90 hover:bg-slate-900 border-slate-800 hover:border-slate-700 shadow-md'
                    }">
                        <div class="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                            <!-- Datos principales del producto -->
                            <div class="space-y-2 flex-1">
                                <div class="flex flex-wrap items-center gap-2">
                                    <span class="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-md bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">
                                        ${escapeHtml(pl.categoria || 'General')}
                                    </span>
                                    <h4 class="font-bold text-base text-white">${escapeHtml(pl.nombre)}</h4>
                                    ${esAdmin ? `
                                        <button type="button" onclick="editarPrecioRapidoPlato(${pl.id}, ${pl.precio_base}, '${pl.nombre.replace(/'/g, "\\'")}', '${sym}')"
                                            title="Clic para editar el precio rápidamente"
                                            class="text-sm font-extrabold text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/25 px-2.5 py-0.5 rounded-lg border border-emerald-500/30 transition flex items-center gap-1 group cursor-pointer shadow-sm">
                                            <span>${sym}${pl.precio_base.toFixed(2)}</span>
                                            <span class="text-[10px] text-emerald-300 opacity-60 group-hover:opacity-100"><i class="fa-solid fa-pen-to-square"></i></span>
                                        </button>
                                    ` : `
                                        <span class="text-sm font-extrabold text-emerald-400 bg-emerald-500/10 px-2.5 py-0.5 rounded-lg border border-emerald-500/30">
                                            ${sym}${pl.precio_base.toFixed(2)}
                                        </span>
                                    `}
                                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${
                                        pl.disponible_global
                                            ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                            : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                    }">
                                        ${pl.disponible_global ? '<i class="fa-solid fa-circle text-[8px] mr-1 text-emerald-400"></i> DISPONIBLE' : '<i class="fa-solid fa-ban text-[9px] mr-1 text-rose-400"></i> AGOTADO HOY'}
                                    </span>
                                </div>

                                ${pl.descripcion ? `<p class="text-xs text-slate-400 leading-relaxed">${escapeHtml(pl.descripcion)}</p>` : ''}

                                ${profile.usesRemovableIngredients ? `
                                <!-- Bloque 1: Ingredientes base removibles (C$ 0) -->
                                <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                                    <div class="flex items-center justify-between text-[11px]">
                                        <span class="text-emerald-300 font-semibold flex items-center gap-1">
                                            <span><i class="fa-solid fa-leaf text-emerald-400 mr-1"></i></span> Ingredientes Base Removibles (${sym}0 en WhatsApp):
                                        </span>
                                        <span class="text-[10px] text-slate-500">Toca para agotar/habilitar</span>
                                    </div>
                                    <div class="flex flex-wrap gap-1.5">
                                        ${(pl.ingredientes_removibles || []).length > 0 ? (pl.ingredientes_removibles || []).map(ing => `
                                            <button type="button" onclick="toggleIngredienteDisponibilidad(${ing.id})"
                                                title="Clic para alternar entre Disponible y Agotado"
                                                class="text-[11px] px-2.5 py-1 rounded-lg border font-medium transition flex items-center gap-1 ${
                                                    ing.disponible
                                                        ? 'bg-slate-800/90 hover:bg-slate-700 text-slate-200 border-slate-700 shadow-sm'
                                                        : 'bg-rose-950/60 text-rose-300 border-rose-500/50 line-through opacity-75'
                                                }">
                                                <span>${ing.disponible ? '<i class="fa-solid fa-check text-[9px] text-emerald-400"></i>' : '<i class="fa-solid fa-xmark text-[9px] text-rose-400"></i>'}</span>
                                                <span>${escapeHtml(ing.nombre)}</span>
                                                ${!ing.disponible ? '<span class="text-[9px] font-bold uppercase ml-0.5 text-rose-400">(Agotado)</span>' : ''}
                                            </button>
                                        `).join('') : '<span class="text-[11px] text-slate-500 italic">Sin ingredientes base definidos</span>'}
                                    </div>
                                </div>
                                ` : ''}

                                <!-- Bloque 2: Extras y Variantes con precio -->
                                <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800/80 space-y-1.5">
                                    <div class="flex items-center justify-between text-[11px]">
                                        <span class="text-amber-300 font-semibold flex items-center gap-1">
                                            <span><i class="fa-solid fa-circle-plus text-amber-400 mr-1"></i></span> ${profile.usesRemovableIngredients ? 'Extras y Variantes con Costo:' : 'Presentaciones, Tallas o Variantes:'}
                                        </span>
                                        <span class="text-[10px] text-slate-500">Toca para agotar/habilitar</span>
                                    </div>
                                    <div class="flex flex-wrap gap-1.5">
                                        ${(pl.extras_y_variantes || []).length > 0 ? (pl.extras_y_variantes || []).map(ex => `
                                            <div class="inline-flex items-center rounded-lg border transition ${
                                                ex.disponible
                                                    ? 'bg-amber-500/10 text-amber-200 border-amber-500/30'
                                                    : 'bg-rose-950/60 text-rose-300 border-rose-500/50 line-through opacity-75'
                                            }">
                                                <button type="button" onclick="toggleIngredienteDisponibilidad(${ex.id})"
                                                    title="Clic para alternar entre Disponible y Agotado"
                                                    class="text-[11px] px-2 py-1 font-medium hover:bg-amber-500/20 ${esAdmin ? 'rounded-l-lg' : 'rounded-lg'} flex items-center gap-1">
                                                    <span>${ex.disponible ? '<i class="fa-solid fa-plus text-[9px] text-amber-400"></i>' : '<i class="fa-solid fa-xmark text-[9px] text-rose-400"></i>'}</span>
                                                    <span>${escapeHtml(ex.nombre)}</span>
                                                    <span class="font-bold text-emerald-400">(+${sym}${ex.precio_adicional.toFixed(2)})</span>
                                                    ${!ex.disponible ? '<span class="text-[9px] font-bold uppercase ml-0.5 text-rose-400">(Agotado)</span>' : ''}
                                                </button>
                                                ${esAdmin ? `
                                                    <button type="button" onclick="editarPrecioRapidoExtra(${ex.id}, ${ex.precio_adicional}, '${ex.nombre.replace(/'/g, "\\'")}', '${sym}')"
                                                        title="Cambiar precio de esta opción"
                                                        class="px-1.5 py-1 text-[11px] text-amber-300 hover:text-white hover:bg-amber-500/30 border-l border-amber-500/20 rounded-r-lg transition font-bold">
                                                        <i class="fa-solid fa-pen-to-square text-[10px]"></i>
                                                    </button>
                                                ` : ''}
                                            </div>
                                        `).join('') : '<span class="text-[11px] text-slate-500 italic">Sin variantes configuradas</span>'}
                                    </div>
                                </div>
                            </div>

                            <!-- Botones de Acción Rápida -->
                            <div class="flex lg:flex-col gap-2 shrink-0 pt-1 lg:pt-0">
                                ${esAdmin ? `
                                    <button onclick="cargarPlatoEnFormularioParaEditar(${pl.id})"
                                        class="flex-1 lg:flex-none px-3.5 py-2 rounded-xl text-xs font-bold bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-200 border border-indigo-500/40 transition flex items-center justify-center gap-1.5">
                                        <span><i class="fa-solid fa-pen-to-square mr-1"></i></span> <span>Editar</span>
                                    </button>
                                ` : ''}
                                <button onclick="togglePlatoDisponibilidad(${pl.id})"
                                    class="flex-1 lg:flex-none px-3.5 py-2 rounded-xl text-xs font-bold transition flex items-center justify-center gap-1.5 ${
                                        pl.disponible_global
                                            ? 'bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/40'
                                            : 'bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-extrabold shadow-md'
                                    }">
                                    <span><i class="fa-solid fa-${pl.disponible_global ? 'pause' : 'play'} mr-1"></i></span>
                                    <span>${pl.disponible_global ? 'Agotar Hoy' : 'Habilitar'}</span>
                                </button>
                                ${esAdmin ? `
                                    <button onclick="eliminarPlato(${pl.id})"
                                        class="px-3 py-2 rounded-xl text-xs font-bold bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30 transition flex items-center justify-center gap-1">
                                        <span><i class="fa-solid fa-trash mr-1"></i></span> <span class="hidden sm:inline">Borrar</span>
                                    </button>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        // =========================================================
        // CONSTRUCTOR DINÁMICO DE INGREDIENTES Y EXTRAS
        // (Adaptado dinámicamente según el giro/catálogo de cada comercio)
        // =========================================================
        const FALLBACK_INGREDIENTS = [
            'Cebolla', 'Tomate', 'Lechuga', 'Mayonesa', 'Pepinillos',
            'Mostaza', 'Salsa BBQ', 'Picante', 'Queso', 'Aguacate'
        ];
        const FALLBACK_EXTRAS = [
            { nombre: 'Extra Queso', tipo: 'EXTRA', precio: 30.00 },
            { nombre: 'Porción Extra', tipo: 'EXTRA', precio: 40.00 },
            { nombre: 'Tamaño Grande', tipo: 'VARIANTE', precio: 60.00 }
        ];

        let formIngredientesBaseList = ['Cebolla', 'Tomate', 'Lechuga', 'Mayonesa'];
        let formExtrasList = [
            { nombre: 'Extra Tocino', tipo: 'EXTRA', precio_adicional: 40.00 },
            { nombre: 'Extra Queso', tipo: 'EXTRA', precio_adicional: 30.00 }
        ];

        // Obtiene ingredientes base y extras únicos ya utilizados en el catálogo del local
        function getComercioIngredientPresets() {
            const platos = (currentComercioData && currentComercioData.platos) ? currentComercioData.platos : [];
            const ingBaseSet = new Set();
            const extrasMap = new Map();

            platos.forEach(p => {
                (p.ingredientes_removibles || []).forEach(i => {
                    if (i.nombre) ingBaseSet.add(i.nombre.trim());
                });
                (p.extras_y_variantes || []).forEach(e => {
                    if (e.nombre && !extrasMap.has(e.nombre.toLowerCase().trim())) {
                        extrasMap.set(e.nombre.toLowerCase().trim(), {
                            nombre: e.nombre.trim(),
                            tipo: e.tipo || 'EXTRA',
                            precio: Number(e.precio_adicional || 40.00)
                        });
                    }
                });
            });

            // Si el comercio aún tiene pocos productos, complementamos con presets según su giro
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            (profile.defaultCategories || []).forEach(f => ingBaseSet.add(f));
            (profile.defaultExtras || []).forEach(fe => {
                if (!extrasMap.has(fe.nombre.toLowerCase())) {
                    extrasMap.set(fe.nombre.toLowerCase(), fe);
                }
            });

            return {
                ingredientesBase: Array.from(ingBaseSet).slice(0, 20),
                extras: Array.from(extrasMap.values()).slice(0, 16)
            };
        }

        function renderIngredientBuilderUI() {
            const presets = getComercioIngredientPresets();
            const sym = (currentComercioData && currentComercioData.comercio) ? currentComercioData.comercio.simbolo_moneda : 'C$';

            // 1. Barra de botones rápidos de ingredientes base propios del comercio
            const quickBar = document.getElementById('quickIngredientsBar');
            if (quickBar) {
                quickBar.innerHTML = presets.ingredientesBase.map(item => {
                    const isSelected = formIngredientesBaseList.some(
                        x => x.toLowerCase() === item.toLowerCase()
                    );
                    return `
                        <button type="button" onclick="toggleQuickIngredient('${item}')"
                            class="px-2 py-1 rounded-lg text-[11px] font-semibold border transition ${
                                isSelected
                                    ? 'bg-emerald-500 text-slate-950 border-emerald-400 font-bold'
                                    : 'bg-slate-900 hover:bg-slate-800 text-slate-300 border-slate-700'
                            }">
                            ${isSelected ? '<i class="fa-solid fa-check mr-1"></i>' : '<i class="fa-solid fa-plus mr-1"></i>'}${item}
                        </button>
                    `;
                }).join('');
            }

            // 2. Chips/etiquetas de los ingredientes base actualmente seleccionados
            const baseContainer = document.getElementById('selectedIngBaseContainer');
            const badgeBase = document.getElementById('countIngBaseBadge');
            if (badgeBase) badgeBase.textContent = `${formIngredientesBaseList.length} seleccionados`;
            if (baseContainer) {
                if (formIngredientesBaseList.length === 0) {
                    baseContainer.innerHTML = `<span class="text-[11px] text-slate-500 italic">Ningún ingrediente removible agregado todavía.</span>`;
                } else {
                    baseContainer.innerHTML = formIngredientesBaseList.map((ing, idx) => `
                        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 text-xs font-semibold">
                            <span>${ing}</span>
                            <button type="button" onclick="removeIngBaseAt(${idx})" class="text-emerald-300 hover:text-white font-bold"><i class="fa-solid fa-xmark text-xs"></i></button>
                        </span>
                    `).join('');
                }
            }

            // 3. Barra de sugerencias rápidas para Extras y Variantes propios de este comercio
            const quickExtrasBar = document.getElementById('quickExtrasBar');
            if (quickExtrasBar) {
                quickExtrasBar.innerHTML = presets.extras.map(item => {
                    const isSelected = formExtrasList.some(
                        x => x.nombre.toLowerCase() === item.nombre.toLowerCase()
                    );
                    return `
                        <button type="button" onclick="toggleQuickExtra('${item.nombre}', '${item.tipo}', ${item.precio})"
                            class="px-2 py-1 rounded-lg text-[11px] font-semibold border transition ${
                                isSelected
                                    ? 'bg-amber-500 text-slate-950 border-amber-400 font-bold'
                                    : 'bg-slate-900 hover:bg-slate-800 text-slate-300 border-slate-700'
                            }">
                            ${isSelected ? '<i class="fa-solid fa-check mr-1"></i>' : '<i class="fa-solid fa-plus mr-1"></i>'}${item.nombre} (+${sym}${item.precio.toFixed(2)})
                        </button>
                    `;
                }).join('');
            }

            // 4. Chips/etiquetas de Extras y Variantes seleccionados (estilo badge con botón de eliminación y edición)
            const extrasContainer = document.getElementById('selectedExtrasContainer');
            const badgeExtras = document.getElementById('countExtrasBadge');
            if (badgeExtras) badgeExtras.textContent = `${formExtrasList.length} agregados`;
            if (extrasContainer) {
                if (formExtrasList.length === 0) {
                    extrasContainer.innerHTML = `<div class="text-[11px] text-slate-500 italic">Sin extras o variantes configuradas.</div>`;
                } else {
                    extrasContainer.innerHTML = formExtrasList.map((ex, idx) => `
                        <span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-xs font-semibold ${
                            ex.tipo === 'VARIANTE'
                                ? 'bg-sky-500/15 border-sky-500/40 text-sky-200'
                                : 'bg-amber-500/15 border-amber-500/40 text-amber-200'
                        }">
                            <span class="text-[10px] opacity-75 uppercase font-bold">${ex.tipo === 'VARIANTE' ? 'VAR' : 'EXT'}</span>
                            <span>${ex.nombre}</span>
                            <span class="font-extrabold text-emerald-300">+${sym}${Number(ex.precio_adicional).toFixed(2)}</span>
                            <button type="button" onclick="editFormExtraPrice(${idx})" title="Editar precio" class="text-amber-300 hover:text-white font-bold ml-0.5 text-xs"><i class="fa-solid fa-pen-to-square"></i></button>
                            <button type="button" onclick="removeExtraAt(${idx})" title="Eliminar" class="text-rose-300 hover:text-white font-bold ml-0.5 text-xs"><i class="fa-solid fa-xmark"></i></button>
                        </span>
                    `).join('');
                }
            }
        }

        function toggleQuickExtra(nombre, tipo, precio) {
            const existingIdx = formExtrasList.findIndex(
                x => x.nombre.toLowerCase() === nombre.toLowerCase()
            );
            if (existingIdx >= 0) {
                formExtrasList.splice(existingIdx, 1);
            } else {
                formExtrasList.push({
                    nombre,
                    tipo,
                    precio_adicional: Number(precio)
                });
            }
            renderIngredientBuilderUI();
        }

        function toggleQuickIngredient(nombre) {
            const existingIdx = formIngredientesBaseList.findIndex(
                x => x.toLowerCase() === nombre.toLowerCase()
            );
            if (existingIdx >= 0) {
                formIngredientesBaseList.splice(existingIdx, 1);
            } else {
                formIngredientesBaseList.push(nombre);
            }
            renderIngredientBuilderUI();
        }

        function addCustomIngBase() {
            const input = document.getElementById('inputNuevoIngBase');
            const val = (input.value || '').trim();
            if (!val) return;
            if (!formIngredientesBaseList.some(x => x.toLowerCase() === val.toLowerCase())) {
                formIngredientesBaseList.push(val);
            }
            input.value = '';
            renderIngredientBuilderUI();
        }

        function removeIngBaseAt(index) {
            formIngredientesBaseList.splice(index, 1);
            renderIngredientBuilderUI();
        }

        function addExtraItemFromInputs() {
            const tipoSelect = document.getElementById('inputExtraTipo');
            const nameInput = document.getElementById('inputExtraNombre');
            const priceInput = document.getElementById('inputExtraPrecio');
            const tipo = tipoSelect ? tipoSelect.value : 'EXTRA';
            const nombre = (nameInput.value || '').trim();
            const precio = parseFloat(priceInput.value);
            if (!nombre) {
                showToast('Escribe el nombre del extra o variante (ej. Extra Tocino)', true);
                return;
            }
            formExtrasList.push({
                nombre,
                tipo,
                precio_adicional: isNaN(precio) ? 1.50 : precio
            });
            nameInput.value = '';
            priceInput.value = '';
            renderIngredientBuilderUI();
        }

        function removeExtraAt(index) {
            formExtrasList.splice(index, 1);
            renderIngredientBuilderUI();
        }

        function cargarPlatoEnFormularioParaEditar(platoId) {
            const pl = (currentComercioData.platos || []).find(x => x.id === platoId);
            if (!pl) return;

            document.getElementById('platoEditId').value = pl.id;
            document.getElementById('platoCategoria').value = pl.categoria || 'General';
            document.getElementById('platoNombre').value = pl.nombre;
            document.getElementById('platoPrecio').value = pl.precio_base;
            document.getElementById('platoDesc').value = pl.descripcion || '';

            formIngredientesBaseList = (pl.ingredientes_removibles || []).map(i => i.nombre);
            formExtrasList = (pl.extras_y_variantes || []).map(e => ({
                nombre: e.nombre,
                tipo: e.tipo || 'EXTRA',
                precio_adicional: Number(e.precio_adicional || 0)
            }));
            renderIngredientBuilderUI();

            document.getElementById('formPlatoIcon').innerHTML = '<i class="fa-solid fa-pen-to-square"></i>';
            document.getElementById('formPlatoIcon').className = 'w-8 h-8 rounded-lg bg-indigo-500/20 text-indigo-300 flex items-center justify-center font-bold text-sm';
            document.getElementById('formPlatoTitle').textContent = `Editando: ${pl.nombre}`;
            document.getElementById('formPlatoSubtitle').textContent = 'Modifica los ingredientes, precios o extras y guarda los cambios';
            document.getElementById('btnSubmitPlatoText').textContent = 'Guardar Cambios del Plato';
            document.getElementById('btnSubmitPlato').className =
                'w-full py-3 rounded-xl bg-indigo-500 hover:bg-indigo-600 text-white font-extrabold text-xs shadow-lg shadow-indigo-500/20 transition flex items-center justify-center gap-2';
            document.getElementById('btnCancelarEdicionPlato').classList.remove('hidden');
            
            // Scroll suave hacia el formulario en pantallas móviles
            document.getElementById('formPlatoTitle').scrollIntoView({ behavior: 'smooth', block: 'center' });
            showToast(`Editando "${pl.nombre}". Modifica los datos y presiona Guardar.`);
        }

        function resetFormularioPlato() {
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            document.getElementById('platoEditId').value = '';
            document.getElementById('platoNombre').value = '';
            document.getElementById('platoPrecio').value = '';
            document.getElementById('platoDesc').value = '';
            formIngredientesBaseList = profile.usesRemovableIngredients
                ? ['Cebolla', 'Tomate', 'Lechuga', 'Mayonesa'].slice(0, 4)
                : [];
            formExtrasList = (profile.defaultExtras || []).slice(0, 2).map(e => ({
                nombre: e.nombre,
                tipo: e.tipo || 'EXTRA',
                precio_adicional: Number(e.precio || 0)
            }));
            renderIngredientBuilderUI();
            document.getElementById('formPlatoIcon').innerHTML = '<i class="fa-solid fa-plus"></i>';
            document.getElementById('formPlatoIcon').className = 'w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-300 flex items-center justify-center font-bold text-sm';
            document.getElementById('formPlatoTitle').textContent = `Nuevo ${profile.itemSingular}`;
            document.getElementById('formPlatoSubtitle').textContent = profile.usesRemovableIngredients
                ? 'Configura nombre, precio, ingredientes removibles y extras'
                : 'Configura nombre, precio y variantes o presentaciones';
            document.getElementById('btnSubmitPlatoText').textContent = `Guardar ${profile.itemSingular} en Catálogo`;
            document.getElementById('btnSubmitPlato').className =
                'w-full py-3 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-extrabold text-xs shadow-lg shadow-emerald-500/20 transition flex items-center justify-center gap-2';
            document.getElementById('btnCancelarEdicionPlato').classList.add('hidden');
        }

        async function togglePlatoDisponibilidad(platoId) {
            try {
                await apiFetch(`/api/v1/panel/comercio/platos/${platoId}/toggle-disponibilidad`, {
                    method: 'PATCH'
                });
                showToast('Disponibilidad del plato actualizada al instante para el Bot.');
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function toggleIngredienteDisponibilidad(ingredienteId) {
            try {
                const res = await apiFetch(`/api/v1/panel/comercio/ingredientes/${ingredienteId}/toggle-disponibilidad`, {
                    method: 'PATCH'
                });
                showToast(`Ingrediente "${res.nombre}": ${res.disponible_global ? 'Disponible' : 'Agotado hoy'}`);
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function eliminarPlato(platoId) {
            try {
                await apiFetch(`/api/v1/panel/comercio/platos/${platoId}`, {
                    method: 'DELETE'
                });
                showToast('Plato eliminado del menú.');
                resetFormularioPlato();
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function handleGuardarPlato(e) {
            e.preventDefault();
            const editId = document.getElementById('platoEditId').value;

            const bodyPayload = {
                categoria_nombre: document.getElementById('platoCategoria').value,
                nombre: document.getElementById('platoNombre').value,
                precio_base: parseFloat(document.getElementById('platoPrecio').value),
                descripcion: document.getElementById('platoDesc').value,
                ingredientes_removibles: formIngredientesBaseList,
                extras_con_costo: formExtrasList
            };

            try {
                if (editId) {
                    await apiFetch(`/api/v1/panel/comercio/platos/${editId}`, {
                        method: 'PUT',
                        body: bodyPayload
                    });
                    showToast('¡Cambios del plato e ingredientes guardados correctamente!');
                } else {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/platos`, {
                        method: 'POST',
                        body: bodyPayload
                    });
                    showToast('¡Nuevo plato e ingredientes guardados!');
                }
                resetFormularioPlato();
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // EDICIÓN RÁPIDA DE PRECIOS & CONFIGURACIÓN DE MONEDA (C$)
        // =========================================================
        async function editarPrecioRapidoPlato(platoId, precioActual, nombre, sym) {
            const nuevoStr = prompt(`Cambiar precio base de "${nombre}" (en ${sym}):`, Number(precioActual).toFixed(2));
            if (nuevoStr === null) return;
            const nuevo = parseFloat(nuevoStr);
            if (isNaN(nuevo) || nuevo < 0) {
                showToast('Ingresa un precio válido (mayor o igual a 0).', true);
                return;
            }
            try {
                await apiFetch(`/api/v1/panel/comercio/platos/${platoId}/precio`, {
                    method: 'PATCH',
                    body: { precio_base: nuevo }
                });
                showToast(`¡Precio de "${nombre}" actualizado a ${sym}${nuevo.toFixed(2)}!`);
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function editarPrecioRapidoExtra(extraId, precioActual, nombre, sym) {
            const nuevoStr = prompt(`Cambiar precio adicional de "${nombre}" (en ${sym}):`, Number(precioActual).toFixed(2));
            if (nuevoStr === null) return;
            const nuevo = parseFloat(nuevoStr);
            if (isNaN(nuevo) || nuevo < 0) {
                showToast('Ingresa un precio válido.', true);
                return;
            }
            try {
                await apiFetch(`/api/v1/panel/comercio/ingredientes/${extraId}/precio`, {
                    method: 'PATCH',
                    body: { precio_adicional: nuevo }
                });
                showToast(`¡Precio de "${nombre}" actualizado a +${sym}${nuevo.toFixed(2)}!`);
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        function editFormExtraPrice(index) {
            const ex = formExtrasList[index];
            if (!ex) return;
            const sym = (currentComercioData && currentComercioData.comercio) ? currentComercioData.comercio.simbolo_moneda : 'C$';
            const nuevoStr = prompt(`Nuevo precio para "${ex.nombre}" (${sym}):`, Number(ex.precio_adicional || 0).toFixed(2));
            if (nuevoStr === null) return;
            const nuevo = parseFloat(nuevoStr);
            if (isNaN(nuevo) || nuevo < 0) {
                showToast('Precio inválido', true);
                return;
            }
            ex.precio_adicional = nuevo;
            renderIngredientBuilderUI();
        }

        function openConfigComercioModal() {
            if (!currentComercioData || !currentComercioData.comercio) {
                showToast('Cargando información del restaurante...', true);
                return;
            }
            const c = currentComercioData.comercio;
            document.getElementById('cfgSimboloMoneda').value = c.simbolo_moneda || 'C$';
            document.getElementById('cfgMonedaIso').value = c.moneda || 'NIO';
            document.getElementById('cfgNombreComercial').value = c.nombre_comercial || '';
            const cfgGiroEl = document.getElementById('cfgGiro');
            if (cfgGiroEl) cfgGiroEl.value = getGiroProfile(c.giro).code;
            document.getElementById('cfgTelefono').value = c.telefono_propietario || '';
            document.getElementById('cfgDatosBancarios').value = c.datos_bancarios_texto || '';
            document.getElementById('cfgAceptaEfectivo').checked = c.acepta_efectivo !== false;
            document.getElementById('cfgAceptaTransferencia').checked = c.acepta_transferencia !== false;

            document.getElementById('modalConfigComercio').classList.remove('hidden');
        }

        function closeConfigComercioModal() {
            document.getElementById('modalConfigComercio').classList.add('hidden');
        }

        function setMonedaPreset(simbolo, iso) {
            document.getElementById('cfgSimboloMoneda').value = simbolo;
            document.getElementById('cfgMonedaIso').value = iso;
        }

        async function handleGuardarConfigComercio(e) {
            e.preventDefault();
            if (!activeTenantId) return;

            const body = {
                simbolo_moneda: document.getElementById('cfgSimboloMoneda').value.trim(),
                moneda: document.getElementById('cfgMonedaIso').value.trim().toUpperCase(),
                nombre_comercial: document.getElementById('cfgNombreComercial').value.trim(),
                giro: document.getElementById('cfgGiro')?.value || undefined,
                telefono_propietario: document.getElementById('cfgTelefono').value.trim(),
                datos_bancarios_texto: document.getElementById('cfgDatosBancarios').value.trim(),
                acepta_efectivo: document.getElementById('cfgAceptaEfectivo').checked,
                acepta_transferencia: document.getElementById('cfgAceptaTransferencia').checked
            };

            try {
                await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/configuracion`, {
                    method: 'PATCH',
                    body
                });
                closeConfigComercioModal();
                showToast(`¡Configuración y moneda (${body.simbolo_moneda}) actualizadas con éxito!`);
                await reloadComercioDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // SUCURSALES, QR DE WHATSAPP Y RESERVACIONES DE MESA
        // =========================================================
        function renderSucursalesYMesas(sucursales, comercio) {
            document.getElementById('sucursalesCardsGrid').innerHTML = sucursales.map(s => `
                <div class="p-4 rounded-xl bg-slate-900 border border-slate-800 flex flex-col sm:flex-row items-center gap-4">
                    <img src="${s.qr_data_uri}" alt="QR WhatsApp ${s.nombre_sucursal}" class="w-28 h-28 rounded-xl bg-white p-1.5 shadow-md">
                    <div class="space-y-1.5 text-xs flex-1">
                        <div class="font-bold text-base text-white">${s.nombre_sucursal}</div>
                        <div class="text-emerald-300 font-semibold">Código Opción B: [${s.codigo_whatsapp}]</div>
                        <div class="text-slate-400"><i class="fa-solid fa-location-dot text-rose-400 mr-1"></i> ${s.direccion_fisica || ''}</div>
                        <div class="text-slate-300"><i class="fa-regular fa-clock text-slate-400 mr-1"></i> Horario: ${s.horario_apertura} a ${s.horario_cierre} • Entrega: <strong>${s.tiempo_estimado_entrega_min} min</strong></div>
                        <div class="text-slate-300"><i class="fa-solid fa-print text-slate-400 mr-1"></i> Formato Ticket: <strong>${s.ancho_ticket_termico}</strong></div>
                        <div class="pt-1">
                            <a href="${s.deep_link_whatsapp}" target="_blank"
                                class="inline-block px-3 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-bold text-xs transition">
                                <i class="fa-brands fa-whatsapp text-emerald-400 mr-1.5"></i> Probar Link de WhatsApp de esta Sucursal
                            </a>
                        </div>
                    </div>
                </div>
            `).join('');

            const badgeRes = document.getElementById('badgeEstadoReservaciones');
            if (comercio.reservaciones_habilitadas) {
                badgeRes.className = 'text-xs px-3 py-1 rounded-full font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
                badgeRes.innerHTML = '<i class="fa-solid fa-circle-check mr-1"></i> ACTIVO CON PAGO API (MODO 2)';
            } else {
                badgeRes.className = 'text-xs px-3 py-1 rounded-full font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40';
                badgeRes.innerHTML = '<i class="fa-solid fa-lock mr-1"></i> EN ESPERA DE INTEGRACIÓN (MODO 2)';
            }

            const todasLasMesas = [];
            sucursales.forEach(s => {
                (s.mesas || []).forEach(m => todasLasMesas.push({ ...m, sucursal: s.nombre_sucursal }));
            });
            document.getElementById('mesasGridInfo').innerHTML = todasLasMesas.map(m => `
                <div class="p-3 rounded-xl bg-slate-900 border border-slate-800 text-xs">
                    <div class="font-bold text-white"><i class="fa-solid fa-chair text-indigo-400 mr-1.5"></i> ${m.nombre_mesa}</div>
                    <div class="text-slate-400">${m.sucursal} • Capacidad: ${m.capacidad_personas} personas</div>
                    <div class="text-indigo-300 font-semibold mt-1">Pago obligatorio para reservar: ${comercio.simbolo_moneda}${m.monto_anticipo_reserva.toFixed(2)}</div>
                </div>
            `).join('');
        }

        // Delegación global de clics en pestañas del panel del comercio (inmune a re-renderizado y sin doble disparo)
        document.addEventListener('click', (e) => {
            const btn = e.target.closest ? e.target.closest('[data-comercio-tab]') : null;
            if (btn) {
                e.preventDefault();
                const tab = btn.getAttribute('data-comercio-tab');
                if (tab && typeof switchComercioTab === 'function') {
                    switchComercioTab(tab);
                }
            }
        });

        // Auto-verificar si ya había sesión guardada e inicializar constructor visual de ingredientes
        window.addEventListener('DOMContentLoaded', async () => {
            renderIngredientBuilderUI();
            if (authToken) {
                try {
                    currentUser = await apiFetch('/api/v1/auth/me');
                    await renderAppByRole();
                } catch (e) {
                    logout();
                }
            }
        });
