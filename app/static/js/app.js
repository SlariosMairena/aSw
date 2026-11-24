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

function getCachedUser() {
    try {
        const raw = localStorage.getItem('saas_cached_user');
        return raw ? JSON.parse(raw) : null;
    } catch (e) {
        return null;
    }
}

function setCachedUser(user) {
    try {
        if (user && user.rol) {
            localStorage.setItem('saas_cached_user', JSON.stringify({
                id: user.id,
                email: user.email,
                nombre_completo: user.nombre_completo,
                rol: user.rol,
                tenant_id: user.tenant_id,
                sucursal_id: user.sucursal_id ?? null,
                totp_enabled: Boolean(user.totp_enabled)
            }));
        } else {
            localStorage.removeItem('saas_cached_user');
        }
    } catch (e) {}
}

function finishAuthBoot() {
    document.documentElement.classList.remove('auth-booting');
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
                setCachedUser(currentUser);
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
            if (typeof stopLiveKanbanPolling === 'function') stopLiveKanbanPolling();
            setLocalToken('');
            setCachedUser(null);
            finishAuthBoot();
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
            document.getElementById('repartidorSection')?.classList.add('hidden');
            document.getElementById('loginSection').classList.remove('hidden');
        }

        function isComercioAdminOrSuper() {
            return Boolean(currentUser && (currentUser.rol === 'COMERCIO_ADMIN' || currentUser.rol === 'SUPER_ADMIN'));
        }
        window.isComercioAdminOrSuper = isComercioAdminOrSuper;

        function getGiroProfile(giroRaw) {
            const g = String(giroRaw || 'RESTAURANTE').toUpperCase().trim();
            if (g.includes('SERVICIO') || g.includes('CITA') || g.includes('CLINIC') || g.includes('DENTIS') || g.includes('BARBER') || g.includes('SPA')) {
                return {
                    code: 'SERVICIOS_CITAS',
                    label: 'Clínica / Dentista / Citas',
                    icon: 'fa-calendar-check',
                    col2Icon: 'fa-stethoscope',
                    col2Label: '2. EN CONSULTA / ATENCIÓN',
                    col2ActionBtn: 'En Atención',
                    chatHumanLabel: 'Recepción',
                    hintBanner: 'Perfil Clínica / Citas activo: catálogo de servicios, agenda por consultorio/doctor con anticipo y recordatorios WhatsApp.',
                    tabMenuLabel: 'Servicios',
                    catalogTitle: 'Catálogo de Servicios y Tratamientos',
                    itemSingular: 'Servicio',
                    itemPlural: 'servicios',
                    menuFormSubtitle: 'Configura tratamiento o servicio, precio, duración y opciones adicionales',
                    categoryDefault: 'Consultas',
                    categoryPlaceholder: 'Ej. Consultas, Limpieza Dental, Ortodoncia',
                    itemPlaceholder: 'Ej. Limpieza Dental Ultrasónica, Consulta de Valoración',
                    descPlaceholder: 'Qué incluye el servicio, duración aproximada o indicaciones previas...',
                    extrasTitle: 'Duración y Opciones Adicionales',
                    extrasSubtitle: 'Agrega duraciones o complementos del servicio (ej. Radiografía, Limpieza Profunda):',
                    tabSucLabel: 'Sedes y QR',
                    soundLabel: 'Probar Aviso',
                    enProcesoLabel: '2. En Atención',
                    invMetric1Label: 'Insumos Clínicos',
                    invNewBtnLabel: 'Nuevo Insumo',
                    invThItemLabel: 'Insumo / Material Clínico',
                    invSearchPlaceholder: 'Buscar guantes, resina, anestesia...',
                    invModalNewTitle: 'Nuevo Insumo Clínico',
                    invNameLabel: 'Nombre del Insumo',
                    invNamePlaceholder: 'Ej. Resina Fotocurada A2, Guantes Nitrilo',
                    usesRemovableIngredients: false,
                    usesTables: true,
                    defaultCategories: [],
                    defaultExtras: [
                        { nombre: 'Duración 30 min', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Duración 60 min', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Radiografía Digital', tipo: 'EXTRA', precio: 250.00 },
                        { nombre: 'Limpieza Profunda', tipo: 'EXTRA', precio: 350.00 }
                    ]
                };
            }
            if (g.includes('CAFETER') || g === 'CAFETERIA') {
                return {
                    code: 'CAFETERIA',
                    label: 'Cafetería / Repostería',
                    icon: 'fa-mug-hot',
                    col2Icon: 'fa-mug-hot',
                    col2Label: '2. EN PROCESO (Barra / Preparando)',
                    col2ActionBtn: 'En Barra',
                    chatHumanLabel: 'Cafetería',
                    hintBanner: 'Perfil Cafetería activo: barra de bebidas, repostería, personalización de leche/extras y mesas en local.',
                    tabMenuLabel: 'Menú y Bebidas',
                    catalogTitle: 'Catálogo de Bebidas y Repostería',
                    itemSingular: 'Producto',
                    itemPlural: 'productos',
                    menuFormSubtitle: 'Configura bebida o postre, tamaño, leches y extras de barra',
                    categoryDefault: 'Cafés Calientes',
                    categoryPlaceholder: 'Ej. Cafés Calientes, Frappés, Repostería',
                    itemPlaceholder: 'Ej. Cappuccino Clásico, Croissant de Almendras',
                    descPlaceholder: 'Notas de sabor, tipo de grano o porción...',
                    extrasTitle: 'Tamaños, Leches y Extras de Barra',
                    extrasSubtitle: 'Agrega variantes de tamaño (Grande/Mediano) o extras como shot espresso o leche vegetal:',
                    tabSucLabel: 'Sucursales y Mesas',
                    soundLabel: 'Probar Aviso de Barra',
                    enProcesoLabel: '2. En Barra',
                    invMetric1Label: 'Insumos de Barra',
                    invNewBtnLabel: 'Nuevo Insumo',
                    invThItemLabel: 'Insumo / Grano / Lácteo',
                    invSearchPlaceholder: 'Buscar grano, leche, jarabe o empaque...',
                    invModalNewTitle: 'Nuevo Insumo de Cafetería',
                    invNameLabel: 'Nombre del Insumo / Grano / Empaque',
                    invNamePlaceholder: 'Ej. Café Arábigo en Grano, Leche de Almendra',
                    usesRemovableIngredients: true,
                    usesTables: true,
                    defaultCategories: ['Leche Entera', 'Azúcar', 'Canela', 'Crema Batida', 'Hielo'],
                    defaultExtras: [
                        { nombre: 'Tamaño Grande 16oz', tipo: 'VARIANTE', precio: 25.00 },
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
                    col2Icon: 'fa-pills',
                    col2Label: '2. ALISTANDO (Despacho Farmacéutico)',
                    col2ActionBtn: 'Alistando',
                    chatHumanLabel: 'Farmacia',
                    hintBanner: 'Perfil Farmacéutico activo: catálogo por presentaciones y dosis, despacho sin mesas ni ingredientes removibles.',
                    tabMenuLabel: 'Medicamentos',
                    catalogTitle: 'Catálogo Farmacéutico',
                    itemSingular: 'Medicamento',
                    itemPlural: 'medicamentos',
                    menuFormSubtitle: 'Configura fármaco, dosis, precio y presentaciones (Caja / Blíster)',
                    categoryDefault: 'Analgésicos y Fiebre',
                    categoryPlaceholder: 'Ej. Analgésicos, Antigripales, Vitaminas',
                    itemPlaceholder: 'Ej. Acetaminofén 500mg, Vitamina C + Zinc',
                    descPlaceholder: 'Indicación terapéutica, dosis o requerimientos...',
                    extrasTitle: 'Presentaciones y Variantes (Caja / Blíster / Dosis)',
                    extrasSubtitle: 'Configura presentaciones disponibles del mismo producto con ajuste de precio:',
                    tabSucLabel: 'Sucursales',
                    soundLabel: 'Probar Aviso Despacho',
                    enProcesoLabel: '2. Alistando',
                    invMetric1Label: 'Medicamentos en Stock',
                    invNewBtnLabel: 'Nuevo Medicamento',
                    invThItemLabel: 'Medicamento / Presentación / Botiquín',
                    invSearchPlaceholder: 'Buscar medicamento, vitamina o laboratorio...',
                    invModalNewTitle: 'Nuevo Medicamento en Inventario',
                    invNameLabel: 'Nombre del Medicamento / Insumo Médico',
                    invNamePlaceholder: 'Ej. Ibuprofeno 400mg Blíster x10, Suero Oral',
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
                    col2Icon: 'fa-box-open',
                    col2Label: '2. EMPACANDO (Preparando Envío)',
                    col2ActionBtn: 'Empacando',
                    chatHumanLabel: 'Tienda',
                    hintBanner: 'Perfil Retail / Tienda activo: artículos por tallas, variantes y empaque para envío (sin mesas ni cocina).',
                    tabMenuLabel: 'Artículos',
                    catalogTitle: 'Catálogo de Tienda',
                    itemSingular: 'Artículo',
                    itemPlural: 'artículos',
                    menuFormSubtitle: 'Configura prenda o artículo, tallas, colores y opciones de empaque',
                    categoryDefault: 'Camisetas & Tops',
                    categoryPlaceholder: 'Ej. Camisetas, Calzado, Accesorios',
                    itemPlaceholder: 'Ej. Camiseta Oversize, Mochila Antirrobo',
                    descPlaceholder: 'Material, corte, medidas o detalles del artículo...',
                    extrasTitle: 'Tallas, Colores o Empaque Especial',
                    extrasSubtitle: 'Configura tallas o servicios adicionales (ej. empaque para regalo):',
                    tabSucLabel: 'Sucursales',
                    soundLabel: 'Probar Aviso Pedido',
                    enProcesoLabel: '2. Empacando',
                    invMetric1Label: 'Artículos Registrados',
                    invNewBtnLabel: 'Nuevo Artículo',
                    invThItemLabel: 'Artículo / Talla / Accesorio',
                    invSearchPlaceholder: 'Buscar artículo, talla o proveedor...',
                    invModalNewTitle: 'Nuevo Artículo de Tienda',
                    invNameLabel: 'Nombre del Artículo / SKU',
                    invNamePlaceholder: 'Ej. Jean Slim Ínidigo T32, Sneakers Blancos T41',
                    usesRemovableIngredients: false,
                    usesTables: false,
                    defaultCategories: [],
                    defaultExtras: [
                        { nombre: 'Talla S', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Talla M', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Talla L', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Talla XL', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Color Negro', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Color Blanco', tipo: 'VARIANTE', precio: 0.00 },
                        { nombre: 'Empaque para Regalo', tipo: 'EXTRA', precio: 45.00 }
                    ]
                };
            }
            if (g.includes('SUPER') || g.includes('MINI')) {
                return {
                    code: 'SUPERMERCADO',
                    label: 'Minisúper / Conveniencia',
                    icon: 'fa-basket-shopping',
                    col2Icon: 'fa-basket-shopping',
                    col2Label: '2. SURTIENDO (Canasta / Despacho)',
                    col2ActionBtn: 'Surtiendo',
                    chatHumanLabel: 'Minisúper',
                    hintBanner: 'Perfil Minisúper activo: abarrotes, lácteos, packs familiares y surtido rápido por sucursal (sin mesas).',
                    tabMenuLabel: 'Productos',
                    catalogTitle: 'Catálogo del Minisúper',
                    itemSingular: 'Producto',
                    itemPlural: 'productos',
                    menuFormSubtitle: 'Configura producto de despensa, presentación o pack familiar',
                    categoryDefault: 'Canasta Básica',
                    categoryPlaceholder: 'Ej. Canasta Básica, Lácteos, Bebidas',
                    itemPlaceholder: 'Ej. Arroz Premium 5 Lb, Cajilla Huevos x30',
                    descPlaceholder: 'Marca, contenido neto o unidad de venta...',
                    extrasTitle: 'Presentaciones y Packs Familiares',
                    extrasSubtitle: 'Agrega formatos alternos como Six-Pack, Galón o Saco Familiar:',
                    tabSucLabel: 'Sucursales',
                    soundLabel: 'Probar Aviso Surtido',
                    enProcesoLabel: '2. Surtiendo',
                    invMetric1Label: 'Productos en Existencia',
                    invNewBtnLabel: 'Nuevo Producto',
                    invThItemLabel: 'Producto / Abarrote / Despensa',
                    invSearchPlaceholder: 'Buscar arroz, lácteos, bebidas o proveedor...',
                    invModalNewTitle: 'Nuevo Producto de Inventario',
                    invNameLabel: 'Nombre del Producto / Abarrote',
                    invNamePlaceholder: 'Ej. Leche UHT 1L, Aceite Vegetal 900ml',
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
                col2Icon: 'fa-fire-burner',
                col2Label: '2. EN PROCESO (En Cocina)',
                col2ActionBtn: 'En Cocina',
                chatHumanLabel: 'Restaurante',
                hintBanner: 'Perfil Restaurante activo: cocina multi-sucursal, platos con ingredientes removibles, extras y reservación de mesas.',
                tabMenuLabel: 'Menú y Precios',
                catalogTitle: 'Catálogo del Menú',
                itemSingular: 'Plato',
                itemPlural: 'platos',
                menuFormSubtitle: 'Configura nombre, precio, ingredientes removibles y extras',
                categoryDefault: 'Hamburguesas Artesanales',
                categoryPlaceholder: 'Ej. Hamburguesas, Pizzas, Bebidas',
                itemPlaceholder: 'Ej. Hamburguesa Clásica Doble',
                descPlaceholder: 'Detalles de cocción, guarnición o descripción breve...',
                extrasTitle: 'Extras o Variantes con Costo',
                extrasSubtitle: 'Toca los botones sugeridos para agregar extras o variantes típicos con su precio, o escribe uno personalizado abajo:',
                tabSucLabel: 'Sucursales y Mesas',
                soundLabel: 'Probar Sonido Cocina',
                enProcesoLabel: '2. En Cocina',
                invMetric1Label: 'Insumos Registrados',
                invNewBtnLabel: 'Nuevo Insumo',
                invThItemLabel: 'Insumo / Materia Prima',
                invSearchPlaceholder: 'Buscar insumo o proveedor...',
                invModalNewTitle: 'Nuevo Insumo de Inventario',
                invNameLabel: 'Nombre del Insumo / Materia Prima',
                invNamePlaceholder: 'Ej. Carne Molida Res, Queso Mozzarella',
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
        window.getGiroProfile = getGiroProfile;

        function applyComercioRoleVisibility() {
            const isAdmin = isComercioAdminOrSuper();
            const esCitasGiro = getGiroProfile(currentComercioData?.comercio?.giro).code === 'SERVICIOS_CITAS';
            document.querySelectorAll('.comercio-admin-only').forEach(el => {
                if (el.id === 'tabBtn_repartidores' && esCitasGiro) {
                    el.classList.add('hidden');
                    return;
                }
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
            const giroHint = document.getElementById('giroOperationalHint');
            if (giroHint) {
                giroHint.innerHTML = `<i class="fa-solid fa-sliders text-indigo-400"></i> <span>${profile.hintBanner}</span>`;
                giroHint.classList.remove('hidden');
            }

            // Kanban columna 2 segun giro
            const col2Icon = document.getElementById('kanbanCol2Icon');
            if (col2Icon) col2Icon.className = `fa-solid ${profile.col2Icon} text-amber-400 mr-2`;
            const col2Label = document.getElementById('kanbanCol2Label');
            if (col2Label) col2Label.textContent = profile.col2Label;

            const tabMenuLbl = document.getElementById('tabLabel_menu');
            if (tabMenuLbl) tabMenuLbl.textContent = profile.tabMenuLabel;
            const tabMenuIcon = document.getElementById('tabIcon_menu');
            if (tabMenuIcon) tabMenuIcon.className = `fa-solid ${profile.icon} pointer-events-none`;

            const tabSucLbl = document.getElementById('tabLabel_sucursales');
            if (tabSucLbl) tabSucLbl.textContent = profile.tabSucLabel;
            const tabPedLbl = document.getElementById('tabLabel_pedidos');
            const tabPedIcon = document.getElementById('tabIcon_pedidos');
            const esCitasGiro = profile.code === 'SERVICIOS_CITAS';
            if (tabPedLbl) tabPedLbl.textContent = esCitasGiro ? 'Agenda de Citas' : 'Pedidos';
            if (tabPedIcon) tabPedIcon.className = `fa-solid ${esCitasGiro ? 'fa-calendar-days' : 'fa-clipboard-list'} w-4 text-center pointer-events-none`;
            const tabCliLbl = document.getElementById('tabLabel_clientes');
            if (tabCliLbl) tabCliLbl.textContent = esCitasGiro ? 'Pacientes / Clientes' : 'Clientes';
            const tabInvLbl = document.getElementById('tabLabel_inventario');
            if (tabInvLbl) tabInvLbl.textContent = esCitasGiro ? 'Insumos' : 'Inventario';
            const sucSecHead = document.getElementById('sucursalesSectionHeading');
            if (sucSecHead) sucSecHead.textContent = esCitasGiro
                ? 'Sedes, Horarios y Códigos QR de WhatsApp'
                : 'Sucursales, Zonas de Envío y Códigos QR de WhatsApp';
            const cfgDelWrap = document.getElementById('cfgDeliverySectionWrap');
            if (cfgDelWrap) cfgDelWrap.classList.toggle('hidden', esCitasGiro);

            // Opción 2: en SERVICIOS_CITAS la pantalla principal es directamente la Agenda/Calendario
            const kanbanWrap = document.getElementById('kanbanPedidosWrapper');
            const citasWrap = document.getElementById('citasAgendaPrincipalWrap');
            const bloqueRes = document.getElementById('bloqueReservacionMesas');
            const resHost = document.getElementById('bloqueReservacionMesasHost');
            if (kanbanWrap && citasWrap && bloqueRes && resHost) {
                if (esCitasGiro) {
                    kanbanWrap.classList.add('hidden');
                    citasWrap.classList.remove('hidden');
                    if (bloqueRes.parentElement !== citasWrap) citasWrap.appendChild(bloqueRes);
                } else {
                    kanbanWrap.classList.remove('hidden');
                    citasWrap.classList.add('hidden');
                    if (bloqueRes.parentElement !== resHost) resHost.appendChild(bloqueRes);
                }
            }
            const soundLbl = document.getElementById('btnSoundLabel');
            if (soundLbl) soundLbl.textContent = profile.soundLabel;

            const catTitle = document.getElementById('catalogTitleText');
            if (catTitle) catTitle.textContent = profile.catalogTitle;
            const catIcon = document.getElementById('catalogTitleIcon');
            if (catIcon) catIcon.className = `fa-solid ${profile.icon} text-indigo-400`;

            const lblProd = document.getElementById('lblNombreProductoMenu');
            if (lblProd) lblProd.textContent = `Nombre del ${profile.itemSingular}`;
            const catInput = document.getElementById('platoCategoria');
            if (catInput) {
                catInput.placeholder = profile.categoryPlaceholder;
                const knownDefaults = ['Hamburguesas Artesanales', 'Cafés Calientes', 'Analgésicos y Fiebre', 'Camisetas & Tops', 'Canasta Básica'];
                if (!catInput.value || knownDefaults.includes(catInput.value)) {
                    catInput.value = profile.categoryDefault;
                }
            }
            const platoNom = document.getElementById('platoNombre');
            if (platoNom) platoNom.placeholder = profile.itemPlaceholder;
            const platoDesc = document.getElementById('platoDesc');
            if (platoDesc) platoDesc.placeholder = profile.descPlaceholder;
            const lblExtrasTitle = document.getElementById('lblExtrasTitle');
            if (lblExtrasTitle) lblExtrasTitle.textContent = profile.extrasTitle;
            const lblExtrasSubtitle = document.getElementById('lblExtrasSubtitle');
            if (lblExtrasSubtitle) lblExtrasSubtitle.textContent = profile.extrasSubtitle;

            const extrasTabMap = {
                SERVICIOS_CITAS: { tab: '2. Duración y Opciones', ph: 'Ej. Duración 45 min, Radiografía Digital', pph: '+ 0', heading: 'Duraciones y opciones configuradas:', tipo: 'VARIANTE', precioLbl: 'Precio del Servicio' },
                TIENDA: { tab: '2. Tallas, Colores y Variantes', ph: 'Ej. Talla M, Color Azul, Caja Regalo', pph: '+ 0', heading: 'Tallas, colores y opciones configuradas:', tipo: 'VARIANTE', precioLbl: 'Precio de Venta' },
                FARMACIA: { tab: '2. Presentaciones / Dosis', ph: 'Ej. Caja x20, Frasco 120ml', pph: '+ 0', heading: 'Presentaciones configuradas:', tipo: 'VARIANTE', precioLbl: 'Precio de Venta' },
                SUPERMERCADO: { tab: '2. Presentaciones / Empaques', ph: 'Ej. Pack x6, Bolsa 1kg', pph: '+ 0', heading: 'Presentaciones configuradas:', tipo: 'VARIANTE', precioLbl: 'Precio de Venta' },
                CAFETERIA: { tab: '3. Tamaños y Leches', ph: 'Ej. Tamaño Grande, Leche Almendra', pph: '+ 25', heading: 'Tamaños y extras de barra configurados:', tipo: 'EXTRA', precioLbl: 'Precio Base' },
                RESTAURANTE: { tab: '3. Extras / Variantes', ph: 'Ej. Extra Queso, Porción Grande', pph: '+ 40', heading: 'Extras y variantes configurados:', tipo: 'EXTRA', precioLbl: 'Precio Base' }
            };
            const exCfg = extrasTabMap[profile.code] || extrasTabMap.RESTAURANTE;
            const lblExTab = document.getElementById('lblExtrasTabTitle');
            if (lblExTab) lblExTab.textContent = exCfg.tab;
            const inpExNom = document.getElementById('inputExtraNombre');
            if (inpExNom) inpExNom.placeholder = exCfg.ph;
            const inpExPre = document.getElementById('inputExtraPrecio');
            if (inpExPre) inpExPre.placeholder = exCfg.pph;
            const selExTipo = document.getElementById('inputExtraTipo');
            if (selExTipo) selExTipo.value = exCfg.tipo;
            const lblSelEx = document.getElementById('lblSelectedExtrasHeading');
            if (lblSelEx) lblSelEx.textContent = exCfg.heading;
            const lblPreBase = document.getElementById('lblPrecioBasePlato');
            if (lblPreBase) lblPreBase.textContent = exCfg.precioLbl;
            const lblFoto = document.getElementById('lblFotoPlatoLabel');
            if (lblFoto) lblFoto.textContent = `Foto del ${profile.itemSingular} (opcional)`;

            if (!document.getElementById('platoEditId')?.value) {
                const formTitle = document.getElementById('formPlatoTitle');
                if (formTitle) formTitle.textContent = `Nuevo ${profile.itemSingular}`;
                const formSub = document.getElementById('formPlatoSubtitle');
                if (formSub) formSub.textContent = profile.menuFormSubtitle;
                const btnSub = document.getElementById('btnSubmitPlatoText');
                if (btnSub) btnSub.textContent = `Guardar ${profile.itemSingular} en Catálogo`;
            }

            // Inventario adaptado al giro
            const invM1 = document.getElementById('invMetric1Label');
            if (invM1) invM1.textContent = profile.invMetric1Label;
            const invBtn = document.getElementById('btnNuevoInsumoText');
            if (invBtn) invBtn.textContent = profile.invNewBtnLabel;
            const invTh = document.getElementById('invThItemLabel');
            if (invTh) invTh.textContent = profile.invThItemLabel;
            const invSearch = document.getElementById('invSearchInput');
            if (invSearch) invSearch.placeholder = profile.invSearchPlaceholder;
            const invNameLbl = document.getElementById('lblInvItemName');
            if (invNameLbl) invNameLbl.textContent = profile.invNameLabel;
            const invNameInput = document.getElementById('invItemNombre');
            if (invNameInput) invNameInput.placeholder = profile.invNamePlaceholder;

            const bloqueIng = document.getElementById('bloqueIngBaseForm');
            if (bloqueIng) bloqueIng.classList.toggle('hidden', !profile.usesRemovableIngredients);
            const tabBtnIng = document.getElementById('btnPlatoTab_ingredientes');
            if (tabBtnIng) tabBtnIng.classList.toggle('hidden', !profile.usesRemovableIngredients);
            const btnNuevoLbl = document.getElementById('btnNuevoPlatoLabel');
            if (btnNuevoLbl) btnNuevoLbl.textContent = `Nuevo ${profile.itemSingular}`;
            const bloqueMesas = document.getElementById('bloqueReservacionMesas');
            if (bloqueMesas) bloqueMesas.classList.toggle('hidden', !profile.usesTables);

            // Módulo Inventario opcional (oculto por defecto salvo que el SuperAdmin lo active para este comercio)
            const invEnabled = Boolean(comercio && comercio.inventario_habilitado);
            const tabBtnInv = document.getElementById('tabBtn_inventario');
            if (tabBtnInv) tabBtnInv.classList.toggle('hidden', !invEnabled);
            const tabContentInv = document.getElementById('tabContent_inventario');
            if (tabContentInv && !invEnabled) tabContentInv.classList.add('hidden');
        }

        async function renderAppByRole() {
            document.getElementById('loginSection').classList.add('hidden');
            const headerBox = document.getElementById('userHeaderBox');
            headerBox.classList.remove('hidden');
            headerBox.classList.add('flex');
            document.getElementById('userBadgeName').textContent = currentUser.nombre_completo;
            applyComercioRoleVisibility();
            if (typeof update2FABadgeNavbar === 'function') update2FABadgeNavbar();

            if (currentUser.rol === 'SUPER_ADMIN') {
                document.getElementById('btnVolverSuperAdmin').classList.add('hidden');
                document.getElementById('comercioSection').classList.add('hidden');
                document.getElementById('repartidorSection')?.classList.add('hidden');
                document.getElementById('superAdminSection').classList.remove('hidden');
                await loadSuperAdminDashboard();
            } else if (currentUser.rol === 'REPARTIDOR') {
                document.getElementById('btnVolverSuperAdmin').classList.add('hidden');
                document.getElementById('superAdminSection').classList.add('hidden');
                document.getElementById('comercioSection').classList.add('hidden');
                document.getElementById('repartidorSection')?.classList.remove('hidden');
                if (typeof loadRepartidorPanel === 'function') await loadRepartidorPanel();
            } else {
                activeTenantId = currentUser.tenant_id;
                document.getElementById('superAdminSection').classList.add('hidden');
                document.getElementById('repartidorSection')?.classList.add('hidden');
                document.getElementById('comercioSection').classList.remove('hidden');
                switchComercioTab('pedidos');
                await reloadComercioDashboard(true);
                startLiveKanbanPolling();
            }
            finishAuthBoot();
        }

        async function inspectComercioAsSuperAdmin(tenantId) {
            activeTenantId = tenantId;
            document.getElementById('superAdminSection').classList.add('hidden');
            document.getElementById('comercioSection').classList.remove('hidden');
            document.getElementById('btnVolverSuperAdmin').classList.remove('hidden');
            applyComercioRoleVisibility();
            switchComercioTab('pedidos');
            await reloadComercioDashboard(true);
            startLiveKanbanPolling();
        }

        async function switchBackToSuperAdmin() {
            stopLiveKanbanPolling();
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
        function syncRepartidoresTabState() {
            const btn = document.getElementById('tabBtn_repartidores');
            if (!btn) return;
            const esCitasGiro = getGiroProfile(currentComercioData?.comercio?.giro).code === 'SERVICIOS_CITAS';
            if (esCitasGiro) {
                btn.classList.add('hidden');
                return;
            }
            const dCfg = currentComercioData?.comercio?.delivery_config || {};
            const deliveryOff = dCfg.modo_delivery === 'DESACTIVADO';
            const isLocked = deliveryOff && currentUser?.rol !== 'SUPER_ADMIN';
            btn.classList.toggle('ui-nav-locked', isLocked);
            btn.title = isLocked
                ? 'Delivery desactivado — Actívalo en Configuración para ver entregas'
                : 'Ver entregas en camino y gestión de repartidor';
            const lbl = btn.querySelector('span');
            if (lbl) {
                lbl.innerHTML = isLocked
                    ? 'Repartidores <i class="fa-solid fa-lock text-[10px] ml-1 opacity-75"></i>'
                    : 'Repartidores';
            }
        }
        window.syncRepartidoresTabState = syncRepartidoresTabState;

        function switchComercioTab(tabName) {
            const esCitasGiro = getGiroProfile(currentComercioData?.comercio?.giro).code === 'SERVICIOS_CITAS';
            if ((tabName === 'estadisticas' || tabName === 'configuracion' || tabName === 'repartidores') && !isComercioAdminOrSuper()) {
                tabName = 'pedidos';
            }
            if (tabName === 'repartidores' && esCitasGiro) {
                tabName = 'pedidos';
            }
            if (tabName === 'inventario' && !currentComercioData?.comercio?.inventario_habilitado) {
                tabName = 'pedidos';
            }
            if (tabName === 'repartidores' && currentComercioData?.comercio?.delivery_config?.modo_delivery === 'DESACTIVADO' && currentUser?.rol !== 'SUPER_ADMIN') {
                showToast('Tu negocio tiene el Delivery desactivado. Actívalo en Configuración para ver entregas.', true);
                return;
            }
            console.log('[switchComercioTab] Activando pestaña:', tabName);
            const tabs = ['pedidos', 'menu', 'inventario', 'sucursales', 'clientes', 'estadisticas', 'repartidores', 'configuracion'];
            
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
                    const hideInvCls = (t === 'inventario' && !currentComercioData?.comercio?.inventario_habilitado) ? ' hidden' : '';
                    const hideRepCitasCls = (t === 'repartidores' && esCitasGiro) ? ' hidden' : '';
                    const isRepLocked = (t === 'repartidores' && !esCitasGiro && currentComercioData?.comercio?.delivery_config?.modo_delivery === 'DESACTIVADO' && currentUser?.rol !== 'SUPER_ADMIN');
                    const lockedRepCls = isRepLocked ? ' ui-nav-locked' : '';
                    if (t === 'repartidores') {
                        btn.title = isRepLocked ? 'Delivery desactivado — Actívalo en Configuración' : 'Ver entregas en camino y repartidor';
                        const lbl = btn.querySelector('span');
                        if (lbl) lbl.innerHTML = isRepLocked ? 'Repartidores <i class="fa-solid fa-lock text-[10px] ml-1 opacity-75"></i>' : 'Repartidores';
                    }
                    if (t === tabName) {
                        btn.className = 'ui-nav-btn is-active' + adminOnlyCls + hideInvCls + hideRepCitasCls + lockedRepCls;
                    } else {
                        btn.className = 'ui-nav-btn' + adminOnlyCls + hideInvCls + hideRepCitasCls + lockedRepCls;
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
            } else if (tabName === 'clientes') {
                if (typeof cargarClientesComercio === 'function') {
                    cargarClientesComercio();
                }
            } else if (tabName === 'estadisticas') {
                if (typeof cargarEstadisticasComercio === 'function') {
                    cargarEstadisticasComercio();
                }
                if (typeof cargarCorteCajaDiario === 'function') {
                    cargarCorteCajaDiario();
                }
            } else if (tabName === 'configuracion') {
                if (typeof populateConfigComercioForm === 'function') {
                    populateConfigComercioForm();
                }
                if (typeof cargarEquipoComercio === 'function') {
                    cargarEquipoComercio();
                }
            } else if (tabName === 'repartidores') {
                if (typeof cargarRepartidoresComercio === 'function') {
                    cargarRepartidoresComercio();
                }
            }
        }
        window.switchComercioTab = switchComercioTab;

        let liveEventsSource = null;
        let liveSyncInFlight = false;
        let knownOrderIds = new Set();
        let knownOrderStates = new Map();
        let lastOrdersSignature = '';

        function _computeOrdersSignature(pedidos) {
            return (pedidos || [])
                .map(p => `${p.id}:${p.estado_pedido}:${p.estado_pago}:${(p.mensajes_chat || []).length}`)
                .join('|');
        }

        function _syncKnownOrdersSnapshot(pedidos) {
            knownOrderIds = new Set();
            knownOrderStates = new Map();
            (pedidos || []).forEach(p => {
                knownOrderIds.add(p.id);
                knownOrderStates.set(p.id, {
                    folio: p.folio,
                    estado: p.estado_pedido,
                    pago: p.estado_pago,
                    msgs: (p.mensajes_chat || []).length
                });
            });
            lastOrdersSignature = _computeOrdersSignature(pedidos);
        }

        async function refreshPedidosFromEvent(evt = null) {
            if (liveSyncInFlight || !activeTenantId || !authToken) return;
            const sec = document.getElementById('comercioSection');
            if (!sec || sec.classList.contains('hidden')) return;

            liveSyncInFlight = true;
            try {
                const sucursalFilter = document.getElementById('filtroSucursalSelect')?.value || '';
                const query = sucursalFilter ? `?sucursal_id=${sucursalFilter}` : '';
                const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/dashboard${query}`);
                const pedidos = data.pedidos || [];
                const prevIds = new Set(knownOrderIds);

                currentComercioData = data;
                const sym = data.comercio?.simbolo_moneda || 'C$';
                _syncKnownOrdersSnapshot(pedidos);
                renderKanbanPedidos(pedidos, sym);

                if (activeChatPedido) {
                    const updatedChat = pedidos.find(x => x.id === activeChatPedido.id);
                    if (updatedChat) {
                        activeChatPedido = updatedChat;
                        renderChatModalMessages(updatedChat);
                    }
                }

                if (evt && evt.tipo === 'NUEVO_PEDIDO') {
                    const esRealmenteNuevo = !evt.pedido_id || !prevIds.has(Number(evt.pedido_id));
                    if (esRealmenteNuevo) {
                        playKitchenBell();
                        showToast(`Nuevo pedido recibido: #${evt.folio || ''}`);
                    }
                } else if (evt && evt.tipo === 'NUEVA_RESERVACION') {
                    playKitchenBell();
                    renderSucursalesYMesas(data.sucursales, data.comercio, data.reservaciones_mesa || []);
                    showToast(`Nueva reservación de mesa: #${evt.folio || ''}`);
                } else if (evt && evt.tipo === 'ESTADO_PEDIDO' && evt.folio) {
                    showToast(`Pedido #${evt.folio} actualizado a ${evt.estado || ''}`);
                }
            } catch (_) {
                // Reintento silencioso si hubo micro-corte
            } finally {
                liveSyncInFlight = false;
            }
        }

        function startLiveKanbanPolling() {
            stopLiveKanbanPolling();
            if (!activeTenantId || !authToken || typeof EventSource === 'undefined') return;
            const url = `/api/v1/panel/comercio/${activeTenantId}/eventos?token=${encodeURIComponent(authToken)}`;
            liveEventsSource = new EventSource(url);
            liveEventsSource.onmessage = (msg) => {
                try {
                    const evt = JSON.parse(msg.data || '{}');
                    if (evt.tipo === 'NUEVO_PEDIDO' || evt.tipo === 'ESTADO_PEDIDO' || evt.tipo === 'NUEVO_MENSAJE' || evt.tipo === 'NUEVA_RESERVACION') {
                        refreshPedidosFromEvent(evt);
                    }
                } catch (_) {}
            };
        }
        window.startLiveKanbanPolling = startLiveKanbanPolling;

        function stopLiveKanbanPolling() {
            if (liveEventsSource) {
                try { liveEventsSource.close(); } catch (_) {}
                liveEventsSource = null;
            }
            liveSyncInFlight = false;
        }
        window.stopLiveKanbanPolling = stopLiveKanbanPolling;

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && activeTenantId && authToken && !document.getElementById('comercioSection')?.classList.contains('hidden')) {
                refreshPedidosFromEvent(null);
            }
        });

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
                const hdrLogo = document.getElementById('comercioHeaderLogo');
                if (hdrLogo) {
                    if (c.logo_url) {
                        hdrLogo.src = c.logo_url;
                        hdrLogo.classList.remove('hidden');
                    } else {
                        hdrLogo.src = '';
                        hdrLogo.classList.add('hidden');
                    }
                }
                document.getElementById('comercioPlanBadge').textContent = `${c.plan_nombre} • ${c.estado_suscripcion}`;
                document.getElementById('comercioSubInfo').textContent = isComercioAdminOrSuper()
                    ? `Pedidos del mes: ${c.pedidos_mes_actual} • Datos transferencia Modo 1: ${c.datos_bancarios_texto}`
                    : `Vista operativa de sucursal • Pedidos activos y control de disponibilidad`;

                applyGiroAdaptation(c);
                applyComercioRoleVisibility();
                syncRepartidoresTabState();

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

                _syncKnownOrdersSnapshot(data.pedidos || []);
                renderKanbanPedidos(data.pedidos, sym);
                renderMenuPlatos(data.platos, sym);
                renderSucursalesYMesas(data.sucursales, c, data.reservaciones_mesa || []);
                renderIngredientBuilderUI();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        let filtroHistorialPedidos = 'TODOS';
        let historialPedidosAbierto = false;

        function toggleHistorialPedidosSection() {
            historialPedidosAbierto = !historialPedidosAbierto;
            const wrap = document.getElementById('wrapperColEntregados');
            const icon = document.getElementById('iconToggleHistorialPedidos');
            if (wrap) wrap.classList.toggle('hidden', !historialPedidosAbierto);
            if (icon) icon.className = `fa-solid ${historialPedidosAbierto ? 'fa-chevron-up' : 'fa-chevron-down'} text-slate-400 group-hover:text-white ml-1 transition`;
        }
        window.toggleHistorialPedidosSection = toggleHistorialPedidosSection;

        function setFiltroHistorialPedidos(filtro) {
            filtroHistorialPedidos = filtro;
            if (!historialPedidosAbierto) toggleHistorialPedidosSection();
            const btns = { TODOS: 'btnHistTodos', ENTREGADO: 'btnHistEntregado', CANCELADO: 'btnHistCancelado' };
            Object.entries(btns).forEach(([k, id]) => {
                const el = document.getElementById(id);
                if (!el) return;
                el.className = k === filtro
                    ? 'px-3 py-1.5 rounded-lg bg-teal-500/20 text-teal-200 border border-teal-500/40 font-semibold transition'
                    : 'px-3 py-1.5 rounded-lg bg-slate-800 text-slate-300 border border-slate-700 font-semibold transition';
            });
            if (currentComercioData) {
                renderKanbanPedidos(currentComercioData.pedidos || [], currentComercioData.comercio?.simbolo_moneda || 'C$');
            }
        }
        window.setFiltroHistorialPedidos = setFiltroHistorialPedidos;

        function renderKanbanPedidos(pedidos, moneda) {
            const recibidos = pedidos.filter(p => p.estado_pedido === 'RECIBIDO');
            const enProceso = pedidos.filter(p => p.estado_pedido === 'EN_PROCESO');
            const listos = pedidos.filter(p => p.estado_pedido === 'LISTO');
            const entregados = pedidos.filter(p => p.estado_pedido === 'ENTREGADO');
            const cancelados = pedidos.filter(p => p.estado_pedido === 'CANCELADO');

            document.getElementById('countRecibido').textContent = recibidos.length;
            document.getElementById('countEnProceso').textContent = enProceso.length;
            document.getElementById('countListo').textContent = listos.length;
            const cEnt = document.getElementById('countEntregado');
            if (cEnt) cEnt.textContent = `${entregados.length} Entregados`;
            const cCanc = document.getElementById('countCancelado');
            if (cCanc) cCanc.textContent = `${cancelados.length} Cancelados`;

            document.getElementById('colRecibido').innerHTML = recibidos.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Sin pedidos pendientes en esta columna</p>`;
            document.getElementById('colEnProceso').innerHTML = enProceso.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Ningún pedido en preparación</p>`;
            document.getElementById('colListo').innerHTML = listos.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                `<p class="text-xs text-slate-500 text-center py-6">Ningún pedido listo para entrega</p>`;

            const histEl = document.getElementById('colEntregados');
            if (histEl) {
                let histList = [];
                if (filtroHistorialPedidos === 'ENTREGADO') histList = entregados;
                else if (filtroHistorialPedidos === 'CANCELADO') histList = cancelados;
                else histList = [...entregados, ...cancelados];
                histEl.innerHTML = histList.map(p => buildPedidoCardHTML(p, moneda)).join('') ||
                    `<p class="col-span-full text-xs text-slate-500 text-center py-4">No hay pedidos entregados o cancelados registrados.</p>`;
            }
        }

        function buildPedidoCardHTML(p, moneda) {
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            const totalQty = (p.items || []).reduce((acc, it) => acc + (Number(it.cantidad) || 1), 0);
            const resumenItems = (p.items || [])
                .slice(0, 2)
                .map(it => `${it.cantidad}x ${it.nombre_plato}`)
                .join(', ') + ((p.items || []).length > 2 ? ` +${p.items.length - 2} más` : '');

            const entregaMap = {
                'DOMICILIO': { lbl: 'Domicilio', icon: 'fa-motorcycle', cls: 'bg-[#FDECE9] text-[#B8321F] border-[#F6C2B9]' },
                'RECOGER_LOCAL': { lbl: 'Retiro', icon: 'fa-bag-shopping', cls: 'bg-[#E4EFE9] text-[#1B4332] border-[#B7D4C5]' },
                'MESA': { lbl: 'Mesa', icon: 'fa-utensils', cls: 'bg-[#FEF3C7] text-[#92400E] border-[#FDE68A]' }
            };
            const ent = entregaMap[p.tipo_entrega] || { lbl: p.tipo_entrega || 'Local', icon: 'fa-box', cls: 'bg-[#EDE5D7] text-[#1A241E] border-[#DFD5C3]' };

            let nextActionBtn = '';
            if (p.estado_pedido === 'RECIBIDO') {
                nextActionBtn = `
                    <button type="button" onclick="event.stopPropagation(); cambiarEstadoPedido(${p.id}, 'EN_PROCESO')"
                        class="px-3 py-1.5 rounded-lg bg-[#D6402B] hover:bg-[#B8321F] text-white keep-white font-bold text-xs transition flex items-center gap-1.5 shadow-sm">
                        <i class="fa-solid ${profile.col2Icon}"></i> <span>${profile.col2ActionBtn}</span>
                    </button>`;
            } else if (p.estado_pedido === 'EN_PROCESO') {
                nextActionBtn = `
                    <button type="button" onclick="event.stopPropagation(); cambiarEstadoPedido(${p.id}, 'LISTO')"
                        class="px-3 py-1.5 rounded-lg bg-[#1B4332] hover:bg-[#143326] text-white keep-white font-bold text-xs transition flex items-center gap-1.5 shadow-sm">
                        <i class="fa-solid fa-circle-check"></i> <span>Marcar Listo</span>
                    </button>`;
            } else if (p.estado_pedido === 'LISTO') {
                nextActionBtn = `
                    <button type="button" onclick="event.stopPropagation(); cambiarEstadoPedido(${p.id}, 'ENTREGADO')"
                        class="px-3 py-1.5 rounded-lg bg-[#1B4332] hover:bg-[#143326] text-white keep-white font-bold text-xs transition flex items-center gap-1.5 shadow-sm">
                        <i class="fa-solid fa-handshake"></i> <span>Entregar</span>
                    </button>`;
            } else if (p.estado_pedido === 'CANCELADO') {
                nextActionBtn = `
                    <button type="button" onclick="event.stopPropagation(); cambiarEstadoPedido(${p.id}, 'RECIBIDO')"
                        class="px-3 py-1.5 rounded-lg bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] font-bold text-xs transition">
                        <i class="fa-solid fa-rotate-left mr-1"></i> Reabrir
                    </button>`;
            }

            const humanPill = p.modo_atencion_chat === 'HUMANO_ACTIVO'
                ? `<span class="px-2 py-0.5 rounded-full bg-[#FEF3C7] text-[#92400E] border border-[#FDE68A] text-[10px] font-bold"><i class="fa-solid fa-hand mr-1"></i> Humano</span>`
                : '';
            const vipPill = p.cliente_es_vip
                ? `<span class="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-700 border border-amber-500/40 text-[10px] font-extrabold"><i class="fa-solid fa-crown mr-1"></i>VIP</span>`
                : '';
            const repPill = (p.delivery_info && p.delivery_info.repartidor_nombre)
                ? `<div class="text-[11px] font-bold text-[#1B4332] bg-[#E4EFE9] border border-[#B7D4C5] rounded-lg px-2 py-1 flex items-center justify-between gap-2">
                     <span><i class="fa-solid fa-motorcycle mr-1"></i>${escapeHtml(p.delivery_info.repartidor_nombre)}</span>
                     <span class="uppercase text-[10px]">${escapeHtml(p.delivery_info.estado_delivery || '')}</span>
                   </div>`
                : '';

            return `
                <div id="cardPedido_${p.id}" onclick="openPedidoDetalleModal(${p.id})"
                    class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3] hover:border-[#1B4332]/50 space-y-2.5 shadow-sm hover:shadow-md transition-all duration-200 relative overflow-hidden cursor-pointer">
                    <!-- Fila 1: Cliente + Folio + Modalidad -->
                    <div class="flex items-start justify-between gap-2">
                        <div class="min-w-0">
                            <div class="font-extrabold text-sm text-[#1A241E] truncate">
                                ${escapeHtml(p.nombre_cliente || 'Cliente')}
                                <span class="font-semibold text-xs text-[#5C6B62]">(#${escapeHtml(p.folio)})</span>
                            </div>
                        </div>
                        <div class="flex items-center gap-1 shrink-0">
                            ${vipPill}
                            ${humanPill}
                            <span class="px-2 py-0.5 rounded-full text-[10px] font-bold border ${ent.cls}">
                                <i class="fa-solid ${ent.icon} mr-1"></i>${ent.lbl}
                            </span>
                        </div>
                    </div>

                    <!-- Fila 2: Resumen compacto de productos + Total -->
                    <div class="flex items-center justify-between gap-2 text-xs text-[#5C6B62]">
                        <div class="truncate" title="${escapeHtml(resumenItems)}">
                            <i class="fa-solid fa-bag-shopping text-[#1B4332] mr-1 text-[11px]"></i>
                            <strong class="text-[#1A241E]">${totalQty} art.</strong> · ${escapeHtml(resumenItems || 'Ver detalle')}
                        </div>
                        <span class="font-extrabold text-sm text-[#1B4332] shrink-0">${moneda}${Number(p.total || 0).toFixed(2)}</span>
                    </div>
                    ${repPill}

                    <!-- Fila 3: Acciones limpias (Siguiente paso + Ver Detalle + Chat) -->
                    <div class="flex flex-wrap items-center gap-1.5 pt-1 border-t border-[#EDE5D7]">
                        ${nextActionBtn}
                        <button type="button" onclick="event.stopPropagation(); openPedidoDetalleModal(${p.id})"
                            class="px-2.5 py-1.5 rounded-lg bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] font-bold text-xs transition flex items-center gap-1">
                            <i class="fa-solid fa-eye text-[#1B4332]"></i> <span>Detalle</span>
                        </button>
                        <button type="button" onclick="event.stopPropagation(); openChatModal(${p.id})"
                            title="Abrir chat de WhatsApp de este pedido"
                            class="px-2.5 py-1.5 rounded-lg bg-[#E4EFE9] hover:bg-[#D2E6DC] border border-[#B7D4C5] text-[#1B4332] font-bold text-xs transition flex items-center gap-1 ml-auto">
                            <i class="fa-solid fa-comments"></i> <span>Chat</span>
                        </button>
                    </div>
                </div>
            `;
        }

        function openPedidoDetalleModal(pedidoId) {
            const pedidos = currentComercioData?.pedidos || [];
            const p = pedidos.find(x => Number(x.id) === Number(pedidoId));
            if (!p) {
                showToast('No se encontró el detalle de este pedido.', true);
                return;
            }
            const moneda = currentComercioData?.comercio?.simbolo_moneda || 'C$';
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);

            document.getElementById('detPedidoFolio').textContent = `#${p.folio}`;
            document.getElementById('detPedidoEstadoBadge').textContent = p.estado_pedido;
            const chatBadge = document.getElementById('detPedidoChatBadge');
            if (p.modo_atencion_chat === 'HUMANO_ACTIVO') {
                chatBadge.innerHTML = '<i class="fa-solid fa-hand mr-1"></i> CONTROL HUMANO';
                chatBadge.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-[#FEF3C7] text-[#92400E] border border-[#FDE68A]';
            } else {
                chatBadge.innerHTML = '<i class="fa-solid fa-robot mr-1"></i> BOT ACTIVO';
                chatBadge.className = 'text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-white/15 text-[#86EFAC] border border-white/25';
            }
            document.getElementById('detPedidoSub').textContent = `${p.sucursal_nombre || 'Sucursal'} • Pago: ${p.metodo_pago || 'EFECTIVO'}`;

            const itemsHTML = (p.items || []).map(it => `
                <div class="p-3 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3]">
                    <div class="flex items-center justify-between gap-2">
                        <span class="font-bold text-[#1A241E] text-xs">${it.cantidad}x ${escapeHtml(it.nombre_plato)}</span>
                        <span class="font-extrabold text-[#1B4332] text-xs">${moneda}${Number(it.subtotal_linea || 0).toFixed(2)}</span>
                    </div>
                    ${it.sin_ingredientes && it.sin_ingredientes.length > 0 ? `
                        <div class="mt-1.5">
                            <span class="inline-block px-2 py-0.5 rounded bg-[#FDECE9] border border-[#F6C2B9] text-[#B8321F] font-bold text-[11px]">
                                <i class="fa-solid fa-circle-xmark mr-1"></i> SIN: ${escapeHtml(it.sin_ingredientes.join(', '))}
                            </span>
                        </div>
                    ` : ''}
                    ${it.extras && it.extras.length > 0 ? `
                        <div class="mt-1.5 flex flex-wrap gap-1">
                            ${it.extras.map(ex => `
                                <span class="inline-block px-2 py-0.5 rounded bg-[#E4EFE9] border border-[#B7D4C5] text-[#1B4332] font-semibold text-[11px]">
                                    <i class="fa-solid fa-circle-plus mr-1"></i> ${escapeHtml(ex.nombre)} (+${moneda}${Number(ex.precio).toFixed(2)})
                                </span>
                            `).join('')}
                        </div>
                    ` : ''}
                </div>
            `).join('');

            document.getElementById('detPedidoBody').innerHTML = `
                <!-- Cliente y Entrega -->
                <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3] space-y-1.5">
                    <div class="font-bold text-[#1B4332] uppercase text-[10px] tracking-wider">Cliente y Entrega</div>
                    <div class="text-[#1A241E]"><i class="fa-solid fa-user text-[#1B4332] w-4"></i> <strong>${escapeHtml(p.nombre_cliente || 'Cliente')}</strong> <span class="text-[#5C6B62]">(+${escapeHtml(p.telefono_cliente || '')})</span></div>
                    <div class="text-[#1A241E]"><i class="fa-solid fa-motorcycle text-[#1B4332] w-4"></i> <strong>${escapeHtml(p.tipo_entrega || 'LOCAL')}:</strong> ${escapeHtml(p.direccion_entrega || 'Recoger en sucursal')}</div>
                    ${p.google_maps_link ? `
                        <div class="pt-1">
                            <a href="${p.google_maps_link}" target="_blank" class="inline-flex items-center gap-1.5 text-[#D6402B] hover:underline font-bold">
                                <i class="fa-solid fa-location-dot"></i> Abrir ubicación GPS en Google Maps <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                            </a>
                        </div>
                    ` : ''}
                </div>

                <!-- Productos -->
                <div class="space-y-2">
                    <div class="font-bold text-[#1B4332] uppercase text-[10px] tracking-wider px-1">Productos del Pedido</div>
                    ${itemsHTML || '<div class="text-[#5C6B62] italic">Sin detalles registrados.</div>'}
                </div>

                <!-- Resumen IA -->
                ${p.resumen_chat_ia ? `
                    <div class="p-3 rounded-xl bg-[#E4EFE9] border border-[#B7D4C5] text-[#1A241E]">
                        <div class="font-bold text-[#1B4332] mb-0.5"><i class="fa-solid fa-wand-magic-sparkles mr-1"></i> Resumen del Chat</div>
                        <p class="text-[#39483F] leading-relaxed">${escapeHtml(p.resumen_chat_ia)}</p>
                    </div>
                ` : ''}

                <!-- Totales y Pago -->
                <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3] flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <div class="font-bold text-[#1A241E]">Método de pago: <span class="text-[#1B4332]">${escapeHtml(p.metodo_pago || 'EFECTIVO')}</span></div>
                        ${p.paga_con_monto ? `<div class="text-[#92400E] font-semibold mt-0.5">Paga con ${moneda}${p.paga_con_monto} • Cambio: ${moneda}${p.cambio_a_entregar}</div>` : ''}
                    </div>
                    <div class="text-right">
                        <div class="text-[11px] text-[#5C6B62]">Envío: ${moneda}${Number(p.costo_envio || 0).toFixed(2)}</div>
                        <div class="text-lg font-extrabold text-[#1B4332]">Total: ${moneda}${Number(p.total || 0).toFixed(2)}</div>
                    </div>
                </div>

                <!-- Cambiar estado desde el modal -->
                <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3] space-y-2">
                    <div class="font-bold text-[#1B4332] uppercase text-[10px] tracking-wider">Cambiar Estado del Pedido</div>
                    <div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
                        <button type="button" onclick="cambiarEstadoPedido(${p.id}, 'RECIBIDO').then(() => closePedidoDetalleModal())"
                            class="py-2 rounded-lg text-[11px] font-bold border transition ${p.estado_pedido === 'RECIBIDO' ? 'bg-[#1B4332] text-white keep-white border-[#1B4332]' : 'bg-[#EDE5D7] text-[#1A241E] border-[#DFD5C3] hover:bg-[#E2D6C1]'}">
                            <i class="fa-solid fa-inbox mr-1"></i> Recibido
                        </button>
                        <button type="button" onclick="cambiarEstadoPedido(${p.id}, 'EN_PROCESO').then(() => closePedidoDetalleModal())"
                            class="py-2 rounded-lg text-[11px] font-bold border transition ${p.estado_pedido === 'EN_PROCESO' ? 'bg-[#D6402B] text-white keep-white border-[#D6402B]' : 'bg-[#EDE5D7] text-[#1A241E] border-[#DFD5C3] hover:bg-[#E2D6C1]'}">
                            <i class="fa-solid ${profile.col2Icon} mr-1"></i> ${profile.col2ActionBtn}
                        </button>
                        <button type="button" onclick="cambiarEstadoPedido(${p.id}, 'LISTO').then(() => closePedidoDetalleModal())"
                            class="py-2 rounded-lg text-[11px] font-bold border transition ${p.estado_pedido === 'LISTO' ? 'bg-[#1B4332] text-white keep-white border-[#1B4332]' : 'bg-[#EDE5D7] text-[#1A241E] border-[#DFD5C3] hover:bg-[#E2D6C1]'}">
                            <i class="fa-solid fa-circle-check mr-1"></i> Listo
                        </button>
                        <button type="button" onclick="cambiarEstadoPedido(${p.id}, 'ENTREGADO').then(() => closePedidoDetalleModal())"
                            class="py-2 rounded-lg text-[11px] font-bold border transition ${p.estado_pedido === 'ENTREGADO' ? 'bg-[#1B4332] text-white keep-white border-[#1B4332]' : 'bg-[#EDE5D7] text-[#1A241E] border-[#DFD5C3] hover:bg-[#E2D6C1]'}">
                            <i class="fa-solid fa-handshake mr-1"></i> Entregado
                        </button>
                    </div>
                </div>
            `;

            document.getElementById('detPedidoFooter').innerHTML = `
                <div class="flex flex-wrap items-center gap-2">
                    <button type="button" onclick="closePedidoDetalleModal(); openChatModal(${p.id})"
                        class="px-3.5 py-2 rounded-xl bg-[#1B4332] hover:bg-[#143326] text-white keep-white font-bold text-xs transition flex items-center gap-1.5">
                        <i class="fa-solid fa-comments"></i> <span>Chat WhatsApp</span>
                    </button>
                    ${p.estado_pago === 'PAGADO' ? `
                        <span class="px-3 py-2 rounded-xl bg-[#E4EFE9] text-[#1B4332] border border-[#B7D4C5] font-bold text-xs flex items-center gap-1.5">
                            <i class="fa-solid fa-circle-check"></i> Pagado Online
                        </span>
                    ` : `
                        <button type="button" onclick="iniciarCheckoutSimulado(${p.id})"
                            class="px-3 py-2 rounded-xl bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] font-bold text-xs transition flex items-center gap-1.5">
                            <i class="fa-solid fa-credit-card text-[#1B4332]"></i> <span>Pagar Online</span>
                        </button>
                    `}
                    <a href="/ticket/${p.id}" target="_blank"
                        class="px-3 py-2 rounded-xl bg-[#EDE5D7] hover:bg-[#E2D6C1] border border-[#DFD5C3] text-[#1A241E] font-bold text-xs transition flex items-center gap-1.5">
                        <i class="fa-solid fa-print text-[#1B4332]"></i> <span>Ticket</span>
                    </a>
                </div>
                <div class="flex items-center gap-2">
                    ${(p.estado_pedido !== 'ENTREGADO' && p.estado_pedido !== 'CANCELADO') ? `
                        <button type="button" onclick="if(confirm('¿Cancelar el pedido #${p.folio}?')) cambiarEstadoPedido(${p.id}, 'CANCELADO').then(() => closePedidoDetalleModal())"
                            class="px-3 py-2 rounded-xl bg-[#FDECE9] hover:bg-[#F9D7D0] border border-[#F6C2B9] text-[#B8321F] font-bold text-xs transition flex items-center gap-1">
                            <i class="fa-solid fa-ban"></i> <span>Cancelar</span>
                        </button>
                    ` : ''}
                    <button type="button" onclick="closePedidoDetalleModal()"
                        class="px-3.5 py-2 rounded-xl bg-[#EDE5D7] hover:bg-[#E2D6C1] text-[#1A241E] font-bold text-xs transition">
                        Cerrar
                    </button>
                </div>
            `;

            document.getElementById('pedidoDetalleModal').classList.remove('hidden');
        }
        window.openPedidoDetalleModal = openPedidoDetalleModal;

        function closePedidoDetalleModal() {
            document.getElementById('pedidoDetalleModal')?.classList.add('hidden');
        }
        window.closePedidoDetalleModal = closePedidoDetalleModal;

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
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
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
                    stateLabel = profile.enProcesoLabel;
                } else if (nuevoEstado === 'LISTO') {
                    iconColor = 'text-emerald-400';
                    stateLabel = '3. Listo';
                } else if (nuevoEstado === 'ENTREGADO') {
                    iconColor = 'text-teal-400';
                    stateLabel = '4. Entregado';
                } else if (nuevoEstado === 'CANCELADO') {
                    iconColor = 'text-rose-400';
                    stateLabel = 'Cancelado';
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
                const descCount = Array.isArray(res?.stock_descontado) ? res.stock_descontado.length : Number(res?.stock_descontado || 0);
                const descTxt = descCount > 0 ? ` (${descCount} descuento(s) de inventario aplicado(s))` : '';
                showToast(`Estado actualizado a ${nuevoEstado}. Notificacion enviada a WhatsApp.${descTxt}`);
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

            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
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
                                ${isClient ? '<i class="fa-solid fa-user mr-1"></i> Cliente' : isHuman ? `<i class="fa-solid fa-user-tie mr-1"></i> ${profile.chatHumanLabel}` : '<i class="fa-solid fa-robot mr-1"></i> Bot WhatsApp'} • ${m.hora}
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
        let expandedPlatoIds = new Set();

        function getSuggestedCategoriesForGiro(giroRaw) {
            const code = getGiroProfile(giroRaw).code;
            const map = {
                SERVICIOS_CITAS: ['Consultas', 'Limpieza Dental', 'Blanqueamiento', 'Ortodoncia', 'Estética', 'Urgencias'],
                RESTAURANTE: ['Pizzas', 'Hamburguesas', 'Combos', 'Entradas', 'Bebidas', 'Postres'],
                CAFETERIA: ['Cafés Calientes', 'Cafés Fríos', 'Frappés', 'Repostería', 'Desayunos', 'Bebidas'],
                FARMACIA: ['Analgésicos', 'Vitaminas', 'Cuidado Personal', 'Primeros Auxilios', 'Bebé'],
                TIENDA: ['Camisetas', 'Pantalones', 'Calzado', 'Accesorios', 'Ofertas'],
                SUPERMERCADO: ['Bebidas', 'Snacks', 'Lácteos', 'Despensa', 'Limpieza', 'Higiene']
            };
            return map[code] || map.RESTAURANTE;
        }

        function renderPlatoModalCategoryChips() {
            const box = document.getElementById('platoModalCatChips');
            if (!box) return;
            const existing = [...new Set((allPlatosCache || []).map(p => (p.categoria || '').trim()).filter(Boolean))];
            const suggested = getSuggestedCategoriesForGiro(currentComercioData?.comercio?.giro);
            const allCats = [...new Set([...existing, ...suggested])];
            const currentVal = (document.getElementById('platoCategoria')?.value || '').trim().toLowerCase();

            box.innerHTML = allCats.map(cat => {
                const isSelected = currentVal && cat.toLowerCase() === currentVal;
                const isExisting = existing.some(e => e.toLowerCase() === cat.toLowerCase());
                const safeCat = cat.replace(/'/g, "\\'");
                return `
                    <button type="button" onclick="pickPlatoModalCategory('${safeCat}')"
                        class="px-2.5 py-1 rounded-full text-[11px] font-semibold border transition inline-flex items-center gap-1 ${
                            isSelected
                                ? 'bg-[#1B4332] text-white keep-white border-[#1B4332] font-bold shadow-sm'
                                : (isExisting
                                    ? 'bg-slate-800/90 text-slate-200 border-slate-700 hover:border-slate-500'
                                    : 'bg-slate-950/70 text-slate-400 border-slate-800 hover:text-white hover:border-slate-600')
                        }">
                        ${isExisting ? '<i class="fa-solid fa-tag text-[9px] opacity-70"></i>' : '<i class="fa-solid fa-sparkles text-[9px] opacity-60"></i>'}
                        <span>${escapeHtml(cat)}</span>
                    </button>`;
            }).join('');
        }
        window.syncPlatoModalCategoryChips = renderPlatoModalCategoryChips;

        function pickPlatoModalCategory(catName) {
            const inp = document.getElementById('platoCategoria');
            if (inp) inp.value = catName;
            renderPlatoModalCategoryChips();
        }
        window.pickPlatoModalCategory = pickPlatoModalCategory;

        function openModalNuevoPlato() {
            resetFormularioPlato();
            switchPlatoModalTab('basico');
            renderPlatoModalCategoryChips();
            document.getElementById('modalPlatoForm')?.classList.remove('hidden');
        }
        window.openModalNuevoPlato = openModalNuevoPlato;

        function closeModalPlatoForm() {
            document.getElementById('modalPlatoForm')?.classList.add('hidden');
        }
        window.closeModalPlatoForm = closeModalPlatoForm;

        function switchPlatoModalTab(tab) {
            ['basico', 'ingredientes', 'extras'].forEach(t => {
                const panel = document.getElementById(`platoTab_${t}`);
                const btn = document.getElementById(`btnPlatoTab_${t}`);
                if (panel) panel.classList.toggle('hidden', t !== tab);
                if (btn) {
                    const isHiddenByGiro = btn.classList.contains('hidden') && t === 'ingredientes';
                    btn.className = (t === tab
                        ? 'px-3.5 py-2 rounded-t-xl font-bold bg-slate-800 text-emerald-300 border-t border-x border-slate-700 transition flex items-center gap-1.5'
                        : 'px-3.5 py-2 rounded-t-xl font-semibold text-slate-400 hover:text-white transition flex items-center gap-1.5') + (isHiddenByGiro ? ' hidden' : '');
                }
            });
        }
        window.switchPlatoModalTab = switchPlatoModalTab;

        function togglePlatoCardDetails(platoId) {
            if (expandedPlatoIds.has(platoId)) expandedPlatoIds.delete(platoId);
            else expandedPlatoIds.add(platoId);
            const box = document.getElementById(`platoDetails_${platoId}`);
            const chev = document.getElementById(`platoChev_${platoId}`);
            if (box) box.classList.toggle('hidden', !expandedPlatoIds.has(platoId));
            if (chev) chev.className = `fa-solid fa-chevron-${expandedPlatoIds.has(platoId) ? 'up' : 'down'} text-[10px]`;
        }
        window.togglePlatoCardDetails = togglePlatoCardDetails;

        function selectMenuCategoryChip(cat) {
            activeCatalogCategory = cat || '';
            menuCurrentPage = 1;
            const sel = document.getElementById('filterCategoryMenuSelect');
            if (sel) sel.value = activeCatalogCategory;
            renderMenuCategoryChips();
            filterCatalogCards();
        }
        window.selectMenuCategoryChip = selectMenuCategoryChip;

        function onMenuCategorySelectChange() {
            const sel = document.getElementById('filterCategoryMenuSelect');
            activeCatalogCategory = sel ? sel.value : '';
            menuCurrentPage = 1;
            renderMenuCategoryChips();
            filterCatalogCards();
        }
        window.onMenuCategorySelectChange = onMenuCategorySelectChange;

        const MENU_PAGE_SIZE = 8;
        let menuCurrentPage = 1;

        function setMenuPage(page) {
            menuCurrentPage = Math.max(1, Number(page) || 1);
            filterCatalogCards(null, false);
            document.getElementById('tabContent_menu')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        window.setMenuPage = setMenuPage;

        function renderMenuCategoryChips() {
            const bar = document.getElementById('menuCategoriasChipsBar');
            if (!bar) return;
            bar.className = 'flex items-center gap-1.5 flex-nowrap overflow-x-auto sm:flex-wrap pb-1 sm:pb-0';
            const counts = {};
            allPlatosCache.forEach(p => {
                const c = p.categoria || 'General';
                counts[c] = (counts[c] || 0) + 1;
            });
            const cats = Object.keys(counts);
            let html = `
                <button type="button" onclick="selectMenuCategoryChip('')"
                    class="shrink-0 px-3 py-1.5 rounded-full text-xs font-bold border transition inline-flex items-center gap-1.5 ${
                        !activeCatalogCategory
                            ? 'bg-[#1B4332] text-white keep-white border-[#1B4332] shadow-sm'
                            : 'bg-slate-900 text-slate-300 border-slate-700 hover:bg-slate-800'
                    }">
                    <span>Todas</span>
                    <span class="text-[10px] px-1.5 py-0.2 rounded-full ${!activeCatalogCategory ? 'bg-white/20' : 'bg-slate-800 text-slate-400'}">${allPlatosCache.length}</span>
                </button>
            `;
            cats.forEach(c => {
                const active = activeCatalogCategory === c;
                html += `
                    <button type="button" onclick="selectMenuCategoryChip('${c.replace(/'/g, "\\'")}')"
                        class="shrink-0 px-3 py-1.5 rounded-full text-xs font-semibold border transition inline-flex items-center gap-1.5 ${
                            active
                                ? 'bg-[#1B4332] text-white keep-white border-[#1B4332] font-bold shadow-sm'
                                : 'bg-slate-900 text-slate-300 border-slate-700 hover:bg-slate-800'
                        }">
                        <span>${escapeHtml(c)}</span>
                        <span class="text-[10px] px-1.5 py-0.2 rounded-full ${active ? 'bg-white/20' : 'bg-slate-800 text-slate-400'}">${counts[c]}</span>
                    </button>
                `;
            });
            bar.innerHTML = html;
        }

        function renderMenuPlatos(platos, moneda) {
            allPlatosCache = platos || [];
            
            // Poblar selector de categorías y datalist del modal
            const categories = [...new Set(allPlatosCache.map(p => p.categoria || 'General'))].filter(Boolean);
            const catSelect = document.getElementById('filterCategoryMenuSelect');
            if (catSelect) {
                const currentVal = activeCatalogCategory || catSelect.value;
                catSelect.innerHTML = `<option value="">Todas las categorías (${categories.length})</option>` +
                    categories.map(c => `<option value="${escapeHtml(c)}" ${c === currentVal ? 'selected' : ''}>${escapeHtml(c)}</option>`).join('');
            }
            const catDatalist = document.getElementById('menuCategoriasDatalist');
            if (catDatalist) {
                catDatalist.innerHTML = categories.map(c => `<option value="${escapeHtml(c)}">`).join('');
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

            renderMenuCategoryChips();
            filterCatalogCards(moneda);
        }

        function filterCatalogCards(moneda = null, resetPage = true) {
            const sym = moneda || (currentComercioData && currentComercioData.comercio ? currentComercioData.comercio.simbolo_moneda : '$');
            const searchInput = document.getElementById('searchMenuInput');
            const catSelect = document.getElementById('filterCategoryMenuSelect');
            
            const q = (searchInput ? searchInput.value : '').toLowerCase().trim();
            const cat = catSelect ? catSelect.value : '';
            if (resetPage && q !== activeCatalogSearch) {
                menuCurrentPage = 1;
                activeCatalogSearch = q;
            }

            const filtered = allPlatosCache.filter(pl => {
                const matchQ = !q || (pl.nombre || '').toLowerCase().includes(q) || (pl.descripcion || '').toLowerCase().includes(q);
                const matchCat = !cat || (pl.categoria || '') === cat;
                return matchQ && matchCat;
            });

            const grid = document.getElementById('menuPlatosGrid');
            const pagBar = document.getElementById('menuPaginationBar');
            if (!grid) return;

            if (filtered.length === 0) {
                if (pagBar) pagBar.classList.add('hidden');
                grid.innerHTML = `
                    <div class="p-8 text-center rounded-2xl bg-slate-900/40 border border-slate-800">
                        <i class="fa-solid fa-magnifying-glass text-3xl block mb-2 text-slate-600"></i>
                        <p class="text-sm font-semibold text-slate-300">No se encontraron productos</p>
                        <p class="text-xs text-slate-500 mt-1">Prueba con otro término de búsqueda o agrega uno nuevo con el botón superior.</p>
                    </div>
                `;
                return;
            }

            const totalPages = Math.max(1, Math.ceil(filtered.length / MENU_PAGE_SIZE));
            if (menuCurrentPage > totalPages) menuCurrentPage = totalPages;
            const startIdx = (menuCurrentPage - 1) * MENU_PAGE_SIZE;
            const pageItems = filtered.slice(startIdx, startIdx + MENU_PAGE_SIZE);

            if (pagBar) {
                if (totalPages > 1) {
                    pagBar.classList.remove('hidden');
                    const endIdx = Math.min(startIdx + MENU_PAGE_SIZE, filtered.length);
                    pagBar.innerHTML = `
                        <span class="text-slate-400">Mostrando <strong class="text-white">${startIdx + 1}-${endIdx}</strong> de <strong class="text-white">${filtered.length}</strong> productos</span>
                        <div class="flex items-center gap-1.5">
                            <button type="button" onclick="setMenuPage(${menuCurrentPage - 1})" ${menuCurrentPage <= 1 ? 'disabled' : ''}
                                class="px-3 py-1.5 rounded-xl border text-xs font-semibold transition ${menuCurrentPage <= 1 ? 'bg-slate-900 text-slate-600 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 text-white border-slate-700'}">
                                <i class="fa-solid fa-chevron-left mr-1"></i> Anterior
                            </button>
                            <span class="px-3 py-1.5 rounded-xl bg-slate-900 border border-slate-800 text-slate-200 font-bold">
                                Página ${menuCurrentPage} / ${totalPages}
                            </span>
                            <button type="button" onclick="setMenuPage(${menuCurrentPage + 1})" ${menuCurrentPage >= totalPages ? 'disabled' : ''}
                                class="px-3 py-1.5 rounded-xl border text-xs font-semibold transition ${menuCurrentPage >= totalPages ? 'bg-slate-900 text-slate-600 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 text-white border-slate-700'}">
                                Siguiente <i class="fa-solid fa-chevron-right ml-1"></i>
                            </button>
                        </div>
                    `;
                } else {
                    pagBar.classList.add('hidden');
                }
            }

            const esAdmin = isComercioAdminOrSuper();
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            const activeSucFilterId = Number(document.getElementById('filtroSucursalSelect')?.value || (currentUser && currentUser.sucursal_id) || 0);

            // Agrupar los productos de la página actual por categoría
            const grupos = {};
            pageItems.forEach(pl => {
                const c = pl.categoria || 'General';
                if (!grupos[c]) grupos[c] = [];
                grupos[c].push(pl);
            });

            grid.innerHTML = Object.entries(grupos).map(([catName, itemsCat]) => `
                <div class="space-y-2.5">
                    <div class="flex items-center gap-2.5 pt-1 pb-1 border-b border-slate-800/70">
                        <span class="w-6 h-6 rounded-lg bg-slate-800/90 border border-slate-700 flex items-center justify-center text-emerald-400 text-[11px] shrink-0">
                            <i class="fa-solid fa-layer-group"></i>
                        </span>
                        <h4 class="text-xs font-extrabold uppercase tracking-wider text-white flex items-center gap-2">
                            <span>${escapeHtml(catName)}</span>
                        </h4>
                        <span class="px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 text-[10px] font-bold">${itemsCat.length} ${itemsCat.length === 1 ? 'artículo' : 'artículos'}</span>
                    </div>
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        ${itemsCat.map(pl => {
                            const agotadoEnSuc = activeSucFilterId > 0 && Array.isArray(pl.agotado_sucursal_ids) && pl.agotado_sucursal_ids.includes(activeSucFilterId);
                            const isAgotado = !pl.disponible_global || agotadoEnSuc;
                            const ingCount = (pl.ingredientes_removibles || []).length;
                            const exCount = (pl.extras_y_variantes || []).length;
                            const isOpen = expandedPlatoIds.has(pl.id);
                            const safeName = pl.nombre.replace(/'/g, "\\'");

                            return `
                                <div class="p-3.5 sm:p-4 rounded-2xl transition border flex flex-col justify-between gap-3 min-w-0 ${
                                    isAgotado
                                        ? 'bg-slate-950/70 border-rose-900/40 opacity-85'
                                        : 'bg-slate-900/90 hover:border-slate-700 border-slate-800 shadow-sm'
                                }">
                                    <!-- Fila superior: Miniatura + Contenido (Título, Precio, Estado y Descripción) -->
                                    <div class="flex items-start gap-3 min-w-0">
                                        ${pl.imagen_url ? `
                                            <img src="${pl.imagen_url}" alt="${escapeHtml(pl.nombre)}" loading="lazy"
                                                class="w-12 h-12 sm:w-14 sm:h-14 rounded-xl object-cover border border-[#DFD5C3] shrink-0">
                                        ` : ''}
                                        <div class="min-w-0 flex-1 space-y-1">
                                            <div class="flex items-start justify-between gap-2">
                                                <h5 class="font-bold text-sm text-white leading-snug break-words min-w-0">${escapeHtml(pl.nombre)}</h5>
                                                ${esAdmin ? `
                                                    <button type="button" onclick="editarPrecioRapidoPlato(${pl.id}, ${pl.precio_base}, '${safeName}', '${sym}')"
                                                        title="Clic para editar precio rápido"
                                                        class="text-xs sm:text-sm font-extrabold text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/25 px-2 sm:px-2.5 py-1 rounded-xl border border-emerald-500/30 transition flex items-center gap-1 shrink-0 cursor-pointer">
                                                        <span>${sym}${pl.precio_base.toFixed(2)}</span>
                                                        <i class="fa-solid fa-pen text-[9px] opacity-70"></i>
                                                    </button>
                                                ` : `
                                                    <span class="text-xs sm:text-sm font-extrabold text-emerald-400 bg-emerald-500/10 px-2 sm:px-2.5 py-1 rounded-xl border border-emerald-500/30 shrink-0">
                                                        ${sym}${pl.precio_base.toFixed(2)}
                                                    </span>
                                                `}
                                            </div>
                                            <div class="flex flex-wrap items-center gap-1.5">
                                                <span class="text-[10px] font-bold px-2 py-0.5 rounded-full ${
                                                    pl.disponible_global
                                                        ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                                        : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                                                }">
                                                    ${pl.disponible_global ? 'Disponible' : 'Agotado'}
                                                </span>
                                                ${agotadoEnSuc ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40">Agotado Sucursal</span>` : ''}
                                            </div>
                                            ${pl.descripcion ? `<p class="text-xs text-slate-400 line-clamp-2 leading-relaxed" title="${escapeHtml(pl.descripcion)}">${escapeHtml(pl.descripcion)}</p>` : ''}
                                        </div>
                                    </div>

                                    <!-- Barra compacta de acciones principales (responsiva en móvil) -->
                                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pt-2.5 border-t border-slate-800/80">
                                        <button type="button" onclick="togglePlatoCardDetails(${pl.id})"
                                            class="w-full sm:w-auto justify-between sm:justify-center px-3 py-1.5 rounded-xl bg-slate-800/90 hover:bg-slate-700 text-slate-200 border border-slate-700 text-[11px] font-semibold transition flex items-center gap-1.5">
                                            <span class="flex items-center gap-1.5 truncate">
                                                <i class="fa-solid fa-sliders text-indigo-400 shrink-0"></i>
                                                <span class="truncate">Opciones (${profile.usesRemovableIngredients ? `${ingCount} ing. · ` : ''}${exCount} ext.)</span>
                                            </span>
                                            <i id="platoChev_${pl.id}" class="fa-solid fa-chevron-${isOpen ? 'up' : 'down'} text-[10px] shrink-0"></i>
                                        </button>

                                        <div class="grid ${esAdmin ? 'grid-cols-2' : 'grid-cols-1'} sm:flex items-center gap-1.5 w-full sm:w-auto">
                                            <button type="button" onclick="togglePlatoDisponibilidad(${pl.id})"
                                                class="justify-center px-2.5 py-1.5 rounded-xl text-[11px] font-bold transition flex items-center gap-1 ${
                                                    pl.disponible_global
                                                        ? 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30'
                                                        : 'bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-extrabold'
                                                }">
                                                <i class="fa-solid fa-${pl.disponible_global ? 'pause' : 'play'} text-[10px]"></i>
                                                <span>${pl.disponible_global ? 'Agotar' : 'Habilitar'}</span>
                                            </button>
                                            ${esAdmin ? `
                                                <button type="button" onclick="cargarPlatoEnFormularioParaEditar(${pl.id})"
                                                    class="justify-center px-2.5 py-1.5 rounded-xl text-[11px] font-bold bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-200 border border-indigo-500/40 transition flex items-center gap-1">
                                                    <i class="fa-solid fa-pen-to-square"></i> <span>Editar</span>
                                                </button>
                                            ` : ''}
                                        </div>
                                    </div>

                                    <!-- Detalle desplegable bajo demanda (ingredientes, extras, sucursal, receta, borrar) -->
                                    <div id="platoDetails_${pl.id}" class="${isOpen ? '' : 'hidden'} pt-2.5 border-t border-slate-800 space-y-2.5">
                                        ${profile.usesRemovableIngredients ? `
                                            <div class="p-2.5 rounded-xl bg-slate-950/80 border border-slate-800 space-y-1.5">
                                                <div class="flex flex-wrap items-center justify-between gap-1 text-[11px]">
                                                    <span class="text-emerald-300 font-semibold"><i class="fa-solid fa-leaf mr-1"></i> Ingredientes Base (${sym}0)</span>
                                                    <span class="text-[10px] text-slate-500">Toca para apagar/encender</span>
                                                </div>
                                                <div class="flex flex-wrap gap-1.5">
                                                    ${ingCount > 0 ? (pl.ingredientes_removibles || []).map(ing => `
                                                        <button type="button" onclick="toggleIngredienteDisponibilidad(${ing.id})"
                                                            class="text-[11px] px-2 py-1 rounded-lg border font-medium transition flex items-center gap-1 ${
                                                                ing.disponible
                                                                    ? 'bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700'
                                                                    : 'bg-rose-950/60 text-rose-300 border-rose-500/50 line-through opacity-75'
                                                            }">
                                                            <i class="fa-solid fa-${ing.disponible ? 'check text-emerald-400' : 'xmark text-rose-400'} text-[9px]"></i>
                                                            <span>${escapeHtml(ing.nombre)}</span>
                                                        </button>
                                                    `).join('') : '<span class="text-[11px] text-slate-500 italic">Sin ingredientes base</span>'}
                                                </div>
                                            </div>
                                        ` : ''}

                                        <div class="p-2.5 rounded-xl bg-slate-950/80 border border-slate-800 space-y-1.5">
                                            <div class="flex flex-wrap items-center justify-between gap-1 text-[11px]">
                                                <span class="text-amber-300 font-semibold"><i class="fa-solid fa-circle-plus mr-1"></i> ${profile.usesRemovableIngredients ? 'Extras y Variantes' : 'Presentaciones / Variantes'}</span>
                                                <span class="text-[10px] text-slate-500">Toca para apagar o editar precio</span>
                                            </div>
                                            <div class="flex flex-wrap gap-1.5">
                                                ${exCount > 0 ? (pl.extras_y_variantes || []).map(ex => `
                                                    <div class="inline-flex items-center rounded-lg border transition ${
                                                        ex.disponible
                                                            ? 'bg-amber-500/10 text-amber-200 border-amber-500/30'
                                                            : 'bg-rose-950/60 text-rose-300 border-rose-500/50 line-through opacity-75'
                                                    }">
                                                        <button type="button" onclick="toggleIngredienteDisponibilidad(${ex.id})"
                                                            class="text-[11px] px-2 py-1 font-medium hover:bg-amber-500/20 flex items-center gap-1">
                                                            <span>${escapeHtml(ex.nombre)}</span>
                                                            <span class="font-bold text-emerald-400">(+${sym}${ex.precio_adicional.toFixed(2)})</span>
                                                        </button>
                                                        ${esAdmin ? `
                                                            <button type="button" onclick="editarPrecioRapidoExtra(${ex.id}, ${ex.precio_adicional}, '${ex.nombre.replace(/'/g, "\\'")}', '${sym}')"
                                                                class="px-1.5 py-1 text-[10px] text-amber-300 hover:text-white border-l border-amber-500/20">
                                                                <i class="fa-solid fa-pen"></i>
                                                            </button>
                                                        ` : ''}
                                                    </div>
                                                `).join('') : '<span class="text-[11px] text-slate-500 italic">Sin extras ni variantes</span>'}
                                            </div>
                                        </div>

                                        <!-- Acciones avanzadas dentro del desplegable -->
                                        <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
                                            <button type="button" onclick="toggleAgotadoEnSucursal(${pl.id})"
                                                class="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold transition flex items-center gap-1.5 ${
                                                    agotadoEnSuc
                                                        ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/40'
                                                        : 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700'
                                                }">
                                                <i class="fa-solid fa-store-slash text-[10px]"></i>
                                                <span>${agotadoEnSuc ? 'Habilitar en Sucursal' : 'Agotar solo en Sucursal'}</span>
                                            </button>
                                            ${esAdmin ? `
                                                <div class="flex items-center gap-1.5">
                                                    ${currentComercioData?.comercio?.inventario_habilitado ? `
                                                        <button type="button" onclick="openModalRecetaProducto(${pl.id}, '${safeName}')"
                                                            class="px-2.5 py-1.5 rounded-lg text-[11px] font-semibold bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30 transition flex items-center gap-1">
                                                            <i class="fa-solid fa-link"></i> <span>Stock / Receta</span>
                                                        </button>
                                                    ` : ''}
                                                    <button type="button" onclick="eliminarPlato(${pl.id})"
                                                        class="px-2.5 py-1.5 rounded-lg text-[11px] font-bold bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30 transition flex items-center gap-1">
                                                        <i class="fa-solid fa-trash"></i> <span>Borrar</span>
                                                    </button>
                                                </div>
                                            ` : ''}
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `).join('');
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

        let pendingPlatoImagenBase64 = null;
        let pendingPlatoEliminarImagen = false;

        function setPlatoImgPreviewUI(srcUrl, metaText = 'Se comprime automático a WebP (~20 KB)') {
            const img = document.getElementById('platoImgPreview');
            const ph = document.getElementById('platoImgPlaceholder');
            const btnDel = document.getElementById('btnQuitarFotoPlato');
            const meta = document.getElementById('platoImgMeta');
            if (meta) meta.textContent = metaText;
            if (srcUrl) {
                if (img) { img.src = srcUrl; img.classList.remove('hidden'); }
                if (ph) ph.classList.add('hidden');
                if (btnDel) btnDel.classList.remove('hidden');
            } else {
                if (img) { img.src = ''; img.classList.add('hidden'); }
                if (ph) ph.classList.remove('hidden');
                if (btnDel) btnDel.classList.add('hidden');
            }
        }

        function compressImageToWebP(file, maxSize = 480, quality = 0.72) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onerror = () => reject(new Error('No se pudo leer la imagen.'));
                reader.onload = () => {
                    const img = new Image();
                    img.onerror = () => reject(new Error('Archivo de imagen no válido.'));
                    img.onload = () => {
                        let w = img.width;
                        let h = img.height;
                        if (w > maxSize || h > maxSize) {
                            if (w >= h) {
                                h = Math.round((h * maxSize) / w);
                                w = maxSize;
                            } else {
                                w = Math.round((w * maxSize) / h);
                                h = maxSize;
                            }
                        }
                        const canvas = document.createElement('canvas');
                        canvas.width = w;
                        canvas.height = h;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(img, 0, 0, w, h);
                        let dataUrl = canvas.toDataURL('image/webp', quality);
                        if (!dataUrl.startsWith('data:image/webp')) {
                            dataUrl = canvas.toDataURL('image/jpeg', quality);
                        }
                        // Si aún pesara > 120 KB, recomprimir ligeramente
                        if (dataUrl.length > 160000) {
                            dataUrl = canvas.toDataURL('image/webp', 0.55);
                            if (!dataUrl.startsWith('data:image/webp')) {
                                dataUrl = canvas.toDataURL('image/jpeg', 0.55);
                            }
                        }
                        const approxKb = Math.max(1, Math.round((dataUrl.length * 0.75) / 1024));
                        resolve({ dataUrl, approxKb });
                    };
                    img.src = reader.result;
                };
                reader.readAsDataURL(file);
            });
        }

        async function handlePlatoImagenSeleccionada(input) {
            const file = input && input.files && input.files[0];
            if (!file) return;
            try {
                const { dataUrl, approxKb } = await compressImageToWebP(file, 480, 0.72);
                pendingPlatoImagenBase64 = dataUrl;
                pendingPlatoEliminarImagen = false;
                setPlatoImgPreviewUI(dataUrl, `Lista: ~${approxKb} KB (WebP optimizado)`);
            } catch (err) {
                showToast(err.message || 'No se pudo procesar la imagen.', true);
            } finally {
                if (input) input.value = '';
            }
        }
        window.handlePlatoImagenSeleccionada = handlePlatoImagenSeleccionada;

        function quitarFotoPlatoModal() {
            pendingPlatoImagenBase64 = null;
            pendingPlatoEliminarImagen = true;
            setPlatoImgPreviewUI(null, 'La foto se eliminará al guardar');
        }
        window.quitarFotoPlatoModal = quitarFotoPlatoModal;

        function cargarPlatoEnFormularioParaEditar(platoId) {
            const pl = (currentComercioData.platos || []).find(x => x.id === platoId);
            if (!pl) return;

            document.getElementById('platoEditId').value = pl.id;
            document.getElementById('platoCategoria').value = pl.categoria || 'General';
            document.getElementById('platoNombre').value = pl.nombre;
            document.getElementById('platoPrecio').value = pl.precio_base;
            document.getElementById('platoDesc').value = pl.descripcion || '';

            pendingPlatoImagenBase64 = null;
            pendingPlatoEliminarImagen = false;
            setPlatoImgPreviewUI(pl.imagen_url || null, pl.imagen_url ? 'Foto actual guardada' : 'Se comprime automático a WebP (~20 KB)');

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
            document.getElementById('btnSubmitPlatoText').textContent = 'Guardar Cambios';
            document.getElementById('btnSubmitPlato').className =
                'px-6 py-2.5 rounded-xl bg-indigo-500 hover:bg-indigo-600 text-white font-extrabold text-xs shadow-lg shadow-indigo-500/20 transition flex items-center gap-2';
            switchPlatoModalTab('basico');
            renderPlatoModalCategoryChips();
            document.getElementById('modalPlatoForm')?.classList.remove('hidden');
        }

        function resetFormularioPlato() {
            const profile = getGiroProfile(currentComercioData?.comercio?.giro);
            document.getElementById('platoEditId').value = '';
            document.getElementById('platoNombre').value = '';
            document.getElementById('platoPrecio').value = '';
            document.getElementById('platoDesc').value = '';
            pendingPlatoImagenBase64 = null;
            pendingPlatoEliminarImagen = false;
            setPlatoImgPreviewUI(null);
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
            document.getElementById('btnSubmitPlatoText').textContent = `Guardar ${profile.itemSingular}`;
            document.getElementById('btnSubmitPlato').className =
                'px-6 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 text-slate-950 font-extrabold text-xs shadow-lg shadow-emerald-500/20 transition flex items-center gap-2';
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
                closeModalPlatoForm();
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
                let savedPlatoId = editId ? Number(editId) : null;
                if (editId) {
                    await apiFetch(`/api/v1/panel/comercio/platos/${editId}`, {
                        method: 'PUT',
                        body: bodyPayload
                    });
                } else {
                    const created = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/platos`, {
                        method: 'POST',
                        body: bodyPayload
                    });
                    savedPlatoId = created?.plato_id || null;
                }

                if (savedPlatoId && pendingPlatoImagenBase64) {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/platos/${savedPlatoId}/imagen`, {
                        method: 'PUT',
                        body: { imagen_base64: pendingPlatoImagenBase64 }
                    });
                } else if (savedPlatoId && pendingPlatoEliminarImagen) {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/platos/${savedPlatoId}/imagen`, {
                        method: 'DELETE'
                    });
                }

                showToast(editId ? '¡Cambios del producto guardados correctamente!' : '¡Nuevo producto guardado correctamente!');
                resetFormularioPlato();
                closeModalPlatoForm();
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

        let pendingComercioLogoBase64 = null;
        let pendingComercioEliminarLogo = false;

        function setComercioLogoPreviewUI(srcUrl, metaText = 'WebP ligero (~10 KB) • Panel, Menú Web y Ticket') {
            const img = document.getElementById('cfgLogoPreview');
            const ph = document.getElementById('cfgLogoPlaceholder');
            const btnDel = document.getElementById('btnQuitarLogoComercio');
            const meta = document.getElementById('cfgLogoMeta');
            if (meta) meta.textContent = metaText;
            if (srcUrl) {
                if (img) { img.src = srcUrl; img.classList.remove('hidden'); }
                if (ph) ph.classList.add('hidden');
                if (btnDel) btnDel.classList.remove('hidden');
            } else {
                if (img) { img.src = ''; img.classList.add('hidden'); }
                if (ph) ph.classList.remove('hidden');
                if (btnDel) btnDel.classList.add('hidden');
            }
        }

        async function handleComercioLogoSeleccionado(input) {
            const file = input && input.files && input.files[0];
            if (!file) return;
            try {
                const { dataUrl, approxKb } = await compressImageToWebP(file, 256, 0.78);
                pendingComercioLogoBase64 = dataUrl;
                pendingComercioEliminarLogo = false;
                setComercioLogoPreviewUI(dataUrl, `Listo: ~${approxKb} KB (WebP 256px)`);
            } catch (err) {
                showToast(err.message || 'No se pudo procesar el logo.', true);
            } finally {
                if (input) input.value = '';
            }
        }
        window.handleComercioLogoSeleccionado = handleComercioLogoSeleccionado;

        function quitarLogoComercioModal() {
            pendingComercioLogoBase64 = null;
            pendingComercioEliminarLogo = true;
            setComercioLogoPreviewUI(null, 'El logo se eliminará al guardar');
        }
        window.quitarLogoComercioModal = quitarLogoComercioModal;

        function populateConfigComercioForm() {
            if (!currentComercioData || !currentComercioData.comercio) {
                return;
            }
            const c = currentComercioData.comercio;
            pendingComercioLogoBase64 = null;
            pendingComercioEliminarLogo = false;
            setComercioLogoPreviewUI(c.logo_url || null, c.logo_url ? 'Logo actual guardado' : 'WebP ligero (~10 KB) • Panel, Menú Web y Ticket');

            const elNombre = document.getElementById('cfgNombreComercial');
            if (elNombre) elNombre.value = c.nombre_comercial || '';
            const elTel = document.getElementById('cfgTelefono');
            if (elTel) elTel.value = c.telefono_propietario || '';

            const dCfg = c.delivery_config || {};
            const selDel = document.getElementById('cfgModoDelivery');
            if (selDel) {
                const optPropio = selDel.querySelector('option[value="PROPIO"]');
                const canPropio = !!(dCfg.permite_repartidor_propio || currentUser?.rol === 'SUPER_ADMIN');
                if (optPropio) {
                    optPropio.disabled = !canPropio;
                    optPropio.textContent = canPropio
                        ? 'Repartidor Propio del Negocio (Máx. 1)'
                        : 'Repartidor Propio (Inhabilitado — Contactar administrador)';
                }
                const hintEl = document.getElementById('cfgDeliveryPropioLockedHint');
                if (hintEl) hintEl.classList.toggle('hidden', canPropio);
                selDel.value = dCfg.modo_delivery || 'FLOTA_SAAS';
            }
            const cbPin = document.getElementById('cfgDeliveryPin');
            if (cbPin) cbPin.checked = dCfg.requiere_pin_entrega !== false;
            const cbWa = document.getElementById('cfgDeliveryAvisoWa');
            if (cbWa) cbWa.checked = dCfg.notificar_whatsapp_repartidor !== false;

            const fCfg = c.fidelidad_config || {};
            const cbFid = document.getElementById('cfgFidelidadActivo');
            if (cbFid) cbFid.checked = !!fCfg.activo;
            const elMeta = document.getElementById('cfgSellosMeta');
            if (elMeta) elMeta.value = fCfg.sellos_meta || 5;
            const elMin = document.getElementById('cfgMontoMinSello');
            if (elMin) elMin.value = fCfg.monto_minimo_sello ?? 150;
            const elPremio = document.getElementById('cfgPremioPct');
            if (elPremio) elPremio.value = fCfg.premio_descuento_pct || 15;
        }

        function openConfigComercioModal() {
            if (!currentComercioData || !currentComercioData.comercio) {
                showToast('Cargando información del restaurante...', true);
                return;
            }
            switchComercioTab('configuracion');
        }

        function closeConfigComercioModal() {
            const m = document.getElementById('modalConfigComercio');
            if (m) m.classList.add('hidden');
        }

        function setMonedaPreset(simbolo, iso) {
            const s = document.getElementById('cfgSimboloMoneda');
            const m = document.getElementById('cfgMonedaIso');
            if (s) s.value = simbolo;
            if (m) m.value = iso;
        }

        async function handleGuardarConfigComercio(e) {
            e.preventDefault();
            if (!activeTenantId) return;

            const body = {
                nombre_comercial: document.getElementById('cfgNombreComercial')?.value.trim() || undefined,
                telefono_propietario: document.getElementById('cfgTelefono')?.value.trim() || ''
            };

            try {
                await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/configuracion`, {
                    method: 'PATCH',
                    body
                });
                const selDel = document.getElementById('cfgModoDelivery');
                if (selDel) {
                    await apiFetch(`/api/v1/delivery/comercio/${activeTenantId}/config`, {
                        method: 'PUT',
                        body: {
                            modo_delivery: selDel.value,
                            requiere_pin_entrega: !!document.getElementById('cfgDeliveryPin')?.checked,
                            notificar_whatsapp_repartidor: !!document.getElementById('cfgDeliveryAvisoWa')?.checked
                        }
                    });
                }
                const cbFid = document.getElementById('cfgFidelidadActivo');
                if (cbFid) {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/fidelidad/config`, {
                        method: 'PUT',
                        body: {
                            activo: !!cbFid.checked,
                            sellos_meta: Number(document.getElementById('cfgSellosMeta')?.value || 5),
                            monto_minimo_sello: Number(document.getElementById('cfgMontoMinSello')?.value || 150),
                            premio_descuento_pct: Number(document.getElementById('cfgPremioPct')?.value || 15)
                        }
                    });
                }
                if (pendingComercioLogoBase64) {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/logo`, {
                        method: 'PUT',
                        body: { imagen_base64: pendingComercioLogoBase64 }
                    });
                } else if (pendingComercioEliminarLogo) {
                    await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/logo`, {
                        method: 'DELETE'
                    });
                }
                pendingComercioLogoBase64 = null;
                pendingComercioEliminarLogo = false;
                closeConfigComercioModal();
                showToast(`¡Configuración y logo actualizados con éxito!`);
                await reloadComercioDashboard();
                populateConfigComercioForm();
            } catch (err) {
                showToast(err.message, true);
            }
        }

        // =========================================================
        // CORTE / CIERRE DE CAJA DEL DÍA (LISTO PARA PAGADITO API)
        // =========================================================
        let ultimoCorteCajaData = null;

        async function cargarCorteCajaDiario() {
            if (!activeTenantId || !isComercioAdminOrSuper()) return;
            const grid = document.getElementById('corteCajaResumenGrid');
            const bar = document.getElementById('corteCajaExtraBar');
            const fechaIn = document.getElementById('corteCajaFechaInput');
            if (!grid) return;

            if (fechaIn && !fechaIn.value) {
                const hoy = new Date();
                fechaIn.value = hoy.toISOString().slice(0, 10);
            }
            const fechaVal = fechaIn ? fechaIn.value : '';
            const sucId = document.getElementById('filtroSucursalSelect')?.value || '';
            const params = new URLSearchParams();
            if (fechaVal) params.set('fecha', fechaVal);
            if (sucId) params.set('sucursal_id', sucId);

            try {
                const data = await apiFetch(`/api/v1/panel/comercio/${activeTenantId}/corte-caja?${params.toString()}`);
                ultimoCorteCajaData = data;
                const r = data.resumen || {};
                const s = data.simbolo_moneda || 'C$';

                grid.innerHTML = `
                    <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3]">
                        <div class="text-[11px] font-bold text-[#5C6B62] uppercase">Efectivo en Caja</div>
                        <div class="text-xl font-extrabold text-[#1B4332] mt-1">${s}${Number(r.efectivo_en_caja || 0).toFixed(2)}</div>
                        <div class="text-[11px] text-[#5C6B62] mt-0.5">Cobros en efectivo del día</div>
                    </div>
                    <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3]">
                        <div class="text-[11px] font-bold text-[#5C6B62] uppercase">Transferencias</div>
                        <div class="text-xl font-extrabold text-[#1A241E] mt-1">${s}${Number(r.transferencias || 0).toFixed(2)}</div>
                        <div class="text-[11px] text-[#5C6B62] mt-0.5">Depósitos / transferencias directas</div>
                    </div>
                    <div class="p-3.5 rounded-xl bg-[#FFFDF8] border border-[#DFD5C3]">
                        <div class="text-[11px] font-bold text-[#5C6B62] uppercase">Pasarela Online (Pagadito)</div>
                        <div class="text-xl font-extrabold text-[#D6402B] mt-1">${s}${Number(r.pago_online_api || 0).toFixed(2)}</div>
                        <div class="text-[11px] text-[#5C6B62] mt-0.5">Pagos aprobados vía API</div>
                    </div>
                    <div class="p-3.5 rounded-xl bg-[#E4EFE9] border border-[#B7D4C5]">
                        <div class="text-[11px] font-bold text-[#1B4332] uppercase">Total Ventas del Día</div>
                        <div class="text-xl font-extrabold text-[#1B4332] mt-1">${s}${Number(r.venta_bruta || 0).toFixed(2)}</div>
                        <div class="text-[11px] text-[#39483F] mt-0.5">Cobrado: <strong>${s}${Number(r.total_cobrado || 0).toFixed(2)}</strong> • Envíos: ${s}${Number(r.envios_total || 0).toFixed(2)}</div>
                    </div>
                `;
                if (bar) {
                    bar.innerHTML = `
                        <div class="flex flex-wrap items-center gap-3">
                            <span><strong class="text-[#1A241E]">${r.pedidos_validos || 0}</strong> pedidos confirmados</span>
                            <span>•</span>
                            <span>Anticipos reservas pagadas (${r.reservas_pagadas_count || 0}): <strong class="text-[#1B4332]">${s}${Number(r.anticipos_reservas || 0).toFixed(2)}</strong></span>
                            <span>•</span>
                            <span>Esperando pago: <strong class="text-[#92400E]">${r.pedidos_esperando_pago || 0}</strong></span>
                            <span>•</span>
                            <span>Cancelados: <strong class="text-[#B8321F]">${r.pedidos_cancelados || 0}</strong></span>
                        </div>
                        <span class="font-semibold">Fecha corte: ${escapeHtml(data.fecha)}</span>
                    `;
                }
            } catch (err) {
                grid.innerHTML = `<div class="col-span-full text-rose-400">${escapeHtml(err.message)}</div>`;
            }
        }
        window.cargarCorteCajaDiario = cargarCorteCajaDiario;

        function imprimirCorteCaja() {
            if (!ultimoCorteCajaData) {
                showToast('Primero carga el corte de caja.', true);
                return;
            }
            const d = ultimoCorteCajaData;
            const r = d.resumen || {};
            const s = d.simbolo_moneda || 'C$';
            const w = window.open('', '_blank', 'width=420,height=640');
            if (!w) return;
            w.document.write(`
                <html><head><title>Corte de Caja ${d.fecha}</title>
                <style>body{font-family:monospace;padding:20px;color:#111}h2{margin:0 0 4px}hr{border:none;border-top:1px dashed #333;margin:10px 0}.row{display:flex;justify-content:space-between;margin:5px 0}</style>
                </head><body>
                <h2>${escapeHtml(d.nombre_comercial)}</h2>
                <div>CORTE DE CAJA DIARIO — ${escapeHtml(d.fecha)}</div>
                <hr>
                <div class="row"><span>Pedidos confirmados:</span><strong>${r.pedidos_validos || 0}</strong></div>
                <div class="row"><span>Esperando pago:</span><span>${r.pedidos_esperando_pago || 0}</span></div>
                <div class="row"><span>Cancelados:</span><span>${r.pedidos_cancelados || 0}</span></div>
                <hr>
                <div class="row"><span>Efectivo en caja:</span><strong>${s}${Number(r.efectivo_en_caja || 0).toFixed(2)}</strong></div>
                <div class="row"><span>Transferencias:</span><strong>${s}${Number(r.transferencias || 0).toFixed(2)}</strong></div>
                <div class="row"><span>Pasarela Online (API):</span><strong>${s}${Number(r.pago_online_api || 0).toFixed(2)}</strong></div>
                <div class="row"><span>Anticipos reservas:</span><strong>${s}${Number(r.anticipos_reservas || 0).toFixed(2)}</strong></div>
                <div class="row"><span>Total envios:</span><span>${s}${Number(r.envios_total || 0).toFixed(2)}</span></div>
                <hr>
                <div class="row" style="font-size:16px"><span>VENTA BRUTA:</span><strong>${s}${Number(r.venta_bruta || 0).toFixed(2)}</strong></div>
                <div class="row" style="font-size:15px"><span>TOTAL COBRADO:</span><strong>${s}${Number(r.total_cobrado || 0).toFixed(2)}</strong></div>
                <script>window.print();<\/script>
                </body></html>
            `);
            w.document.close();
        }
        window.imprimirCorteCaja = imprimirCorteCaja;

        // =========================================================
        // SUCURSALES, QR DE WHATSAPP Y RESERVACIONES DE MESA
        // =========================================================
        function renderSucursalesYMesas(sucursales, comercio, reservaciones = []) {
            if (typeof renderSucursalesYMesasV2 === 'function') {
                renderSucursalesYMesasV2(sucursales, comercio, reservaciones);
                return;
            }
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
            if (!authToken) {
                finishAuthBoot();
                return;
            }
            try {
                const cachedUser = getCachedUser();
                if (cachedUser && cachedUser.rol) {
                    currentUser = cachedUser;
                    await renderAppByRole();
                    // Refrescar sesión/perfil en segundo plano sin bloquear el arranque
                    apiFetch('/api/v1/auth/me')
                        .then((freshUser) => {
                            if (freshUser && freshUser.rol) {
                                currentUser = freshUser;
                                setCachedUser(freshUser);
                                const badge = document.getElementById('userBadgeName');
                                if (badge) badge.textContent = freshUser.nombre_completo;
                                if (typeof update2FABadgeNavbar === 'function') update2FABadgeNavbar();
                            }
                        })
                        .catch(() => {});
                } else {
                    currentUser = await apiFetch('/api/v1/auth/me');
                    setCachedUser(currentUser);
                    await renderAppByRole();
                }
            } catch (e) {
                logout();
            } finally {
                finishAuthBoot();
            }
        });
