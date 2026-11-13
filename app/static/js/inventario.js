// =========================================================
// GESTIÓN DE INVENTARIO Y STOCK DEL RESTAURANTE
// =========================================================

let inventarioItems = [];
let inventarioCategorias = [];
let filtroEstadoActual = 'TODOS'; // 'TODOS', 'BAJO_STOCK', 'AGOTADOS'

/**
 * Carga el inventario desde el endpoint del panel
 */
async function cargarInventario() {
    if (!activeTenantId) return;

    const tbody = document.getElementById('inventarioTableBody');
    if (tbody) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-slate-400">
            <span class="inline-block animate-spin mr-2">🔄</span> Consultando stock en tiempo real...
        </td></tr>`;
    }

    try {
        const res = await fetch(`/api/v1/panel/comercio/${activeTenantId}/inventario`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
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
            const currentVal = catSelect.value;
            catSelect.innerHTML = `<option value="">Todas las categorías (${inventarioCategorias.length})</option>`;
            inventarioCategorias.forEach(cat => {
                const opt = document.createElement('option');
                opt.value = cat;
                opt.textContent = cat;
                catSelect.appendChild(opt);
            });
            catSelect.value = currentVal;
        }

        // 3. Renderizar tabla con los filtros activos
        filtrarInventarioTabla();

    } catch (err) {
        console.error('Error cargando inventario:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-rose-400">
                ⚠️ Error al cargar el inventario: ${err.message}
            </td></tr>`;
        }
        showToast('Error cargando inventario: ' + err.message, true);
    }
}

/**
 * Filtra los insumos de inventario según búsqueda, categoría y estado (TODOS, BAJO_STOCK, AGOTADOS)
 */
function filtrarInventarioTabla() {
    const search = (document.getElementById('invSearchInput')?.value || '').toLowerCase().trim();
    const cat = document.getElementById('invCategorySelect')?.value || '';

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

    renderizarTablaInventario(filtrados);
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
 * Dibuja las filas de la tabla de inventario
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

    const curSymbol = currentComercioData?.simbolo_moneda || 'C$';

    tbody.innerHTML = items.map(item => {
        const stock = Number(item.stock_actual || 0);
        const min = Number(item.stock_minimo || 0);
        const costo = Number(item.costo_unitario || 0);
        const valorTotal = stock * costo;

        // Determinar badge de estado
        let badgeHtml = '';
        if (stock <= 0) {
            badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500/30 text-[10px] font-bold animate-pulse">🛑 AGOTADO</span>`;
        } else if (stock <= min) {
            badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] font-bold">⚠️ BAJO</span>`;
        } else {
            badgeHtml = `<span class="px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-[10px] font-bold">✓ ÓPTIMO</span>`;
        }

        const safeItemJson = encodeURIComponent(JSON.stringify(item));

        return `
        <tr class="hover:bg-slate-900/60 transition group border-b border-slate-800/60">
            <td class="px-4 py-3">
                <div class="font-bold text-white text-xs">${escapeHtml(item.nombre_insumo)}</div>
                ${item.proveedor ? `<div class="text-[10px] text-slate-400 flex items-center gap-1">🚚 ${escapeHtml(item.proveedor)}</div>` : ''}
            </td>
            <td class="px-3 py-3">
                <span class="px-2 py-0.5 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-[11px] font-medium">
                    ${escapeHtml(item.categoria)}
                </span>
            </td>
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
            <td class="px-3 py-3 text-right">
                <span class="text-slate-300 font-semibold">${costo.toFixed(2)}</span>
            </td>
            <td class="px-3 py-3 text-right">
                <span class="text-emerald-400 font-bold">${valorTotal.toLocaleString('es-NI', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
            </td>
            <td class="px-4 py-3 text-center whitespace-nowrap">
                <div class="flex items-center justify-center gap-1">
                    <button type="button" title="Registrar Entrada / Compra (+)" 
                        onclick="abrirModalAjusteRapido('${item.id}', '${escapeJsStr(item.nombre_insumo)}', '${escapeJsStr(item.unidad_medida)}', 'SUMAR')"
                        class="w-7 h-7 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 flex items-center justify-center font-bold text-xs transition">
                        +
                    </button>
                    <button type="button" title="Registrar Salida / Merma (-)" 
                        onclick="abrirModalAjusteRapido('${item.id}', '${escapeJsStr(item.nombre_insumo)}', '${escapeJsStr(item.unidad_medida)}', 'RESTAR')"
                        class="w-7 h-7 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 flex items-center justify-center font-bold text-xs transition">
                        -
                    </button>
                    <button type="button" title="Editar Insumo" 
                        onclick="abrirModalEditarInsumoJson('${safeItemJson}')"
                        class="w-7 h-7 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center justify-center text-xs transition">
                        ✏️
                    </button>
                    <button type="button" title="Eliminar Insumo" 
                        onclick="eliminarItemInventario('${item.id}', '${escapeJsStr(item.nombre_insumo)}')"
                        class="w-7 h-7 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 flex items-center justify-center text-xs transition">
                        🗑️
                    </button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

/**
 * Abre el modal para dar de alta un insumo nuevo
 */
function abrirModalNuevoInsumo() {
    document.getElementById('invItemEditId').value = '';
    document.getElementById('invItemNombre').value = '';
    document.getElementById('invItemCategoria').value = '';
    document.getElementById('invItemUnidad').value = 'kg';
    document.getElementById('invItemStockActual').value = '';
    document.getElementById('invItemStockMinimo').value = '';
    document.getElementById('invItemCosto').value = '';
    document.getElementById('invItemProveedor').value = '';
    document.getElementById('invItemNotas').value = '';

    document.getElementById('modalInvIcon').textContent = '📦';
    document.getElementById('modalInvTitle').textContent = 'Nuevo Insumo de Inventario';
    document.getElementById('btnSubmitItemInventarioText').textContent = 'Guardar Insumo';
    document.getElementById('wrapperStockInicial').classList.remove('hidden');

    document.getElementById('modalItemInventario').classList.remove('hidden');
}

/**
 * Abre el modal para editar un insumo existente
 */
function abrirModalEditarInsumoJson(safeJson) {
    try {
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

        document.getElementById('modalInvIcon').textContent = '✏️';
        document.getElementById('modalInvTitle').textContent = `Editar: ${item.nombre_insumo}`;
        document.getElementById('btnSubmitItemInventarioText').textContent = 'Actualizar Datos';
        // En edición el stock se modifica vía movimientos para trazabilidad, pero dejamos ver el valor
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
