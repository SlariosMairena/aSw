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
                auditoria: document.getElementById('saTabAuditoria'),
                repartidores: document.getElementById('saTabRepartidores')
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
                btn.classList.toggle('is-active', active);
            });
            if (saCurrentTab === 'repartidores' && typeof cargarFlotaSuperAdmin === 'function') {
                cargarFlotaSuperAdmin();
            }
        }

        function setSaEstadoFilter(estado) {
            saEstadoFilter = estado || 'ALL';
            document.querySelectorAll('.sa-estado-chip').forEach(chip => {
                const isSelected = chip.getAttribute('data-estado') === saEstadoFilter;
                chip.classList.toggle('bg-[#D6402B]', isSelected);
                chip.classList.toggle('text-white', isSelected);
                chip.classList.toggle('keep-white', isSelected);
                chip.classList.toggle('bg-[#EDE5D7]', !isSelected);
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
                <div class="glass rounded-2xl p-4 flex items-start justify-between gap-3">
                    <div>
                        <p class="text-[11px] font-bold uppercase tracking-wider text-[#5C6B62]">Comercios Activos</p>
                        <p class="text-2xl font-extrabold text-[#1A241E] mt-1">${m.comercios_activos || 0} <span class="text-sm font-semibold text-[#5C6B62]">/ ${m.total_comercios || 0}</span></p>
                        <p class="text-[11px] text-[#5C6B62] mt-1">Trial: ${m.comercios_trial || 0} · Por vencer: ${m.comercios_por_vencer || 0} · Susp: ${m.comercios_suspendidos || 0}</p>
                    </div>
                    <span class="w-10 h-10 rounded-xl bg-[#E4EFE9] text-[#1B4332] flex items-center justify-center shrink-0">
                        <i class="fa-solid fa-store"></i>
                    </span>
                </div>
                <div class="glass rounded-2xl p-4 flex items-start justify-between gap-3">
                    <div>
                        <p class="text-[11px] font-bold uppercase tracking-wider text-[#5C6B62]">MRR Mensual</p>
                        <p class="text-2xl font-extrabold text-[#1B4332] mt-1">$${Number(m.ingresos_fijos_mensuales_usd || 0).toFixed(2)} <span class="text-xs font-semibold">USD</span></p>
                        <p class="text-[11px] text-[#5C6B62] mt-1">Cobrado: $${Number(m.facturas_pagadas_usd || 0).toFixed(2)} USD</p>
                    </div>
                    <span class="w-10 h-10 rounded-xl bg-[#E4EFE9] text-[#1B4332] flex items-center justify-center shrink-0">
                        <i class="fa-solid fa-sack-dollar"></i>
                    </span>
                </div>
                <div class="glass rounded-2xl p-4 flex items-start justify-between gap-3">
                    <div>
                        <p class="text-[11px] font-bold uppercase tracking-wider text-[#5C6B62]">Facturas Pendientes</p>
                        <p class="text-2xl font-extrabold text-[#D6402B] mt-1">$${Number(m.facturas_pendientes_usd || 0).toFixed(2)} <span class="text-xs font-semibold">USD</span></p>
                        <p class="text-[11px] text-[#5C6B62] mt-1">${m.facturas_pendientes_count || 0} factura(s) por cobrar</p>
                    </div>
                    <span class="w-10 h-10 rounded-xl bg-[#FDECE9] text-[#D6402B] flex items-center justify-center shrink-0">
                        <i class="fa-solid fa-file-invoice-dollar"></i>
                    </span>
                </div>
                <div class="glass rounded-2xl p-4 flex items-start justify-between gap-3">
                    <div>
                        <p class="text-[11px] font-bold uppercase tracking-wider text-[#5C6B62]">Volumen SaaS</p>
                        <p class="text-2xl font-extrabold text-[#1A241E] mt-1">${m.total_pedidos_procesados || 0} <span class="text-xs font-semibold text-[#5C6B62]">ped/mes</span></p>
                        <p class="text-[11px] text-[#5C6B62] mt-1">${m.total_usuarios || 0} usuarios registrados</p>
                    </div>
                    <span class="w-10 h-10 rounded-xl bg-[#EDE5D7] text-[#1B4332] flex items-center justify-center shrink-0">
                        <i class="fa-solid fa-chart-line"></i>
                    </span>
                </div>
            `;
        }

        function getEstadoBadgeMeta(estado, activo) {
            if (!activo || estado === 'SUSPENDIDO') {
                return { label: 'SUSPENDIDO', badgeCls: 'bg-[#FDECE9] text-[#B8321F] border border-[#F6C2B9]', icon: 'fa-ban', order: 4 };
            }
            if (estado === 'POR_VENCER') {
                return { label: 'POR VENCER', badgeCls: 'bg-[#FEF3C7] text-[#92400E] border border-[#FDE68A]', icon: 'fa-triangle-exclamation', order: 1 };
            }
            if (estado === 'TRIAL') {
                return { label: 'TRIAL', badgeCls: 'bg-[#E0F2FE] text-[#0369A1] border border-[#BAE6FD]', icon: 'fa-flask', order: 2 };
            }
            return { label: 'ACTIVO', badgeCls: 'bg-[#E4EFE9] text-[#1B4332] border border-[#B7D4C5]', icon: 'fa-circle-check', order: 3 };
        }

        const saOpenComercioDetails = new Set();
        function toggleSaComercioDetail(tenantId) {
            const box = document.getElementById(`saComDetail_${tenantId}`);
            const chev = document.getElementById(`saComChev_${tenantId}`);
            if (!box) return;
            const willOpen = box.classList.contains('hidden');
            box.classList.toggle('hidden', !willOpen);
            if (willOpen) saOpenComercioDetails.add(tenantId);
            else saOpenComercioDetails.delete(tenantId);
            if (chev) chev.className = `fa-solid fa-chevron-${willOpen ? 'up' : 'down'} text-[10px]`;
        }
        window.toggleSaComercioDetail = toggleSaComercioDetail;

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
                container.innerHTML = `<div class="glass rounded-2xl p-8 text-center text-[#5C6B62] text-sm"><i class="fa-solid fa-store-slash text-2xl opacity-50 mb-2 block"></i>No hay comercios que coincidan con los filtros actuales.</div>`;
                return;
            }

            if (!saComerciosGroupMode) {
                container.innerHTML = `<div class="space-y-3">${filtered.map(renderComercioCard).join('')}</div>`;
                return;
            }

            const groups = [
                { key: 'POR_VENCER', title: 'Por Vencer', icon: 'fa-triangle-exclamation text-[#92400E]', items: [] },
                { key: 'TRIAL', title: 'En Prueba (Trial)', icon: 'fa-flask text-[#0369A1]', items: [] },
                { key: 'ACTIVO', title: 'Comercios Activos', icon: 'fa-circle-check text-[#1B4332]', items: [] },
                { key: 'SUSPENDIDO', title: 'Suspendidos', icon: 'fa-ban text-[#D6402B]', items: [] }
            ];

            filtered.forEach(c => {
                const eff = !c.activo ? 'SUSPENDIDO' : (c.estado_suscripcion || 'ACTIVO');
                const target = groups.find(g => g.key === eff) || groups[2];
                target.items.push(c);
            });

            container.innerHTML = groups
                .filter(g => g.items.length > 0)
                .map(g => `
                    <div class="space-y-2.5">
                        <div class="flex items-center justify-between px-1">
                            <span class="text-xs font-extrabold uppercase tracking-wider text-[#5C6B62] flex items-center gap-2">
                                <i class="fa-solid ${g.icon}"></i>
                                <span>${g.title}</span>
                            </span>
                            <span class="text-[11px] px-2.5 py-0.5 rounded-full bg-[#EDE5D7] text-[#1A241E] font-bold">${g.items.length}</span>
                        </div>
                        <div class="space-y-2.5">
                            ${g.items.map(renderComercioCard).join('')}
                        </div>
                    </div>
                `).join('');
        }

        function renderComercioCard(c) {
            const meta = getEstadoBadgeMeta(c.estado_suscripcion, c.activo);
            const isSuspended = !c.activo || c.estado_suscripcion === 'SUSPENDIDO';
            const limit = Number(c.plan?.limite_pedidos_mes || 0);
            const used = Number(c.pedidos_mes_actual || 0);
            const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
            const vencStr = c.fecha_vencimiento ? new Date(c.fecha_vencimiento).toLocaleDateString() : 'Sin vencimiento';
            const isOpen = saOpenComercioDetails.has(c.id);
            const initials = (c.nombre_comercial || 'C').split(' ').slice(0, 2).map(w => w[0] || '').join('').toUpperCase();

            return `
            <div class="glass rounded-2xl p-4 space-y-3 transition-all ${isSuspended ? 'opacity-85' : ''}">
                <!-- Fila Principal Compacta -->
                <div class="flex flex-wrap items-center justify-between gap-3">
                    <div class="flex items-center gap-3 min-w-0 flex-1">
                        <div class="w-10 h-10 rounded-xl bg-[#1B4332] text-white keep-white font-extrabold text-xs flex items-center justify-center shrink-0 shadow-sm">
                            ${escapeHtml(initials)}
                        </div>
                        <div class="min-w-0 flex-1">
                            <div class="flex items-center gap-2 flex-wrap">
                                <span class="text-sm sm:text-base font-extrabold text-[#1A241E] truncate">${escapeHtml(c.nombre_comercial)}</span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${meta.badgeCls}">
                                    <i class="fa-solid ${meta.icon}"></i>
                                    <span>${meta.label}</span>
                                </span>
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-[#EDE5D7] text-[#1A241E]">${escapeHtml(c.plan?.nombre || 'Sin Plan')}</span>
                                ${c.inventario_habilitado ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 border border-emerald-500/30"><i class="fa-solid fa-boxes-stacked mr-1"></i>Inventario</span>` : ''}
                                ${c.facturas_pendientes > 0 ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-[#FEF3C7] text-[#92400E] border border-[#FDE68A]">${c.facturas_pendientes} pend.</span>` : ''}
                            </div>
                            <p class="text-[11px] text-[#5C6B62] truncate mt-0.5">
                                ${escapeHtml(c.giro || 'RESTAURANTE')} · ${escapeHtml(c.simbolo_moneda || 'C$')} (${escapeHtml(c.moneda || 'NIO')}) · ${(c.sucursales || []).length} suc. · ${used}/${limit || '∞'} ped/mes
                            </p>
                        </div>
                    </div>

                    <!-- Acciones Limpias -->
                    <div class="flex flex-wrap items-center gap-1.5 shrink-0">
                        <button type="button" onclick="inspectComercioAsSuperAdmin(${c.id}, '${escapeHtml(c.nombre_comercial).replace(/'/g, "\\'")}')"
                            class="bg-[#1B4332] hover:bg-[#143326] text-white keep-white px-3 py-1.5 rounded-xl text-xs font-bold flex items-center gap-1.5 transition shadow-sm">
                            <i class="fa-solid fa-eye"></i>
                            <span>Entrar</span>
                        </button>
                        <button type="button" onclick="openModalEditarComercioAdmin(${c.id})"
                            class="bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] px-3 py-1.5 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-pen-to-square text-[#1B4332]"></i>
                            <span>Editar</span>
                        </button>
                        <button type="button" onclick="toggleSaComercioDetail(${c.id})"
                            class="bg-[#FFFDF8] hover:bg-[#EDE5D7] border border-[#DFD5C3] text-[#1A241E] px-2.5 py-1.5 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                            <span>Más</span>
                            <i id="saComChev_${c.id}" class="fa-solid fa-chevron-${isOpen ? 'up' : 'down'} text-[10px]"></i>
                        </button>
                    </div>
                </div>

                <!-- Panel Desplegable: Datos, Consumo, Acciones Secundarias y QRs -->
                <div id="saComDetail_${c.id}" class="${isOpen ? '' : 'hidden'} pt-3 border-t border-[#EDE5D7] space-y-3 text-xs">
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-[#5C6B62]">
                        <div><i class="fa-solid fa-envelope text-[#1B4332] mr-1.5"></i>${escapeHtml(c.email_contacto || 'Sin correo')}</div>
                        <div><i class="fa-solid fa-phone text-[#1B4332] mr-1.5"></i>${escapeHtml(c.telefono_propietario || 'Sin teléfono')}</div>
                        <div><i class="fa-solid fa-calendar-day text-[#1B4332] mr-1.5"></i>Vence: <strong class="text-[#1A241E]">${vencStr}</strong></div>
                        <div><i class="fa-solid fa-users text-[#1B4332] mr-1.5"></i>${c.usuarios_count || 0} usuario(s) · <span class="font-mono">${escapeHtml(c.slug)}</span></div>
                    </div>

                    <div class="max-w-sm space-y-1">
                        <div class="flex justify-between text-[11px] text-[#5C6B62]">
                            <span>Consumo mensual</span>
                            <strong class="text-[#1A241E]">${used} / ${limit || '∞'} (${pct}%)</strong>
                        </div>
                        <div class="h-1.5 w-full bg-[#EDE5D7] rounded-full overflow-hidden">
                            <div class="h-full ${pct >= 90 ? 'bg-[#D6402B]' : pct >= 70 ? 'bg-amber-500' : 'bg-[#1B4332]'}" style="width: ${pct}%"></div>
                        </div>
                    </div>

                    <div class="flex flex-wrap gap-1.5">
                        <button type="button" onclick="openModalUsuariosComercioAdmin(${c.id})" class="bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-users-gear text-[#1B4332]"></i><span>Usuarios (${c.usuarios_count || 0})</span>
                        </button>
                        <button type="button" onclick="openModalGenerarFacturaSaaS(${c.id})" class="bg-[#E4EFE9] hover:bg-[#D2E6DC] border border-[#B7D4C5] text-[#1B4332] px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-file-invoice-dollar"></i><span>Facturar</span>
                        </button>
                        <button type="button" onclick="toggleInventarioComercioAdmin(this, ${c.id}, ${!c.inventario_habilitado})"
                            class="${c.inventario_habilitado ? 'bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/40 text-emerald-300' : 'bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E]'} px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-boxes-stacked"></i><span>${c.inventario_habilitado ? 'Inventario: Activo' : 'Habilitar Inventario'}</span>
                        </button>
                        <button type="button" onclick="poblarDemoComercio(this, ${c.id})" class="bg-[#FEF3C7] hover:bg-[#FDE68A] border border-[#FDE68A] text-[#92400E] px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-wand-magic-sparkles"></i><span>Poblar Demo</span>
                        </button>
                        ${isSuspended
                            ? `<button type="button" onclick="toggleEstadoSuscripcion(this, ${c.id}, 'ACTIVO')" class="bg-[#E4EFE9] hover:bg-[#D2E6DC] border border-[#B7D4C5] text-[#1B4332] px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition"><i class="fa-solid fa-circle-check"></i><span>Reactivar</span></button>`
                            : `<button type="button" onclick="toggleEstadoSuscripcion(this, ${c.id}, 'SUSPENDIDO')" class="bg-[#FDECE9] hover:bg-[#F9D7D0] border border-[#F6C2B9] text-[#B8321F] px-2.5 py-1.5 rounded-lg text-[11px] font-bold flex items-center gap-1.5 transition"><i class="fa-solid fa-ban"></i><span>Suspender</span></button>`
                        }
                    </div>

                    ${(c.sucursales || []).length > 0 ? `
                        <div class="flex gap-2.5 flex-wrap pt-1">
                            ${(c.sucursales || []).map(s => `
                                <div class="bg-[#F5EFE4] border border-[#DFD5C3] rounded-xl p-2.5 flex items-center gap-2.5">
                                    <img src="${s.qr_data_uri}" alt="QR ${escapeHtml(s.codigo_whatsapp)}" class="w-14 h-14 rounded-lg bg-white p-1 shrink-0">
                                    <div class="space-y-0.5 min-w-0">
                                        <p class="text-xs font-bold text-[#1A241E] truncate">${escapeHtml(s.nombre_sucursal)}</p>
                                        <p class="text-[11px] font-mono text-[#1B4332]">${escapeHtml(s.codigo_whatsapp)}</p>
                                        <a href="${s.deep_link_whatsapp}" target="_blank" rel="noopener noreferrer" class="text-[11px] text-[#D6402B] hover:underline font-bold inline-flex items-center gap-1">
                                            <i class="fa-brands fa-whatsapp"></i><span>Link WA</span>
                                        </a>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    ` : ''}
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
                <div class="bg-[#FFFDF8] border border-[#DFD5C3] ${p.activo ? '' : 'opacity-70'} rounded-2xl p-4 flex flex-col justify-between gap-4 shadow-sm">
                    <div class="space-y-2.5">
                        <div class="flex items-start justify-between gap-2">
                            <div>
                                <span class="text-[10px] font-mono font-bold uppercase px-2 py-0.5 rounded bg-[#EDE5D7] text-[#1B4332]">${escapeHtml(p.codigo)}</span>
                                <h4 class="text-base font-extrabold text-[#1A241E] mt-1.5">${escapeHtml(p.nombre)}</h4>
                            </div>
                            <div class="text-right">
                                <p class="text-xl font-extrabold text-[#1B4332]">$${Number(p.precio_mensual_usd || 0).toFixed(2)}</p>
                                <p class="text-[10px] text-[#5C6B62]">USD / mes</p>
                            </div>
                        </div>
                        <p class="text-xs text-[#5C6B62]">${escapeHtml(p.descripcion || 'Sin descripción comercial')}</p>
                        <div class="grid grid-cols-2 gap-1.5 text-[11px] text-[#39483F] pt-1">
                            <div><i class="fa-solid fa-receipt text-[#1B4332] mr-1"></i>Límite: ${p.limite_pedidos_mes} ped/mes</div>
                            <div><i class="fa-solid fa-shop text-[#1B4332] mr-1"></i>Sucursales: ${p.max_sucursales}</div>
                            <div><i class="fa-solid fa-percent text-[#1B4332] mr-1"></i>Comisión: ${p.porcentaje_comision_pedido}%</div>
                            <div><i class="fa-solid fa-building text-[#1B4332] mr-1"></i>Suscritos: <strong>${p.comercios_suscritos || 0}</strong></div>
                        </div>
                        <div class="flex flex-wrap gap-1.5 text-[10px] pt-1">
                            <span class="px-2 py-0.5 rounded font-semibold ${p.incluye_intervencion_humana ? 'bg-[#E4EFE9] text-[#1B4332]' : 'bg-[#EDE5D7] text-[#5C6B62]'}">Chat Humano</span>
                            <span class="px-2 py-0.5 rounded font-semibold ${p.incluye_impresion_termica ? 'bg-[#E4EFE9] text-[#1B4332]' : 'bg-[#EDE5D7] text-[#5C6B62]'}">Tickets 58/80mm</span>
                            <span class="px-2 py-0.5 rounded font-semibold ${p.incluye_reservacion_mesas ? 'bg-[#E4EFE9] text-[#1B4332]' : 'bg-[#EDE5D7] text-[#5C6B62]'}">Mesas &amp; Reservas</span>
                        </div>
                    </div>
                    <div class="flex justify-between items-center border-t border-[#EDE5D7] pt-3 text-xs">
                        <span class="${p.activo ? 'text-[#1B4332]' : 'text-[#D6402B]'} font-bold">${p.activo ? 'Disponible' : 'Oculto'}</span>
                        <button type="button" onclick="openModalPlanSaaS(${p.id})" class="bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] px-3 py-1.5 rounded-lg font-bold flex items-center gap-1.5 transition">
                            <i class="fa-solid fa-pen text-[#1B4332]"></i>
                            <span>Editar</span>
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
                list.innerHTML = `<div class="p-6 text-center text-[#5C6B62] text-sm">No hay facturas SaaS en esta vista. Usa "Generar Factura" para emitir un cobro mensual.</div>`;
                return;
            }

            list.innerHTML = `
                <div class="rounded-xl overflow-hidden">
                    <div class="overflow-x-auto">
                        <table class="ui-zebra-table w-full text-left border-collapse text-xs">
                            <thead class="uppercase">
                                <tr>
                                    <th class="px-3.5 py-3">Comercio</th>
                                    <th class="px-3.5 py-3">Periodo</th>
                                    <th class="px-3.5 py-3">Estado</th>
                                    <th class="px-3.5 py-3 text-right">Total USD</th>
                                    <th class="px-3.5 py-3 text-right">Acción</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${facturas.map(f => {
                                    const isPaid = f.estado_pago === 'PAGADO';
                                    return `
                                    <tr>
                                        <td class="px-3.5 py-2.5 font-bold text-[#1A241E]">
                                            ${escapeHtml(f.comercio_nombre)}
                                            ${f.referencia_pago ? `<div class="text-[10px] font-mono font-normal text-[#5C6B62]">Ref: ${escapeHtml(f.referencia_pago)}</div>` : ''}
                                        </td>
                                        <td class="px-3.5 py-2.5 font-mono text-[#5C6B62]">${escapeHtml(f.periodo_mes)}</td>
                                        <td class="px-3.5 py-2.5">
                                            <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${isPaid ? 'bg-[#E4EFE9] text-[#1B4332]' : 'bg-[#FEF3C7] text-[#92400E]'}">${escapeHtml(f.estado_pago)}</span>
                                        </td>
                                        <td class="px-3.5 py-2.5 text-right font-extrabold ${isPaid ? 'text-[#1B4332]' : 'text-[#D6402B]'}">$${Number(f.total_a_pagar || 0).toFixed(2)}</td>
                                        <td class="px-3.5 py-2.5 text-right">
                                            ${!isPaid
                                                ? `<button type="button" onclick="marcarFacturaPagada(${f.id})" class="bg-[#1B4332] hover:bg-[#143326] text-white keep-white text-[11px] font-bold px-2.5 py-1 rounded-lg transition">Marcar Pagada</button>`
                                                : `<button type="button" onclick="revertirFacturaPendiente(${f.id})" class="bg-[#EDE5D7] hover:bg-[#E2D6C1] text-[#1A241E] text-[11px] font-bold px-2.5 py-1 rounded-lg transition">Pendiente</button>`
                                            }
                                        </td>
                                    </tr>`;
                                }).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        async function marcarFacturaPagada(facturaId) {
            const ref = await uiPrompt({
                title: `Registrar pago de factura #${facturaId}`,
                message: 'La factura pasará a PAGADO y el comercio se reactivará automáticamente si estaba suspendido.',
                label: 'Referencia de pago / transferencia (opcional)',
                defaultValue: `PAGO-${facturaId}`,
                confirmText: 'Marcar como pagada',
                variant: 'info',
                icon: 'fa-solid fa-receipt text-base'
            });
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
                <div class="glass rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] font-bold uppercase text-[#5C6B62]"><i class="fa-brands fa-whatsapp text-[#1B4332] mr-1.5"></i>WhatsApp</p>
                    <p class="text-sm font-extrabold text-[#1A241E]">${escapeHtml(m.whatsapp_provider || 'EVOLUTION_QR')}</p>
                    <p class="text-xs font-mono text-[#1B4332]">${escapeHtml(m.whatsapp_bot_number || 'Instancia activa')}</p>
                </div>
                <div class="glass rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] font-bold uppercase text-[#5C6B62]"><i class="fa-solid fa-robot text-[#1B4332] mr-1.5"></i>Motor de IA</p>
                    <p class="text-sm font-extrabold text-[#1A241E] truncate" title="${escapeHtml(m.ai_priority || '')}">${escapeHtml(m.ai_priority || 'Groq -> Gemini -> GitHub')}</p>
                    <p class="text-xs text-[#5C6B62]">Cascada automática</p>
                </div>
                <div class="glass rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] font-bold uppercase text-[#5C6B62]"><i class="fa-solid fa-credit-card text-[#D6402B] mr-1.5"></i>Pasarela</p>
                    <p class="text-sm font-extrabold text-[#1A241E]">${escapeHtml(m.payment_mode || 'SIMULATION')}</p>
                    <p class="text-xs text-[#5C6B62]">Listo para Pagadito API</p>
                </div>
                <div class="glass rounded-2xl p-4 space-y-1">
                    <p class="text-[11px] font-bold uppercase text-[#5C6B62]"><i class="fa-solid fa-database text-[#1B4332] mr-1.5"></i>Base de Datos</p>
                    <p class="text-sm font-extrabold text-[#1A241E]">PostgreSQL Neon</p>
                    <p class="text-xs font-mono text-[#5C6B62]">Schema: ${escapeHtml(m.database_schema || 'saas_whatsapp')}</p>
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

            list.innerHTML = `
                <div class="rounded-xl overflow-hidden">
                    <div class="overflow-x-auto">
                        <table class="ui-zebra-table w-full text-left border-collapse text-xs">
                            <thead class="uppercase">
                                <tr>
                                    <th class="px-3.5 py-3">Acción</th>
                                    <th class="px-3.5 py-3">Recurso</th>
                                    <th class="px-3.5 py-3">Actor</th>
                                    <th class="px-3.5 py-3 text-right">Fecha</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${logs.map(l => `
                                    <tr>
                                        <td class="px-3.5 py-2.5 font-bold text-[#1B4332]">${escapeHtml(l.accion)}</td>
                                        <td class="px-3.5 py-2.5 font-mono text-[11px] text-[#5C6B62]">${escapeHtml(l.recurso_tipo || 'SISTEMA')}${l.recurso_id ? '#' + l.recurso_id : ''}${l.tenant_id ? ` · T#${l.tenant_id}` : ''}</td>
                                        <td class="px-3.5 py-2.5 text-[#1A241E]">${escapeHtml(l.actor_email || 'Sistema')}</td>
                                        <td class="px-3.5 py-2.5 text-right font-mono text-[11px] text-[#5C6B62]">${new Date(l.creado_en).toLocaleString()}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
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
            const ecInv = document.getElementById('ecInventario');
            if (ecInv) ecInv.checked = Boolean(c.inventario_habilitado);
            document.getElementById('modalEditarComercioAdmin')?.classList.remove('hidden');
        }

        function closeModalEditarComercioAdmin() {
            document.getElementById('modalEditarComercioAdmin')?.classList.add('hidden');
        }

        async function toggleInventarioComercioAdmin(btn, tenantId, habilitar) {
            if (btn) btn.disabled = true;
            try {
                await apiFetch(`/api/v1/panel/superadmin/comercios/${tenantId}`, {
                    method: 'PUT',
                    body: { inventario_habilitado: !!habilitar }
                });
                showToast(habilitar ? 'Módulo de Inventario habilitado para este comercio.' : 'Módulo de Inventario ocultado para este comercio.');
                await loadSuperAdminDashboard();
            } catch (err) {
                showToast(err.message, true);
            } finally {
                if (btn) btn.disabled = false;
            }
        }
        window.toggleInventarioComercioAdmin = toggleInventarioComercioAdmin;

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
                        reservaciones_habilitadas: document.getElementById('ecReservaciones').checked,
                        inventario_habilitado: Boolean(document.getElementById('ecInventario')?.checked)
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
            const nueva = await uiPrompt({
                title: 'Restablecer contraseña de usuario',
                message: 'Define una nueva contraseña temporal para este usuario del comercio.',
                label: 'Nueva contraseña temporal (mín. 6 caracteres)',
                defaultValue: 'Reset#2026!',
                inputType: 'password',
                minLength: 6,
                required: true,
                confirmText: 'Actualizar contraseña',
                variant: 'password'
            });
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
