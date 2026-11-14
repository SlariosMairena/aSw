// =========================================================
// GESTIÓN DE INVENTARIO Y STOCK DEL RESTAURANTE
// =========================================================

let inventarioItems = [];
let inventarioCategorias = [];
let filtroEstadoActual = 'TODOS'; // 'TODOS', 'BAJO_STOCK', 'AGOTADOS'
let categoriaSeleccionadaActual = '';
let modoVistaInventario = 'AGRUPADO'; // 'AGRUPADO' (acordeones por categoría) | 'TABLA' (tabla continua)
let categoriasColapsadas = {}; // Guarda qué categorías están abiertas/cerradas

const CATEGORY_ICONS = {
    'Carnes': 'fa-drumstick-bite',
    'Panadería': 'fa-bread-slice',
    'Lácteos': 'fa-cheese',
    'Bebidas': 'fa-wine-bottle',
    'Vegetales': 'fa-carrot',
    'Vegetales & Frutas': 'fa-apple-whole',
    'Salsas': 'fa-jar',
    'Salsas & Especias': 'fa-pepper-hot',
    'Empaques': 'fa-box',
    'Empaques & Descartables': 'fa-boxes-packing'
};

/**
 * Carga el inventario desde el endpoint del panel
 */
async function cargarInventario() {
    const tid = (typeof activeTenantId !== 'undefined' && activeTenantId) 
        ? activeTenantId 
        : ((typeof currentUser !== 'undefined' && currentUser) ? currentUser.tenant_id : null);
    
    if (!tid) {
        console.warn('[cargarInventario] No hay tenant_id activo todavía');
        return;
    }

    const token = (typeof authToken !== 'undefined' && authToken) ? authToken : (function() {
        try { return localStorage.getItem('saas_jwt_token') || ''; } catch(e) { return ''; }
    })();

    const tbody = document.getElementById('inventarioTableBody');
    const containerAgrupado = document.getElementById('invVistaAgrupada');
    
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-2"></i> Consultando stock en tiempo real...
        </td></tr>`;
    }
    if (containerAgrupado) {
        containerAgrupado.innerHTML = `<div class="text-center py-8 text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-2"></i> Organizando categorías del inventario...
        </div>`;
    }

    try {
        const res = await fetch(`/api/v1/panel/comercio/${tid}/inventario`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.detail || 'Error al cargar inventario');
        }

        inventarioItems = data.items || [];
        inventarioCategorias = data.categorias || [];

        // 1. Actualizar métricas KPI
        const metrics = data.metricas || {};
        const totalItemsEl = document.getElementById('invMetricTotalItems');
        const valorTotalEl = document.getElementById('invMetricValorTotal');
        const bajoStockEl = document.getElementById('invMetricBajoStock');
        const agotadosEl = document.getElementById('invMetricAgotados');

        if (totalItemsEl) totalItemsEl.textContent = metrics.total_items || 0;
        if (valorTotalEl) {
            const val = Number(metrics.valor_total_inventario || 0);
            valorTotalEl.textContent = val.toLocaleString('es-NI', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }
        if (bajoStockEl) bajoStockEl.textContent = metrics.items_bajo_stock || 0;
        if (agotadosEl) agotadosEl.textContent = metrics.items_agotados || 0;

        // Badge en la pestaña si hay alertas
        const tabBadge = document.getElementById('tabBadgeBajoStock');
        if (tabBadge) {
            if ((metrics.items_bajo_stock || 0) > 0 || (metrics.items_agotados || 0) > 0) {
                tabBadge.textContent = `${(metrics.items_bajo_stock || 0) + (metrics.items_agotados || 0)}`;
                tabBadge.classList.remove('hidden');
            } else {
                tabBadge.classList.add('hidden');
            }
        }

        // 2. Llenar select de categorías
        const catSelect = document.getElementById('invCategorySelect');
        if (catSelect) {
            catSelect.innerHTML = `<option value="">Todas las categorías (${inventarioCategorias.length})</option>`;
            inventarioCategorias.forEach(cat => {
                const opt = document.createElement('option');
                opt.value = cat;
                opt.textContent = cat;
                catSelect.appendChild(opt);
            });
            catSelect.value = categoriaSeleccionadaActual;
        }

        // 3. Renderizar píldoras rápidas de categorías + opciones dinámicas del modal
        renderizarChipsCategorias();
        if (typeof renderDynamicInventoryCategoryOptions === 'function') {
            renderDynamicInventoryCategoryOptions();
        }

        // 4. Renderizar según filtros activos y modo de vista
        filtrarInventarioTabla();

    } catch (err) {
        console.error('Error cargando inventario:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-rose-400">
                <i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Error al cargar el inventario: ${err.message}
            </td></tr>`;
        }
        if (containerAgrupado) {
            containerAgrupado.innerHTML = `<div class="p-4 rounded-xl bg-rose-950/60 border border-rose-500/40 text-rose-300 text-center text-xs">
                <i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Error al cargar el inventario: ${err.message}
            </div>`;
        }
        showToast('Error cargando inventario: ' + err.message, true);
    }
}

/**
 * Renderiza píldoras interactivas arriba del inventario para cambiar de categoría con 1 clic
 */
function renderizarChipsCategorias() {
    const container = document.getElementById('invCategoriasChipsBar');
    if (!container) return;

    // Calcular totales por categoría
    const statsPorCat = {};
    inventarioItems.forEach(it => {
        const c = it.categoria || 'General';
        statsPorCat[c] = (statsPorCat[c] || 0) + 1;
    });

    const isAllActive = !categoriaSeleccionadaActual;

    let html = `
        <button type="button" onclick="seleccionarCategoriaInventario('')"
            class="px-3 py-1 rounded-xl text-xs font-semibold border transition ${
                isAllActive
                    ? 'bg-emerald-500 text-slate-950 border-emerald-400 font-bold shadow-md shadow-emerald-500/20'
                    : 'bg-slate-900/90 text-slate-300 border-slate-700 hover:bg-slate-800'
            }">
            <span>Todas</span> <span class="text-[10px] opacity-75">(${inventarioItems.length})</span>
        </button>
    `;

    inventarioCategorias.forEach(cat => {
        const isActive = categoriaSeleccionadaActual === cat;
        const count = statsPorCat[cat] || 0;
        const iconName = CATEGORY_ICONS[cat] || 'fa-tag';

        html += `
            <button type="button" onclick="seleccionarCategoriaInventario('${escapeJsStr(cat)}')"
                class="px-2.5 py-1 rounded-xl text-xs font-medium border transition flex items-center gap-1.5 ${
                    isActive
                        ? 'bg-emerald-500 text-slate-950 border-emerald-400 font-bold shadow-md shadow-emerald-500/20'
                        : 'bg-slate-900/90 text-slate-300 border-slate-700 hover:bg-slate-800'
                }">
                <i class="fa-solid ${iconName} text-[11px] opacity-80"></i>
                <span>${escapeHtml(cat)}</span>
                <span class="text-[10px] opacity-75">(${count})</span>
            </button>
        `;
    });

    container.innerHTML = html;
}

/**
 * Selecciona una categoría tanto desde las píldoras como desde el select
 */
function seleccionarCategoriaInventario(cat) {
    categoriaSeleccionadaActual = cat;
    const catSelect = document.getElementById('invCategorySelect');
    if (catSelect) catSelect.value = cat;
    renderizarChipsCategorias();
    filtrarInventarioTabla();
}

/**
 * Cambia entre modo agrupado por categorías (acordeón) y tabla plana
 */
function setModoVistaInventario(modo) {
    modoVistaInventario = modo;
    const btnAgrupado = document.getElementById('btnVistaInv_AGRUPADO');
    const btnTabla = document.getElementById('btnVistaInv_TABLA');

    const vistaAgrupada = document.getElementById('invVistaAgrupada');
    const vistaTabla = document.getElementById('invVistaTabla');

    if (modo === 'AGRUPADO') {
        if (btnAgrupado) btnAgrupado.className = 'px-2.5 py-1 rounded-lg bg-indigo-500 text-white font-bold transition flex items-center gap-1';
        if (btnTabla) btnTabla.className = 'px-2.5 py-1 rounded-lg text-slate-300 hover:text-white transition flex items-center gap-1';
        if (vistaAgrupada) vistaAgrupada.classList.remove('hidden');
        if (vistaTabla) vistaTabla.classList.add('hidden');
    } else {
        if (btnAgrupado) btnAgrupado.className = 'px-2.5 py-1 rounded-lg text-slate-300 hover:text-white transition flex items-center gap-1';
        if (btnTabla) btnTabla.className = 'px-2.5 py-1 rounded-lg bg-indigo-500 text-white font-bold transition flex items-center gap-1';
        if (vistaAgrupada) vistaAgrupada.classList.add('hidden');
        if (vistaTabla) vistaTabla.classList.remove('hidden');
    }

    filtrarInventarioTabla();
}

/**
 * Alterna el colapso de un acordeón de categoría
 */
function toggleColapsoCategoria(cat) {
    categoriasColapsadas[cat] = !categoriasColapsadas[cat];
    const body = document.getElementById(`catBody_${cat.replace(/[^a-zA-Z0-9]/g, '_')}`);
    const icon = document.getElementById(`catIcon_${cat.replace(/[^a-zA-Z0-9]/g, '_')}`);
    if (body) {
        body.classList.toggle('hidden', categoriasColapsadas[cat]);
    }
    if (icon) {
        icon.className = `fa-solid fa-chevron-${categoriasColapsadas[cat] ? 'right' : 'down'} text-xs text-slate-400 transition-transform`;
    }
}

/**
 * Filtra los insumos de inventario según búsqueda, categoría y estado (TODOS, BAJO_STOCK, AGOTADOS)
 */
function filtrarInventarioTabla() {
    const search = (document.getElementById('invSearchInput')?.value || '').toLowerCase().trim();
    const cat = categoriaSeleccionadaActual;

    const filtrados = inventarioItems.filter(item => {
        // Filtro texto
        const matchSearch = !search || 
            (item.nombre_insumo || '').toLowerCase().includes(search) ||
            (item.proveedor || '').toLowerCase().includes(search) ||
            (item.categoria || '').toLowerCase().includes(search);

        // Filtro categoría
        const matchCat = !cat || item.categoria === cat;

        // Filtro estado
        let matchEstado = true;
        if (filtroEstadoActual === 'BAJO_STOCK') {
            matchEstado = item.stock_actual <= item.stock_minimo && item.stock_actual > 0;
        } else if (filtroEstadoActual === 'AGOTADOS') {
            matchEstado = item.stock_actual <= 0;
        }

        return matchSearch && matchCat && matchEstado;
    });

    const infoEl = document.getElementById('invFiltradosInfo');
    if (infoEl) {
        infoEl.textContent = `Mostrando ${filtrados.length} de ${inventarioItems.length} insumos`;
    }

    if (modoVistaInventario === 'AGRUPADO') {
        renderizarVistaAgrupada(filtrados);
    } else {
        renderizarTablaInventario(filtrados);
    }
}

/**
 * Renderiza la vista agrupada en acordeones ordenados por categoría
 */
function renderizarVistaAgrupada(items) {
    const container = document.getElementById('invVistaAgrupada');
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = `
            <div class="glass rounded-2xl p-8 text-center text-slate-400 border border-slate-800">
                <i class="fa-solid fa-boxes-stacked text-3xl mb-2 text-slate-600 block"></i>
                <p class="font-bold text-slate-300">Ningún insumo coincide con el filtro seleccionado</p>
                <p class="text-xs text-slate-500 mt-1">Prueba cambiando el buscador o selecciona otra categoría.</p>
            </div>
        `;
        return;
    }

    // Agrupar items por categoría
    const grupos = {};
    items.forEach(it => {
        const c = it.categoria || 'General';
        if (!grupos[c]) grupos[c] = [];
        grupos[c].push(it);
    });

    const curSymbol = currentComercioData?.comercio?.simbolo_moneda || currentComercioData?.simbolo_moneda || 'C$';
    const esAdmin = (typeof isComercioAdminOrSuper === 'function') ? isComercioAdminOrSuper() : true;

    let html = '';
    Object.keys(grupos).sort().forEach(cat => {
        const catItems = grupos[cat];
        const safeCatId = cat.replace(/[^a-zA-Z0-9]/g, '_');
        const isCollapsed = !!categoriasColapsadas[cat];

        // Calcular métricas de la categoría
        let valorCat = 0;
        let alertasCat = 0;
        let agotadosCat = 0;

        catItems.forEach(it => {
            const stock = Number(it.stock_actual || 0);
            const costo = Number(it.costo_unitario || 0);
            valorCat += (stock * costo);
            if (stock <= 0) agotadosCat++;
            else if (stock <= it.stock_minimo) alertasCat++;
        });

        const iconName = CATEGORY_ICONS[cat] || 'fa-layer-group';

        html += `
            <div class="glass rounded-2xl border border-slate-800/80 overflow-hidden shadow-lg transition">
                <!-- Cabecera del Acordeón de la Categoría -->
                <div onclick="toggleColapsoCategoria('${escapeJsStr(cat)}')"
                    class="p-4 bg-slate-900/80 hover:bg-slate-850 cursor-pointer flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 select-none">
                    <div class="flex items-center gap-2.5">
                        <span class="w-8 h-8 rounded-xl bg-indigo-500/15 text-indigo-400 flex items-center justify-center text-sm font-bold border border-indigo-500/25">
                            <i class="fa-solid ${iconName}"></i>
                        </span>
                        <div>
                            <div class="flex items-center gap-2">
                                <h4 class="font-bold text-sm text-white">${escapeHtml(cat)}</h4>
                                <span class="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 font-semibold border border-slate-700">
                                    ${catItems.length} insumos
                                </span>
                            </div>
                            ${esAdmin ? `<span class="text-[11px] text-slate-400">
                                Valor estimado: <strong class="text-emerald-400">${curSymbol} ${valorCat.toLocaleString('es-NI', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong>
                            </span>` : `<span class="text-[11px] text-slate-400">Existencias operativas de sucursal</span>`}
                        </div>
                    </div>

                    <div class="flex items-center gap-2">
                        ${alertasCat > 0 ? `
                            <span class="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] font-bold flex items-center gap-1">
                                <i class="fa-solid fa-triangle-exclamation text-[9px]"></i> ${alertasCat} bajo stock
                            </span>
                        ` : ''}
                        ${agotadosCat > 0 ? `
                            <span class="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 border border-rose-500/30 text-[10px] font-bold flex items-center gap-1">
                                <i class="fa-solid fa-ban text-[9px]"></i> ${agotadosCat} agotados
                            </span>
                        ` : ''}
                        <button type="button" class="w-7 h-7 rounded-lg bg-slate-800 flex items-center justify-center text-slate-400 hover:text-white transition">
                            <i id="catIcon_${safeCatId}" class="fa-solid fa-chevron-${isCollapsed ? 'right' : 'down'} text-xs"></i>
                        </button>
                    </div>
                </div>

                <!-- Cuerpo de Insumos de la Categoría -->
                <div id="catBody_${safeCatId}" class="${isCollapsed ? 'hidden' : ''} overflow-x-auto">
                    <table class="w-full text-left text-xs">
                        <thead class="bg-slate-950/60 text-slate-400 uppercase text-[10px] tracking-wider border-b border-slate-800/80">
                            <tr>
                                <th class="px-4 py-2.5">Insumo</th>
                                <th class="px-3 py-2.5 text-center">Stock Actual</th>
                                <th class="px-3 py-2.5 text-center">Mínimo</th>
                                <th class="px-3 py-2.5 text-center">Estado</th>
                                ${esAdmin ? `<th class="px-3 py-2.5 text-right">Costo Unit.</th>
                                <th class="px-3 py-2.5 text-right">Valor Total</th>` : ''}
                                <th class="px-4 py-2.5 text-center">Ajuste Rápido / Acciones</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-800/50 bg-slate-900/30">
                            ${catItems.map(item => buildFilaItemInventarioHTML(item, curSymbol, false)).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

/**
 * Cambia el filtro de estado rápido (TODOS / BAJO_STOCK / AGOTADOS)
 */
function setFiltroEstadoInventario(estado) {
    filtroEstadoActual = estado;

    ['TODOS', 'BAJO_STOCK', 'AGOTADOS'].forEach(k => {
        const btn = document.getElementById(`btnInvFilter_${k}`);
        if (!btn) return;
        if (k === estado) {
            btn.className = 'px-2.5 py-1 rounded-lg bg-emerald-500 text-slate-950 font-bold transition';
        } else {
            btn.className = 'px-2.5 py-1 rounded-lg text-slate-300 hover:text-white transition';
        }
    });

    filtrarInventarioTabla();
}

/**
 * Construye el HTML de una fila de insumo (compartido entre vista agrupada y tabla continua)
 */
function buildFilaItemInventarioHTML(item, curSymbol, showCategoryColumn = true) {
    const stock = Number(item.stock_actual || 0);
    const min = Number(item.stock_minimo || 0);
    const costo = Number(item.costo_unitario || 0);
    const valorTotal = stock * costo;
    const esAdmin = (typeof isComercioAdminOrSuper === 'function') ? isComercioAdminOrSuper() : true;

    let badgeHtml = '';
    if (stock <= 0) {
        badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30 text-[10px] font-bold"><i class="fa-solid fa-ban text-[9px] mr-1"></i> AGOTADO</span>`;
    } else if (stock <= min) {
        badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] font-bold"><i class="fa-solid fa-triangle-exclamation text-[9px] mr-1"></i> BAJO</span>`;
    } else {
        badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-bold"><i class="fa-solid fa-check text-[9px] mr-1"></i> ÓPTIMO</span>`;
    }

    const safeItemJson = encodeURIComponent(JSON.stringify(item));

    return `
    <tr class="hover:bg-slate-900/60 transition group border-b border-slate-800/60">
        <td class="px-4 py-3">
            <div class="font-bold text-white text-xs">${escapeHtml(item.nombre_insumo)}</div>
            ${item.proveedor ? `<div class="text-[10px] text-slate-400 flex items-center gap-1"><i class="fa-solid fa-truck text-[9px]"></i> ${escapeHtml(item.proveedor)}</div>` : ''}
        </td>
        ${showCategoryColumn ? `
            <td class="px-3 py-3">
                <span class="px-2 py-0.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-[11px] font-medium">
                    ${escapeHtml(item.categoria)}
                </span>
            </td>
        ` : ''}
        <td class="px-3 py-3 text-center">
            <div class="font-extrabold text-sm ${stock <= 0 ? 'text-rose-400' : stock <= min ? 'text-amber-300' : 'text-slate-100'}">
                ${stock.toFixed(2)}
            </div>
            <div class="text-[10px] text-slate-400">${escapeHtml(item.unidad_medida)}</div>
        </td>
        <td class="px-3 py-3 text-center">
            <span class="text-slate-400 font-semibold text-xs">${min.toFixed(2)}</span>
        </td>
        <td class="px-3 py-3 text-center whitespace-nowrap">
            ${badgeHtml}
        </td>
        ${esAdmin ? `
            <td class="px-3 py-3 text-right">
                <span class="text-slate-300 font-semibold">${curSymbol} ${costo.toFixed(2)}</span>
            </td>
            <td class="px-3 py-3 text-right">
                <span class="text-emerald-400 font-bold">${curSymbol} ${valorTotal.toLocaleString('es-NI', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
            </td>
        ` : ''}
        <td class="px-4 py-3 text-center whitespace-nowrap">
            <div class="flex items-center justify-center gap-1">
                <button type="button" title="Registrar Entrada / Compra (+)" 
                    onclick="abrirModalAjusteRapido('${item.id}', '${escapeJsStr(item.nombre_insumo)}', '${escapeJsStr(item.unidad_medida)}', 'SUMAR')"
                    class="w-7 h-7 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 flex items-center justify-center font-bold text-xs transition">
                    <i class="fa-solid fa-plus text-[10px]"></i>
                </button>
                <button type="button" title="Registrar Salida / Merma (-)" 
                    onclick="abrirModalAjusteRapido('${item.id}', '${escapeJsStr(item.nombre_insumo)}', '${escapeJsStr(item.unidad_medida)}', 'RESTAR')"
                    class="w-7 h-7 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 flex items-center justify-center font-bold text-xs transition">
                    <i class="fa-solid fa-minus text-[10px]"></i>
                </button>
                ${esAdmin ? `
                    <button type="button" title="Editar Insumo" 
                        onclick="abrirModalEditarInsumoJson('${safeItemJson}')"
                        class="w-7 h-7 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center justify-center text-xs transition">
                        <i class="fa-solid fa-pen-to-square text-[10px]"></i>
                    </button>
                    <button type="button" title="Eliminar Insumo" 
                        onclick="eliminarItemInventario('${item.id}', '${escapeJsStr(item.nombre_insumo)}')"
                        class="w-7 h-7 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 flex items-center justify-center text-xs transition">
                        <i class="fa-solid fa-trash text-[10px]"></i>
                    </button>
                ` : ''}
            </div>
        </td>
    </tr>`;
}

/**
 * Dibuja las filas de la tabla plana continua
 */
function renderizarTablaInventario(items) {
    const tbody = document.getElementById('inventarioTableBody');
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-8 text-slate-400">
            Ningún insumo coincide con el filtro seleccionado.
        </td></tr>`;
        return;
    }

    const curSymbol = currentComercioData?.comercio?.simbolo_moneda || currentComercioData?.simbolo_moneda || 'C$';
    const esAdmin = (typeof isComercioAdminOrSuper === 'function') ? isComercioAdminOrSuper() : true;
    tbody.innerHTML = items.map(item => buildFilaItemInventarioHTML(item, curSymbol, true)).join('');
}

function renderDynamicInventoryCategoryOptions() {
    const GIRO_CATEGORY_PRESETS = {
        RESTAURANTE: ['Carnes', 'Panadería', 'Lácteos', 'Salsas & Especias', 'Vegetales & Frutas', 'Bebidas', 'Empaques & Descartables'],
        CAFETERIA: ['Granos & Café', 'Lácteos & Leches', 'Repostería', 'Jarabes & Endulzantes', 'Bebidas', 'Empaques & Vasos'],
        FARMACIA: ['Analgésicos', 'Antibióticos', 'Vitaminas & Suplementos', 'Cuidado Personal', 'Material de Curación', 'Pediatría'],
        TIENDA: ['Ropa & Calzado', 'Accesorios', 'Hogar & Decoración', 'Electrónica', 'Empaques & Bolsas'],
        SUPERMERCADO: ['Abarrotes', 'Lácteos & Huevos', 'Frutas & Verduras', 'Carnes & Embutidos', 'Bebidas', 'Limpieza del Hogar', 'Higiene Personal']
    };
    const giroRaw = String(currentComercioData?.comercio?.giro || 'RESTAURANTE').toUpperCase();
    let presets = GIRO_CATEGORY_PRESETS.RESTAURANTE;
    if (giroRaw.includes('CAFETER')) presets = GIRO_CATEGORY_PRESETS.CAFETERIA;
    else if (giroRaw.includes('FARMAC')) presets = GIRO_CATEGORY_PRESETS.FARMACIA;
    else if (giroRaw.includes('TIENDA')) presets = GIRO_CATEGORY_PRESETS.TIENDA;
    else if (giroRaw.includes('SUPER')) presets = GIRO_CATEGORY_PRESETS.SUPERMERCADO;

    const mergedSet = new Set(presets);
    (inventarioCategorias || []).forEach(c => { if (c && c.trim()) mergedSet.add(c.trim()); });
    (inventarioItems || []).forEach(it => { if (it.categoria && it.categoria.trim()) mergedSet.add(it.categoria.trim()); });
    const allCats = Array.from(mergedSet);

    const datalist = document.getElementById('categoriasInventarioList');
    if (datalist) {
        datalist.innerHTML = allCats.map(c => `<option value="${escapeHtml(c)}">`).join('');
    }

    const chips = document.getElementById('invCategoriasQuickChips');
    if (chips) {
        chips.innerHTML = allCats.map(c => `
            <button type="button" onclick="document.getElementById('invItemCategoria').value='${escapeJsStr(c)}'"
                class="px-2 py-0.5 rounded-lg bg-slate-800 hover:bg-emerald-500/20 text-slate-300 hover:text-emerald-300 border border-slate-700 text-[10px] font-medium transition">
                ${escapeHtml(c)}
            </button>
        `).join('');
    }
}

/**
 * Abre el modal para dar de alta un insumo nuevo
 */
function abrirModalNuevoInsumo() {
    renderDynamicInventoryCategoryOptions();
    document.getElementById('invItemEditId').value = '';
    document.getElementById('invItemNombre').value = '';
    document.getElementById('invItemCategoria').value = categoriaSeleccionadaActual || '';
    document.getElementById('invItemUnidad').value = 'kg';
    document.getElementById('invItemStockActual').value = '';
    document.getElementById('invItemStockMinimo').value = '';
    document.getElementById('invItemCosto').value = '';
    document.getElementById('invItemProveedor').value = '';
    document.getElementById('invItemNotas').value = '';

    const profile = typeof getGiroProfile === 'function' ? getGiroProfile(currentComercioData?.comercio?.giro) : null;
    document.getElementById('modalInvIcon').innerHTML = `<i class="fa-solid ${profile ? profile.icon : 'fa-boxes-stacked'}"></i>`;
    document.getElementById('modalInvTitle').textContent = profile ? profile.invModalNewTitle : 'Nuevo Insumo de Inventario';
    document.getElementById('btnSubmitItemInventarioText').textContent = profile ? `Guardar ${profile.itemSingular}` : 'Guardar Insumo';
    document.getElementById('wrapperStockInicial').classList.remove('hidden');

    document.getElementById('modalItemInventario').classList.remove('hidden');
}

/**
 * Abre el modal para editar un insumo existente
 */
function abrirModalEditarInsumoJson(safeJson) {
    try {
        renderDynamicInventoryCategoryOptions();
        const item = JSON.parse(decodeURIComponent(safeJson));
        document.getElementById('invItemEditId').value = item.id;
        document.getElementById('invItemNombre').value = item.nombre_insumo || '';
        document.getElementById('invItemCategoria').value = item.categoria || '';
        document.getElementById('invItemUnidad').value = item.unidad_medida || 'unidades';
        document.getElementById('invItemStockActual').value = item.stock_actual || 0;
        document.getElementById('invItemStockMinimo').value = item.stock_minimo || 0;
        document.getElementById('invItemCosto').value = item.costo_unitario || 0;
        document.getElementById('invItemProveedor').value = item.proveedor || '';
        document.getElementById('invItemNotas').value = item.notas || '';

        document.getElementById('modalInvIcon').innerHTML = '<i class="fa-solid fa-pen-to-square"></i>';
        document.getElementById('modalInvTitle').textContent = `Editar: ${item.nombre_insumo}`;
        document.getElementById('btnSubmitItemInventarioText').textContent = 'Actualizar Datos';
        document.getElementById('wrapperStockInicial').classList.add('hidden');

        document.getElementById('modalItemInventario').classList.remove('hidden');
    } catch (e) {
        console.error('Error parseando json item:', e);
    }
}

function cerrarModalItemInventario() {
    document.getElementById('modalItemInventario').classList.add('hidden');
}

/**
 * Guarda o actualiza un insumo de inventario
 */
async function handleGuardarItemInventario(e) {
    e.preventDefault();
    if (!activeTenantId) return;

    const editId = document.getElementById('invItemEditId').value;
    const isEdit = !!editId;

    const payload = {
        nombre_insumo: document.getElementById('invItemNombre').value.trim(),
        categoria: document.getElementById('invItemCategoria').value.trim(),
        unidad_medida: document.getElementById('invItemUnidad').value,
        stock_minimo: parseFloat(document.getElementById('invItemStockMinimo').value) || 0,
        costo_unitario: parseFloat(document.getElementById('invItemCosto').value) || 0,
        proveedor: document.getElementById('invItemProveedor').value.trim() || null,
        notas: document.getElementById('invItemNotas').value.trim() || null
    };

    if (!isEdit) {
        payload.stock_actual = parseFloat(document.getElementById('invItemStockActual').value) || 0;
    }

    const btnSubmit = document.getElementById('btnSubmitItemInventario');
    btnSubmit.disabled = true;
    btnSubmit.classList.add('opacity-50');

    try {
        let res;
        if (isEdit) {
            res = await fetch(`/api/v1/panel/comercio/inventario/${editId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify(payload)
            });
        } else {
            res = await fetch(`/api/v1/panel/comercio/${activeTenantId}/inventario`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify(payload)
            });
        }

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Error al guardar el insumo');
        }

        showToast(isEdit ? '✓ Insumo actualizado con éxito' : '✓ Insumo registrado con stock inicial');
        cerrarModalItemInventario();
        await cargarInventario();
    } catch (err) {
        showToast(err.message, true);
    } finally {
        btnSubmit.disabled = false;
        btnSubmit.classList.remove('opacity-50');
    }
}

/**
 * Abre el diálogo de ajuste rápido (+ / - / Fijar)
 */
function abrirModalAjusteRapido(itemId, itemNombre, unidad, tipoDefault = 'SUMAR') {
    document.getElementById('ajusteItemId').value = itemId;
    document.getElementById('ajusteModalTitulo').textContent = `Ajustar Stock: ${itemNombre}`;
    document.getElementById('ajusteModalSub').textContent = `Unidad: ${unidad}`;
    document.getElementById('ajusteUnidadLabel').textContent = unidad;
    document.getElementById('ajusteCantidad').value = '';
    document.getElementById('ajusteMotivo').value = tipoDefault === 'SUMAR' ? 'Compra o reposición' : 'Merma o uso en cocina';

    // Seleccionar radio
    const radios = document.getElementsByName('ajusteTipoRadio');
    for (let r of radios) {
        r.checked = (r.value === tipoDefault);
    }

    document.getElementById('modalAjusteStock').classList.remove('hidden');
    setTimeout(() => document.getElementById('ajusteCantidad')?.focus(), 100);
}

function cerrarModalAjusteStock() {
    document.getElementById('modalAjusteStock').classList.add('hidden');
}

/**
 * Ejecuta el ajuste rápido de stock
 */
async function handleEjecutarAjusteStock(e) {
    e.preventDefault();
    const itemId = document.getElementById('ajusteItemId').value;
    const cantidad = parseFloat(document.getElementById('ajusteCantidad').value);
    const motivo = document.getElementById('ajusteMotivo').value.trim();

    const radios = document.getElementsByName('ajusteTipoRadio');
    let tipo = 'SUMAR';
    for (let r of radios) {
        if (r.checked) {
            tipo = r.value;
            break;
        }
    }

    if (isNaN(cantidad) || cantidad <= 0) {
        showToast('Ingresa una cantidad válida mayor a 0', true);
        return;
    }

    try {
        const res = await fetch(`/api/v1/panel/comercio/inventario/${itemId}/ajustar`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                tipo: tipo,
                cantidad: cantidad,
                motivo: motivo || (tipo === 'SUMAR' ? 'Compra' : 'Ajuste manual')
            })
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Error al ajustar stock');
        }

        showToast(`✓ Stock actualizado: ${data.stock_anterior} ➔ ${data.stock_nuevo}`);
        cerrarModalAjusteStock();
        await cargarInventario();
    } catch (err) {
        showToast(err.message, true);
    }
}

/**
 * Elimina (desactiva) un insumo de inventario
 */
async function eliminarItemInventario(itemId, itemNombre) {
    if (!confirm(`¿Estás seguro de eliminar el insumo "${itemNombre}" del inventario?`)) {
        return;
    }

    try {
        const res = await fetch(`/api/v1/panel/comercio/inventario/${itemId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${authToken}` }
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Error al eliminar insumo');
        }

        showToast(`Insumo "${itemNombre}" eliminado.`);
        await cargarInventario();
    } catch (err) {
        showToast(err.message, true);
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeJsStr(str) {
    if (!str) return '';
    return String(str).replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

window.cargarInventario = cargarInventario;
