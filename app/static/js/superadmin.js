        // =========================================================
        // MÓDULO SUPER_ADMIN: TORRE DE CONTROL SAAS AGRUPADA
        // =========================================================
        let saOverviewData = null;
        let saCurrentTab = 'comercios';
        let saEstadoFilter = 'ALL';
        let saComerciosGroupMode = true;

        function getSaTabElements() {
            return {
                comercios: document.getElementById('saTabComercios'),
                facturacion: document.getElementById('saTabFacturacion'),
                auditoria: document.getElementById('saTabAuditoria')
            };
        }

        function switchSuperAdminTab(tabName) {
            saCurrentTab = tabName || 'comercios';
            const panels = getSaTabElements();
            Object.entries(panels).forEach(([key, el]) => {
                if (!el) return;
                el.classList.toggle('hidden', key !== saCurrentTab);
            });
            document.querySelectorAll('.sa-tab-btn').forEach(btn => {
                const active = btn.getAttribute('data-sa-tab') === saCurrentTab;
                btn.classList.toggle('bg-indigo-600', active);
                btn.classList.toggle('text-white', active);
                btn.classList.toggle('shadow-sm', active);
                btn.classList.toggle('text-slate-300', !active);
                btn.classList.toggle('hover:bg-slate-800/80', !active);
            });
        }

        function setSaEstadoFilter(estado) {
            saEstadoFilter = estado || 'ALL';
            document.querySelectorAll('.sa-estado-chip').forEach(chip => {
                const isSelected = chip.getAttribute('data-estado') === saEstadoFilter;
                chip.classList.toggle('bg-indigo-600', isSelected);
                chip.classList.toggle('text-white', isSelected);
                chip.classList.toggle('bg-slate-800', !isSelected);
            });
            renderSuperAdminComercios();
        }

        function toggleSaComerciosGroupMode() {
            saComerciosGroupMode = !saComerciosGroupMode;
            const lbl = document.getElementById('saToggleGroupLabel');
            if (lbl) lbl.textContent = saComerciosGroupMode ? 'Agrupado por estado' : 'Vista plana';
            renderSuperAdminComercios();
        }

        async function loadSuperAdminDashboard() {
            const metricsGrid = document.getElementById('saMetricsGrid');
            if (metricsGrid && !saOverviewData) {
                metricsGrid.innerHTML = `<div class="col-span-full bg-slate-900 border border-slate-800 rounded-2xl p-6 text-center text-slate-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Cargando Torre de Control SaaS...</div>`;
            }
            try {
                const data = await apiFetch('/api/v1/panel/superadmin/overview');
                saOverviewData = data;
                populateSaDynamicSelects(data);
                renderSuperAdminKPIs(data.metricas_saas || {});
                updateSaTabCounters(data);
                renderSuperAdminComercios();
                renderSuperAdminPlanes();
                renderSuperAdminFacturas();
                renderSuperAdminHealth(data.metricas_saas || {});
                renderSuperAdminAuditLogs();
                switchSuperAdminTab(saCurrentTab);
            } catch (err) {
                showToast('Error cargando SuperAdmin: ' + err.message, true);
            }
        }

        function populateSaDynamicSelects(data) {
            const planes = data.planes || [];
            const comercios = data.comercios || [];

            const planSelects = [
                { id: 'ncPlan', useCode: true, allOption: false },
                { id: 'saComerciosPlanFilter', useCode: true, allOption: true },
                { id: 'ecPlanId', useCode: false, allOption: false }
            ];
            planSelects.forEach(cfg => {
                const sel = document.getElementById(cfg.id);
                if (!sel) return;
                const prev = sel.value;
                let opts = cfg.allOption ? `<option value="ALL">Todos los planes</option>` : '';
                opts += planes
                    .filter(p => cfg.allOption || p.activo)
                    .map(p => `<option value="${escapeHtml(cfg.useCode ? p.codigo : String(p.id))}">${escapeHtml(p.nombre)} ($${Number(p.precio_mensual_usd || 0).toFixed(0)} USD/mes)</option>`)
                    .join('');
                sel.innerHTML = opts;
                if (prev && Array.from(sel.options).some(o => o.value === prev)) {
                    sel.value = prev;
                }
            });

            const faTenantSel = document.getElementById('faTenantId');
            if (faTenantSel) {
                const prevTenant = faTenantSel.value;
                faTenantSel.innerHTML = comercios.map(c =>
                    `<option value="${c.id}">${escapeHtml(c.nombre_comercial)} (${escapeHtml(c.plan?.nombre || 'Sin Plan')})</option>`
                ).join('');
                if (prevTenant && Array.from(faTenantSel.options).some(o => o.value === prevTenant)) {
                    faTenantSel.value = prevTenant;
                }
            }
        }

        function updateSaTabCounters(data) {
            const cEl = document.getElementById('saTabCountComercios');
            const fEl = document.getElementById('saTabCountFacturas');
            const aEl = document.getElementById('saTabCountAudit');
            if (cEl) cEl.textContent = (data.comercios || []).length;
            if (fEl) {
                const pending = (data.facturas || []).filter(f => f.estado_pago !== 'PAGADO').length;
                fEl.textContent = pending > 0 ? `${pending} pend.` : (data.facturas || []).length;
            }
            if (aEl) aEl.textContent = (data.audit_logs || []).length;
        }

        function renderSuperAdminKPIs(m) {
            const grid = document.getElementById('saMetricsGrid');
            if (!grid) return;
            grid.innerHTML = `
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <div class="flex items-center justify-between text-xs text-slate-400">
                        <span>Comercios Activos</span>
                        <i class="fa-solid fa-store text-indigo-400"></i>
                    </div>
                    <p class="text-2xl font-bold text-white">${m.comercios_activos || 0} <span class="text-sm text-slate-500 font-normal">/ ${m.total_comercios || 0}</span></p>
                    <p class="text-[11px] text-slate-400">Trial: ${m.comercios_trial || 0} · Por vencer: ${m.comercios_por_vencer || 0} · Susp: ${m.comercios_suspendidos || 0}</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <div class="flex items-center justify-between text-xs text-slate-400">
                        <span>MRR Mensual Estimado</span>
                        <i class="fa-solid fa-sack-dollar text-emerald-400"></i>
                    </div>
                    <p class="text-2xl font-bold text-emerald-400">$${Number(m.ingresos_fijos_mensuales_usd || 0).toFixed(2)} <span class="text-xs font-normal">USD</span></p>
                    <p class="text-[11px] text-slate-400">Cobrado en facturas: $${Number(m.facturas_pagadas_usd || 0).toFixed(2)} USD</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <div class="flex items-center justify-between text-xs text-slate-400">
                        <span>Facturas Pendientes</span>
                        <i class="fa-solid fa-file-invoice-dollar text-amber-400"></i>
                    </div>
                    <p class="text-2xl font-bold text-amber-400">$${Number(m.facturas_pendientes_usd || 0).toFixed(2)} <span class="text-xs font-normal">USD</span></p>
                    <p class="text-[11px] text-slate-400">${m.facturas_pendientes_count || 0} factura(s) por cobrar</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <div class="flex items-center justify-between text-xs text-slate-400">
                        <span>Volumen &amp; Usuarios SaaS</span>
                        <i class="fa-solid fa-chart-line text-sky-400"></i>
                    </div>
                    <p class="text-2xl font-bold text-sky-400">${m.total_pedidos_procesados || 0} <span class="text-xs font-normal text-slate-400">pedidos/mes</span></p>
                    <p class="text-[11px] text-slate-400">${m.total_usuarios || 0} usuarios registrados</p>
                </div>
            `;
        }

        function getEstadoBadgeMeta(estado, activo) {
            if (!activo || estado === 'SUSPENDIDO') {
                return { label: 'SUSPENDIDO', badgeCls: 'bg-red-500/15 text-red-400 border border-red-500/30', icon: 'fa-ban', order: 4 };
            }
            if (estado === 'POR_VENCER') {
                return { label: 'POR VENCER', badgeCls: 'bg-amber-500/15 text-amber-300 border border-amber-500/30', icon: 'fa-triangle-exclamation', order: 1 };
            }
            if (estado === 'TRIAL') {
                return { label: 'TRIAL', badgeCls: 'bg-sky-500/15 text-sky-300 border border-sky-500/30', icon: 'fa-flask', order: 2 };
            }
            return { label: 'ACTIVO', badgeCls: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30', icon: 'fa-circle-check', order: 3 };
        }

        function renderSuperAdminComercios() {
            const container = document.getElementById('saComerciosList');
            if (!container || !saOverviewData) return;

            const query = (document.getElementById('saComerciosSearch')?.value || '').trim().toLowerCase();
            const planFilter = document.getElementById('saComerciosPlanFilter')?.value || 'ALL';

            const filtered = (saOverviewData.comercios || []).filter(c => {
                if (saEstadoFilter !== 'ALL') {
                    const effectiveState = !c.activo ? 'SUSPENDIDO' : c.estado_suscripcion;
                    if (effectiveState !== saEstadoFilter) return false;
                }
                if (planFilter !== 'ALL' && (c.plan?.codigo || '') !== planFilter) {
                    return false;
                }
                if (query) {
                    const branchCodes = (c.sucursales || []).map(s => `${s.nombre_sucursal} ${s.codigo_whatsapp}`).join(' ');
                    const hay = `${c.nombre_comercial} ${c.slug} ${c.email_contacto || ''} ${c.telefono_propietario || ''} ${branchCodes}`.toLowerCase();
                    if (!hay.includes(query)) return false;
                }
                return true;
            });

            if (filtered.length === 0) {
                container.innerHTML = `<div class="bg-slate-900 border border-slate-800 rounded-2xl p-8 text-center text-slate-400 text-sm"><i class="fa-solid fa-store-slash text-2xl text-slate-600 mb-2 block"></i>No hay comercios que coincidan con los filtros actuales.</div>`;
                return;
            }

            if (!saComerciosGroupMode) {
                container.innerHTML = `<div class="space-y-4">${filtered.map(renderComercioCard).join('')}</div>`;
                return;
            }

            const groups = [
                { key: 'POR_VENCER', title: 'Atención Prioritaria: Por Vencer', icon: 'fa-triangle-exclamation text-amber-400', items: [] },
                { key: 'TRIAL', title: 'Comercios en Prueba (Trial)', icon: 'fa-flask text-sky-400', items: [] },
                { key: 'ACTIVO', title: 'Comercios Activos', icon: 'fa-circle-check text-emerald-400', items: [] },
                { key: 'SUSPENDIDO', title: 'Comercios Suspendidos / Bloqueados', icon: 'fa-ban text-red-400', items: [] }
            ];

            filtered.forEach(c => {
                const eff = !c.activo ? 'SUSPENDIDO' : (c.estado_suscripcion || 'ACTIVO');
                const target = groups.find(g => g.key === eff) || groups[2];
                target.items.push(c);
            });

            container.innerHTML = groups
                .filter(g => g.items.length > 0)
                .map(g => `
                    <details open class="bg-slate-900/50 border border-slate-800 rounded-2xl overflow-hidden">
                        <summary class="cursor-pointer select-none px-4 py-3 bg-slate-900 flex items-center justify-between text-sm font-bold text-white hover:bg-slate-800/70 transition">
                            <span class="flex items-center gap-2">
                                <i class="fa-solid ${g.icon}"></i>
                                <span>${g.title}</span>
                            </span>
                            <span class="text-xs px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-300 font-semibold">${g.items.length}</span>
                        </summary>
                        <div class="p-4 space-y-4">
                            ${g.items.map(renderComercioCard).join('')}
                        </div>
                    </details>
                `).join('');
        }

        function renderComercioCard(c) {
            const meta = getEstadoBadgeMeta(c.estado_suscripcion, c.activo);
            const isSuspended = !c.activo || c.estado_suscripcion === 'SUSPENDIDO';
            const limit = Number(c.plan?.limite_pedidos_mes || 0);
            const used = Number(c.pedidos_mes_actual || 0);
            const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
            const vencStr = c.fecha_vencimiento ? new Date(c.fecha_vencimiento).toLocaleDateString() : 'Sin vencimiento';

            return `
            <div class="bg-slate-900 border ${isSuspended ? 'border-red-900/50' : 'border-slate-800'} rounded-2xl p-5 flex flex-col xl:flex-row justify-between gap-5">
                <div class="space-y-3 flex-1 min-w-0">
                    <div class="flex items-center gap-2.5 flex-wrap">
                        <span class="text-lg font-bold text-white truncate">${escapeHtml(c.nombre_comercial)}</span>
                        <span class="text-[11px] font-bold px-2.5 py-0.5 rounded-full inline-flex items-center gap-1 ${meta.badgeCls}">
                            <i class="fa-solid ${meta.icon}"></i>
                            <span>${meta.label}</span>
                        </span>
                        <span class="text-[11px] font-semibold px-2.5 py-0.5 rounded-full bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">${escapeHtml(c.plan?.nombre || 'Sin Plan')}</span>
                        <span class="text-[11px] px-2 py-0.5 rounded-md bg-slate-800 text-slate-300">${escapeHtml(c.giro || 'RESTAURANTE')} · ${escapeHtml(c.simbolo_moneda || 'C$')} (${escapeHtml(c.moneda || 'NIO')})</span>
                        ${c.facturas_pendientes > 0 ? `<span class="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30"><i class="fa-solid fa-receipt mr-1"></i>${c.facturas_pendientes} factura(s) pend.</span>` : ''}
                    </div>

                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-xs text-slate-400">
                        <div><i class="fa-solid fa-envelope text-slate-500 mr-1.5"></i>${escapeHtml(c.email_contacto || 'Sin correo')}</div>
                        <div><i class="fa-solid fa-phone text-slate-500 mr-1.5"></i>${escapeHtml(c.telefono_propietario || 'Sin teléfono')}</div>
                        <div><i class="fa-solid fa-calendar-day text-slate-500 mr-1.5"></i>Vence: <span class="text-slate-200 font-medium">${vencStr}</span></div>
                        <div><i class="fa-solid fa-users text-slate-500 mr-1.5"></i>${c.usuarios_count || 0} usuario(s) · <span class="font-mono text-slate-300">${escapeHtml(c.slug)}</span></div>
                    </div>

                    <!-- Barra de Consumo Mensual del Plan -->
                    <div class="max-w-md space-y-1">
                        <div class="flex justify-between text-[11px] text-slate-400">
                            <span>Consumo de pedidos del mes</span>
                            <span class="font-semibold text-slate-200">${used} / ${limit || '∞'} (${pct}%)</span>
                        </div>
                        <div class="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
                            <div class="h-full ${pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-amber-400' : 'bg-emerald-500'}" style="width: ${pct}%"></div>
                        </div>
                    </div>

                    <!-- Chips de Módulos -->
                    <div class="flex flex-wrap gap-2 text-[11px]">
                        <span class="px-2 py-0.5 rounded-md ${c.modo_pago_api_activo ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20' : 'bg-slate-800 text-slate-400'}">
                            <i class="fa-solid fa-credit-card mr-1"></i>Modo 2 Pagos API: ${c.modo_pago_api_activo ? 'Activo' : 'Simulación'}
                        </span>
                        <span class="px-2 py-0.5 rounded-md ${c.reservaciones_habilitadas ? 'bg-sky-500/10 text-sky-300 border border-sky-500/20' : 'bg-slate-800 text-slate-400'}">
                            <i class="fa-solid fa-chair mr-1"></i>Mesas &amp; Reservas: ${c.reservaciones_habilitadas ? 'Habilitado' : 'Deshabilitado'}
                        </span>
                    </div>

                    <!-- Acciones Operativas sobre el Comercio -->
                    <div class="flex flex-wrap gap-2 pt-1">
                        <button type="button" onclick="inspectComercioAsSuperAdmin(${c.id}, '${escapeHtml(c.nombre_comercial).replace(/'/g, "\\'")}')" class="bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-eye"></i>
                            <span>Entrar al Panel</span>
                        </button>
                        <button type="button" onclick="openModalEditarComercioAdmin(${c.id})" class="bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-pen-to-square"></i>
                            <span>Editar / Plan</span>
                        </button>
                        <button type="button" onclick="openModalUsuariosComercioAdmin(${c.id})" class="bg-sky-500/10 hover:bg-sky-500/20 text-sky-300 border border-sky-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-users-gear"></i>
                            <span>Usuarios (${c.usuarios_count || 0})</span>
                        </button>
                        <button type="button" onclick="openModalGenerarFacturaSaaS(${c.id})" class="bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-file-invoice-dollar"></i>
                            <span>Facturar</span>
                        </button>
                        <button type="button" onclick="poblarDemoComercio(this, ${c.id})" title="Cargar productos, inventario y pedidos de prueba según su giro" class="bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 border border-amber-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-wand-magic-sparkles"></i>
                            <span>Poblar Datos Demo</span>
                        </button>
                        ${isSuspended
                            ? `<button type="button" onclick="toggleEstadoSuscripcion(this, ${c.id}, 'ACTIVO')" class="bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 border border-emerald-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition"><i class="fa-solid fa-circle-check"></i><span>Reactivar</span></button>`
                            : `<button type="button" onclick="toggleEstadoSuscripcion(this, ${c.id}, 'SUSPENDIDO')" class="bg-red-500/15 hover:bg-red-500/25 text-red-300 border border-red-500/30 px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition"><i class="fa-solid fa-ban"></i><span>Suspender</span></button>`
                        }
                    </div>
                </div>

                <!-- Sucursales y QR WhatsApp -->
                <div class="flex gap-3 flex-wrap xl:justify-end items-start">
                    ${(c.sucursales || []).map(s => `
                        <div class="bg-slate-950 border border-slate-800 rounded-xl p-3 flex items-center gap-3 w-full sm:w-auto">
                            <img src="${s.qr_data_uri}" alt="QR ${escapeHtml(s.codigo_whatsapp)}" class="w-16 h-16 rounded-lg bg-white p-1 shrink-0">
                            <div class="space-y-1 min-w-0">
                                <p class="text-xs font-bold text-white truncate">${escapeHtml(s.nombre_sucursal)}</p>
                                <p class="text-[11px] font-mono text-emerald-400">Código: ${escapeHtml(s.codigo_whatsapp)}</p>
                                <a href="${s.deep_link_whatsapp}" target="_blank" rel="noopener noreferrer" class="text-[11px] text-indigo-400 hover:underline inline-flex items-center gap-1">
                                    <i class="fa-brands fa-whatsapp"></i>
                                    <span>Probar Link</span>
                                    <i class="fa-solid fa-arrow-up-right-from-square text-[9px]"></i>
                                </a>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>`;
        }

        async function toggleEstadoSuscripcion(btn, tenantId, nuevoEstado) {
            const origHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><span>Aplicando...</span>`;
            }
            try {
                await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}/suscripcion`, {
                    method: 'PATCH',
                    body: { estado_suscripcion: nuevoEstado }
                });
                showToast(`Comercio actualizado a ${nuevoEstado}`);
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = origHtml;
                }
            }
        }

        // =========================================================
        // PLANES SAAS & FACTURACIÓN
        // =========================================================
        function renderSuperAdminPlanes() {
            const list = document.getElementById('saPlanesList');
            if (!list || !saOverviewData) return;
            const planes = saOverviewData.planes || [];
            if (planes.length === 0) {
                list.innerHTML = `<div class="col-span-full text-center text-slate-400 text-sm py-6">No hay planes SaaS registrados.</div>`;
                return;
            }
            list.innerHTML = planes.map(p => `
                <div class="bg-slate-950 border ${p.activo ? 'border-slate-800' : 'border-slate-800/50 opacity-75'} rounded-2xl p-4 flex flex-col justify-between gap-4">
                    <div class="space-y-2.5">
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">${escapeHtml(p.codigo)}</span>
                                <h4 class="text-base font-bold text-white mt-1.5">${escapeHtml(p.nombre)}</h4>
                            </div>
                            <div class="text-right">
                                <p class="text-xl font-bold text-emerald-400">$${Number(p.precio_mensual_usd || 0).toFixed(2)}</p>
                                <p class="text-[10px] text-slate-500">USD / mes</p>
                            </div>
                        </div>
                        <p class="text-xs text-slate-400">${escapeHtml(p.descripcion || 'Sin descripción comercial')}</p>
                        <div class="grid grid-cols-2 gap-1.5 text-[11px] text-slate-300 pt-1">
                            <div><i class="fa-solid fa-receipt text-slate-500 mr-1"></i>Límite: ${p.limite_pedidos_mes} ped/mes</div>
                            <div><i class="fa-solid fa-shop text-slate-500 mr-1"></i>Máx sucursales: ${p.max_sucursales}</div>
                            <div><i class="fa-solid fa-percent text-slate-500 mr-1"></i>Comisión: ${p.porcentaje_comision_pedido}%</div>
                            <div><i class="fa-solid fa-building text-slate-500 mr-1"></i>Suscritos: <strong>${p.comercios_suscritos || 0}</strong></div>
                        </div>
                        <div class="flex flex-wrap gap-1.5 text-[10px] pt-1">
                            <span class="px-2 py-0.5 rounded ${p.incluye_intervencion_humana ? 'bg-emerald-500/10 text-emerald-300' : 'bg-slate-800 text-slate-500'}">Chat Humano</span>
                            <span class="px-2 py-0.5 rounded ${p.incluye_impresion_termica ? 'bg-emerald-500/10 text-emerald-300' : 'bg-slate-800 text-slate-500'}">Tickets 58/80mm</span>
                            <span class="px-2 py-0.5 rounded ${p.incluye_reservacion_mesas ? 'bg-emerald-500/10 text-emerald-300' : 'bg-slate-800 text-slate-500'}">Mesas &amp; Reservas</span>
                        </div>
                    </div>
                    <div class="flex justify-between items-center border-t border-slate-800 pt-3 text-xs">
                        <span class="${p.activo ? 'text-emerald-400' : 'text-red-400'} font-semibold">${p.activo ? 'Disponible' : 'Oculto'}</span>
                        <button type="button" onclick="openModalPlanSaaS(${p.id})" class="bg-slate-800 hover:bg-slate-700 text-slate-200 px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-pen"></i>
                            <span>Editar Plan</span>
                        </button>
                    </div>
                </div>
            `).join('');
        }

        function renderSuperAdminFacturas() {
            const list = document.getElementById('saFacturasList');
            if (!list || !saOverviewData) return;

            const estadoFilter = document.getElementById('saFacturasEstadoFilter')?.value || 'ALL';
            const facturas = (saOverviewData.facturas || []).filter(f => {
                if (estadoFilter === 'PENDIENTE') return f.estado_pago !== 'PAGADO';
                if (estadoFilter === 'PAGADO') return f.estado_pago === 'PAGADO';
                return true;
            });

            if (facturas.length === 0) {
                list.innerHTML = `<div class="bg-slate-950 border border-slate-800 rounded-xl p-6 text-center text-slate-400 text-sm">No hay facturas SaaS en esta vista. Usa "Generar Factura" para emitir un cobro mensual.</div>`;
                return;
            }

            const pendientes = facturas.filter(f => f.estado_pago !== 'PAGADO');
            const pagadas = facturas.filter(f => f.estado_pago === 'PAGADO');

            const renderFacturaRow = f => {
                const isPaid = f.estado_pago === 'PAGADO';
                return `
                    <div class="bg-slate-950 border border-slate-800 rounded-xl p-3.5 flex flex-wrap items-center justify-between gap-3">
                        <div class="space-y-1">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="font-bold text-sm text-white">${escapeHtml(f.comercio_nombre)}</span>
                                <span class="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">Periodo ${escapeHtml(f.periodo_mes)}</span>
                                <span class="text-[11px] font-bold px-2 py-0.5 rounded-full ${isPaid ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' : 'bg-amber-500/15 text-amber-300 border border-amber-500/30'}">${escapeHtml(f.estado_pago)}</span>
                            </div>
                            <p class="text-xs text-slate-400">
                                Fijo: $${Number(f.monto_mensualidad_fija || 0).toFixed(2)} + Comisiones (${f.pedidos_contabilizados || 0} ped): $${Number(f.monto_comision_pedidos || 0).toFixed(2)}
                                ${f.referencia_pago ? ` · Ref: <span class="text-slate-300 font-mono">${escapeHtml(f.referencia_pago)}</span>` : ''}
                            </p>
                        </div>
                        <div class="flex items-center gap-3">
                            <p class="text-base font-bold ${isPaid ? 'text-emerald-400' : 'text-amber-300'}">$${Number(f.total_a_pagar || 0).toFixed(2)} USD</p>
                            ${!isPaid
                                ? `<button type="button" onclick="marcarFacturaPagada(${f.id})" class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition"><i class="fa-solid fa-check"></i><span>Marcar Pagada</span></button>`
                                : `<button type="button" onclick="revertirFacturaPendiente(${f.id})" class="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-3 py-1.5 rounded-lg transition">Marcar Pendiente</button>`
                            }
                        </div>
                    </div>
                `;
            };

            let html = '';
            if (pendientes.length > 0) {
                html += `
                    <details open class="bg-slate-950/60 border border-amber-500/20 rounded-xl overflow-hidden">
                        <summary class="cursor-pointer px-4 py-2.5 bg-amber-500/10 text-amber-300 text-xs font-bold flex items-center justify-between">
                            <span><i class="fa-solid fa-clock mr-1.5"></i>Facturas Pendientes / Por Cobrar</span>
                            <span>${pendientes.length}</span>
                        </summary>
                        <div class="p-3 space-y-2.5">${pendientes.map(renderFacturaRow).join('')}</div>
                    </details>
                `;
            }
            if (pagadas.length > 0) {
                html += `
                    <details ${pendientes.length === 0 ? 'open' : ''} class="bg-slate-950/60 border border-slate-800 rounded-xl overflow-hidden">
                        <summary class="cursor-pointer px-4 py-2.5 bg-slate-900 text-emerald-400 text-xs font-bold flex items-center justify-between">
                            <span><i class="fa-solid fa-circle-check mr-1.5"></i>Historial de Facturas Pagadas</span>
                            <span>${pagadas.length}</span>
                        </summary>
                        <div class="p-3 space-y-2.5">${pagadas.map(renderFacturaRow).join('')}</div>
                    </details>
                `;
            }
            list.innerHTML = html;
        }

        async function marcarFacturaPagada(facturaId) {
            const ref = window.prompt('Ingresa la referencia de pago / transferencia (opcional):', `PAGO-${facturaId}`);
            if (ref === null) return;
            try {
                await apiFetch(`/api/v1/panel/superadmin/facturas/${facturaId}`, {
                    method: 'PATCH',
                    body: { estado_pago: 'PAGADO', referencia_pago: ref || null, reactivar_comercio: true }
                });
                showToast('Factura marcada como PAGADO y comercio reactivado si estaba suspendido.');
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function revertirFacturaPendiente(facturaId) {
            try {
                await apiFetch(`/api/v1/panel/superadmin/facturas/${facturaId}`, {
                    method: 'PATCH',
                    body: { estado_pago: 'PENDIENTE', reactivar_comercio: false }
                });
                showToast('Factura devuelta a estado PENDIENTE.');
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // INFRAESTRUCTURA & AUDITORÍA ISO 27001
        // =========================================================
        function renderSuperAdminHealth(m) {
            const grid = document.getElementById('saSystemHealthGrid');
            if (!grid) return;
            grid.innerHTML = `
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] text-slate-400"><i class="fa-brands fa-whatsapp text-emerald-400 mr-1.5"></i>Proveedor WhatsApp</p>
                    <p class="text-sm font-bold text-white">${escapeHtml(m.whatsapp_provider || 'TWILIO')}</p>
                    <p class="text-xs font-mono text-emerald-400">${escapeHtml(m.whatsapp_bot_number || 'Sin número')}</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] text-slate-400"><i class="fa-solid fa-robot text-indigo-400 mr-1.5"></i>Motor de IA</p>
                    <p class="text-sm font-bold text-white">${escapeHtml(m.ai_priority || 'Ollama -> Gemini -> OpenAI')}</p>
                    <p class="text-xs text-slate-400">Fallback automático en cascada</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] text-slate-400"><i class="fa-solid fa-credit-card text-amber-400 mr-1.5"></i>Pasarela Global</p>
                    <p class="text-sm font-bold text-white">${escapeHtml(m.payment_mode || 'SIMULATION')}</p>
                    <p class="text-xs text-slate-400">Listo para Stripe / Checkout Pro</p>
                </div>
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] text-slate-400"><i class="fa-solid fa-database text-cyan-400 mr-1.5"></i>Base de Datos</p>
                    <p class="text-sm font-bold text-white">PostgreSQL Multi-Tenant</p>
                    <p class="text-xs font-mono text-cyan-300">Schema: ${escapeHtml(m.database_schema || 'public')}</p>
                </div>
            `;
        }

        function renderSuperAdminAuditLogs() {
            const list = document.getElementById('saAuditLogsList');
            if (!list || !saOverviewData) return;
            const query = (document.getElementById('saAuditSearch')?.value || '').trim().toLowerCase();
            const tipoFilter = document.getElementById('saAuditTipoFilter')?.value || 'ALL';

            const logs = (saOverviewData.audit_logs || []).filter(l => {
                if (tipoFilter !== 'ALL' && (l.recurso_tipo || '') !== tipoFilter) return false;
                if (query) {
                    const hay = `${l.accion} ${l.actor_email || ''} ${l.recurso_tipo || ''} ${JSON.stringify(l.detalles || {})}`.toLowerCase();
                    if (!hay.includes(query)) return false;
                }
                return true;
            });

            if (logs.length === 0) {
                list.innerHTML = `<div class="text-center text-slate-500 text-xs py-6">No hay registros de auditoría que coincidan con el filtro.</div>`;
                return;
            }

            list.innerHTML = logs.map(l => `
                <div class="bg-slate-950 border border-slate-800 rounded-xl p-3 flex flex-wrap justify-between items-center gap-2 text-xs">
                    <div class="space-y-0.5 min-w-0">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="font-bold text-cyan-300">${escapeHtml(l.accion)}</span>
                            <span class="text-[10px] px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">${escapeHtml(l.recurso_tipo || 'SISTEMA')}${l.recurso_id ? '#' + l.recurso_id : ''}</span>
                            ${l.tenant_id ? `<span class="text-[10px] px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300">Tenant #${l.tenant_id}</span>` : ''}
                        </div>
                        <p class="text-[11px] text-slate-400">
                            Actor: <span class="text-slate-200">${escapeHtml(l.actor_email || 'Sistema')}</span>
                            ${l.ip_origen ? ` · IP: <span class="font-mono">${escapeHtml(l.ip_origen)}</span>` : ''}
                        </p>
                    </div>
                    <span class="text-[11px] text-slate-500 font-mono">${new Date(l.creado_en).toLocaleString()}</span>
                </div>
            `).join('');
        }

        // =========================================================
        // MODALES SUPER_ADMIN: ALTA Y EDICIÓN DE COMERCIOS
        // =========================================================
        function openModalNuevoComercio() {
            const form = document.getElementById('formNuevoComercio');
            if (form) form.reset();
            const passInput = document.getElementById('ncDuenoPass');
            if (passInput && !passInput.value) passInput.value = 'Demo#2026!';
            const envioInput = document.getElementById('ncCostoEnvio');
            if (envioInput && !envioInput.value) envioInput.value = '35';
            document.getElementById('modalNuevoComercio')?.classList.remove('hidden');
        }

        function closeModalNuevoComercio() {
            document.getElementById('modalNuevoComercio')?.classList.add('hidden');
        }

        function autoFillComercioSlug() {
            const nombreEl = document.getElementById('ncNombre');
            const slugEl = document.getElementById('ncSlug');
            if (!nombreEl || !slugEl) return;
            slugEl.value = nombreEl.value
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^a-z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '');
        }

        async function handleCrearComercio(e) {
            e.preventDefault();
            const btn = document.getElementById('btnSubmitNuevoComercio');
            const origHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><span>Creando comercio...</span>`;
            }
            const monedaRaw = (document.getElementById('ncMoneda')?.value || 'NIO|C$').split('|');
            try {
                await apiFetch('/api/v1/panel/superadmin/comercios', {
                    method: 'POST',
                    body: {
                        nombre_comercial: document.getElementById('ncNombre').value.trim(),
                        slug: document.getElementById('ncSlug').value.trim(),
                        giro: document.getElementById('ncGiro').value,
                        plan_codigo: document.getElementById('ncPlan').value,
                        moneda: monedaRaw[0] || 'NIO',
                        simbolo_moneda: monedaRaw[1] || 'C$',
                        telefono_propietario: document.getElementById('ncTelefono')?.value.trim() || null,
                        nombre_sucursal_principal: document.getElementById('ncSucursal').value.trim(),
                        direccion_sucursal: document.getElementById('ncDireccion')?.value.trim() || 'Centro',
                        costo_envio_base: Number(document.getElementById('ncCostoEnvio')?.value || 35),
                        nombre_dueno: document.getElementById('ncDuenoNombre').value.trim(),
                        email_dueno: document.getElementById('ncDuenoEmail').value.trim(),
                        password_dueno: document.getElementById('ncDuenoPass').value,
                        poblar_datos_demo: Boolean(document.getElementById('ncPoblarDemo')?.checked)
                    }
                });
                closeModalNuevoComercio();
                showToast('Nuevo comercio creado correctamente.');
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = origHtml;
                }
            }
        }

        async function poblarDemoComercio(btn, tenantId) {
            const origHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i><span>Cargando...</span>`;
            }
            try {
                const res = await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}/poblar-demo`, {
                    method: 'POST'
                });
                showToast(`Datos demo cargados (${res.giro}): +${res.productos_creados} productos, +${res.insumos_creados} inventario, +${res.pedidos_creados} pedidos.`);
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = origHtml;
                }
            }
        }
        window.poblarDemoComercio = poblarDemoComercio;

        function openModalEditarComercioAdmin(tenantId) {
            const c = (saOverviewData?.comercios || []).find(item => item.id === tenantId);
            if (!c) return;
            document.getElementById('ecTenantId').value = c.id;
            document.getElementById('ecTituloNombre').textContent = c.nombre_comercial;
            document.getElementById('ecNombre').value = c.nombre_comercial || '';
            document.getElementById('ecGiro').value = typeof getGiroProfile === 'function' ? getGiroProfile(c.giro).code : (c.giro || 'RESTAURANTE');
            document.getElementById('ecEmail').value = c.email_contacto || '';
            document.getElementById('ecTelefono').value = c.telefono_propietario || '';
            document.getElementById('ecMoneda').value = `${c.moneda || 'NIO'}|${c.simbolo_moneda || 'C$'}`;
            if (c.plan?.id) document.getElementById('ecPlanId').value = String(c.plan.id);
            document.getElementById('ecEstado').value = c.estado_suscripcion || 'ACTIVO';
            document.getElementById('ecExtenderDias').value = 0;
            document.getElementById('ecActivo').checked = Boolean(c.activo);
            document.getElementById('ecModoPagoApi').checked = Boolean(c.modo_pago_api_activo);
            document.getElementById('ecReservaciones').checked = Boolean(c.reservaciones_habilitadas);
            document.getElementById('modalEditarComercioAdmin')?.classList.remove('hidden');
        }

        function closeModalEditarComercioAdmin() {
            document.getElementById('modalEditarComercioAdmin')?.classList.add('hidden');
        }

        async function handleEditarComercioAdmin(e) {
            e.preventDefault();
            const tenantId = Number(document.getElementById('ecTenantId').value);
            const monedaRaw = (document.getElementById('ecMoneda').value || 'NIO|C$').split('|');
            const extenderDias = Number(document.getElementById('ecExtenderDias').value || 0);
            try {
                await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}`, {
                    method: 'PUT',
                    body: {
                        nombre_comercial: document.getElementById('ecNombre').value.trim(),
                        giro: document.getElementById('ecGiro').value,
                        email_contacto: document.getElementById('ecEmail').value.trim() || null,
                        telefono_propietario: document.getElementById('ecTelefono').value.trim() || null,
                        moneda: monedaRaw[0] || 'NIO',
                        simbolo_moneda: monedaRaw[1] || 'C$',
                        plan_id: Number(document.getElementById('ecPlanId').value),
                        estado_suscripcion: document.getElementById('ecEstado').value,
                        extender_dias: extenderDias > 0 ? extenderDias : null,
                        activo: document.getElementById('ecActivo').checked,
                        modo_pago_api_activo: document.getElementById('ecModoPagoApi').checked,
                        reservaciones_habilitadas: document.getElementById('ecReservaciones').checked
                    }
                });
                closeModalEditarComercioAdmin();
                showToast('Comercio actualizado correctamente.');
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // MODALES SUPER_ADMIN: USUARIOS DEL COMERCIO
        // =========================================================
        async function openModalUsuariosComercioAdmin(tenantId) {
            document.getElementById('ucTenantId').value = tenantId;
            document.getElementById('ucUsuariosList').innerHTML = `<div class="text-center text-slate-400 text-xs py-4"><i class="fa-solid fa-spinner fa-spin mr-1.5"></i>Cargando usuarios...</div>`;
            document.getElementById('modalUsuariosComercioAdmin')?.classList.remove('hidden');
            try {
                const data = await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}/usuarios`);
                document.getElementById('ucComercioNombre').textContent = data.comercio_nombre || '';
                const sucSel = document.getElementById('ucNuevaSucursal');
                if (sucSel) {
                    sucSel.innerHTML = `<option value="">Todas las sucursales</option>` + (data.sucursales || []).map(s =>
                        `<option value="${s.id}">${escapeHtml(s.nombre_sucursal)}</option>`
                    ).join('');
                }
                renderUsuariosComercioList(data.usuarios || []);
            } catch (err) {
                showToast(err.message, true);
            }
        }

        function closeModalUsuariosComercioAdmin() {
            document.getElementById('modalUsuariosComercioAdmin')?.classList.add('hidden');
        }

        function renderUsuariosComercioList(usuarios) {
            const list = document.getElementById('ucUsuariosList');
            if (!list) return;
            if (usuarios.length === 0) {
                list.innerHTML = `<div class="text-center text-slate-500 text-xs py-4">Este comercio aún no tiene usuarios registrados.</div>`;
                return;
            }
            list.innerHTML = usuarios.map(u => `
                <div class="bg-slate-950 border border-slate-800 rounded-xl p-3 flex flex-wrap items-center justify-between gap-3">
                    <div class="space-y-0.5">
                        <div class="flex items-center gap-2 flex-wrap">
                            <span class="text-sm font-bold text-white">${escapeHtml(u.nombre_completo)}</span>
                            <span class="text-[10px] font-semibold px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">${escapeHtml(u.rol)}</span>
                            <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${u.activo ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}">${u.activo ? 'ACTIVO' : 'BLOQUEADO'}</span>
                        </div>
                        <p class="text-xs text-slate-400">${escapeHtml(u.email)} · ${escapeHtml(u.sucursal_nombre || 'Todas las sucursales')}</p>
                    </div>
                    <div class="flex items-center gap-2">
                        <button type="button" onclick="resetPasswordUsuarioAdmin(${u.id})" class="bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs px-2.5 py-1.5 rounded-lg flex items-center gap-1 transition">
                            <i class="fa-solid fa-key"></i>
                            <span>Clave</span>
                        </button>
                        <button type="button" onclick="toggleUsuarioActivoAdmin(${u.id}, ${!u.activo})" class="${u.activo ? 'bg-red-500/15 hover:bg-red-500/25 text-red-300' : 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300'} text-xs font-semibold px-2.5 py-1.5 rounded-lg transition">
                            ${u.activo ? 'Bloquear' : 'Activar'}
                        </button>
                    </div>
                </div>
            `).join('');
        }

        async function handleCrearUsuarioComercioAdmin(e) {
            e.preventDefault();
            const tenantId = Number(document.getElementById('ucTenantId').value);
            const sucursalVal = document.getElementById('ucNuevaSucursal').value;
            try {
                await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}/usuarios`, {
                    method: 'POST',
                    body: {
                        nombre_completo: document.getElementById('ucNuevoNombre').value.trim(),
                        email: document.getElementById('ucNuevoEmail').value.trim(),
                        password: document.getElementById('ucNuevoPass').value,
                        rol: document.getElementById('ucNuevoRol').value,
                        sucursal_id: sucursalVal ? Number(sucursalVal) : null
                    }
                });
                document.getElementById('ucNuevoNombre').value = '';
                document.getElementById('ucNuevoEmail').value = '';
                showToast('Usuario agregado al comercio.');
                await openModalUsuariosComercioAdmin(tenantId);
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function toggleUsuarioActivoAdmin(usuarioId, nuevoActivo) {
            const tenantId = Number(document.getElementById('ucTenantId').value);
            try {
                await apiFetch(`/api/v1/panel/superadmin/usuarios/${usuarioId}`, {
                    method: 'PATCH',
                    body: { activo: nuevoActivo }
                });
                showToast(nuevoActivo ? 'Usuario activado.' : 'Usuario bloqueado.');
                await openModalUsuariosComercioAdmin(tenantId);
            } catch (err) {
                showToast(err.message, true);
            }
        }

        async function resetPasswordUsuarioAdmin(usuarioId) {
            const nueva = window.prompt('Nueva contraseña temporal (mín. 6 caracteres):', 'Reset#2026!');
            if (!nueva) return;
            const tenantId = Number(document.getElementById('ucTenantId').value);
            try {
                await apiFetch(`/api/v1/panel/superadmin/usuarios/${usuarioId}`, {
                    method: 'PATCH',
                    body: { nueva_password: nueva }
                });
                showToast('Contraseña actualizada correctamente.');
                await openModalUsuariosComercioAdmin(tenantId);
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // MODALES SUPER_ADMIN: PLANES SAAS & GENERAR FACTURA
        // =========================================================
        function openModalPlanSaaS(planId = null) {
            const form = document.getElementById('formPlanSaaS');
            if (form) form.reset();
            const titleEl = document.getElementById('modalPlanSaaSTitle');
            if (planId && saOverviewData?.planes) {
                const p = saOverviewData.planes.find(item => item.id === planId);
                if (!p) return;
                document.getElementById('plId').value = p.id;
                document.getElementById('plCodigo').value = p.codigo || '';
                document.getElementById('plNombre').value = p.nombre || '';
                document.getElementById('plPrecio').value = p.precio_mensual_usd ?? 0;
                document.getElementById('plLimitePedidos').value = p.limite_pedidos_mes ?? 500;
                document.getElementById('plComision').value = p.porcentaje_comision_pedido ?? 0;
                document.getElementById('plTarifaExtra').value = p.tarifa_fija_pedido_extra_usd ?? 0;
                document.getElementById('plMaxSucursales').value = p.max_sucursales ?? 1;
                document.getElementById('plDescripcion').value = p.descripcion || '';
                document.getElementById('plActivo').checked = Boolean(p.activo);
                document.getElementById('plIntervencion').checked = Boolean(p.incluye_intervencion_humana);
                document.getElementById('plImpresion').checked = Boolean(p.incluye_impresion_termica);
                document.getElementById('plReservaciones').checked = Boolean(p.incluye_reservacion_mesas);
                if (titleEl) titleEl.innerHTML = `<i class="fa-solid fa-layer-group text-indigo-400"></i><span>Editar Plan: ${escapeHtml(p.nombre)}</span>`;
            } else {
                document.getElementById('plId').value = '';
                document.getElementById('plActivo').checked = true;
                document.getElementById('plIntervencion').checked = true;
                document.getElementById('plImpresion').checked = true;
                document.getElementById('plReservaciones').checked = false;
                if (titleEl) titleEl.innerHTML = `<i class="fa-solid fa-layer-group text-indigo-400"></i><span>Crear Nuevo Plan SaaS</span>`;
            }
            document.getElementById('modalPlanSaaS')?.classList.remove('hidden');
        }

        function closeModalPlanSaaS() {
            document.getElementById('modalPlanSaaS')?.classList.add('hidden');
        }

        async function handleGuardarPlanSaaS(e) {
            e.preventDefault();
            const planId = document.getElementById('plId').value;
            const payload = {
                codigo: document.getElementById('plCodigo').value.trim().toUpperCase(),
                nombre: document.getElementById('plNombre').value.trim(),
                descripcion: document.getElementById('plDescripcion').value.trim() || '',
                precio_mensual_usd: Number(document.getElementById('plPrecio').value || 0),
                limite_pedidos_mes: Number(document.getElementById('plLimitePedidos').value || 100),
                porcentaje_comision_pedido: Number(document.getElementById('plComision').value || 0),
                tarifa_fija_pedido_extra_usd: Number(document.getElementById('plTarifaExtra').value || 0),
                max_sucursales: Number(document.getElementById('plMaxSucursales').value || 1),
                incluye_intervencion_humana: document.getElementById('plIntervencion').checked,
                incluye_impresion_termica: document.getElementById('plImpresion').checked,
                incluye_reservacion_mesas: document.getElementById('plReservaciones').checked,
                activo: document.getElementById('plActivo').checked
            };
            try {
                if (planId) {
                    await apiFetch(`/api/v1/panel/superadmin/planes/${planId}`, { method: 'PUT', body: payload });
                    showToast('Plan SaaS actualizado.');
                } else {
                    await apiFetch('/api/v1/panel/superadmin/planes', { method: 'POST', body: payload });
                    showToast('Nuevo Plan SaaS creado.');
                }
                closeModalPlanSaaS();
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        function openModalGenerarFacturaSaaS(tenantId = null) {
            if (tenantId) {
                const sel = document.getElementById('faTenantId');
                if (sel) sel.value = String(tenantId);
            }
            const periodoEl = document.getElementById('faPeriodo');
            if (periodoEl && !periodoEl.value) {
                const now = new Date();
                periodoEl.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
            }
            document.getElementById('modalFacturaSaaS')?.classList.remove('hidden');
        }

        function closeModalGenerarFacturaSaaS() {
            document.getElementById('modalFacturaSaaS')?.classList.add('hidden');
        }

        async function handleGenerarFacturaSaaS(e) {
            e.preventDefault();
            const tenantId = Number(document.getElementById('faTenantId').value);
            try {
                await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}/facturas`, {
                    method: 'POST',
                    body: {
                        periodo_mes: document.getElementById('faPeriodo').value.trim() || null,
                        estado_pago: document.getElementById('faEstado').value,
                        referencia_pago: document.getElementById('faReferencia').value.trim() || null
                    }
                });
                closeModalGenerarFacturaSaaS();
                showToast('Factura SaaS emitida correctamente.');
                await loadSuperAdminDashboard();
                switchSuperAdminTab('facturacion');
            } catch (err) {
                showToast(err.message, true);
            }
        }
