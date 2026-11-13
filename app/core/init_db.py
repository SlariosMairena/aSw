"""
Inicialización de Base de Datos (Neon.tech PostgreSQL / SQLite) y Datos Semilla (Seed).
Crea:
- Los 3 Planes del SaaS (Básico, Restaurante Pro, Híbrido Multi-Sucursal)
- Tu cuenta SUPER_ADMIN (Dueño del SaaS)
- Un Restaurante Demo ('Pizzería & Burger Napoli') con 2 Sucursales, Menú con Ingredientes
  Removibles ($0) y Extras con costo, Zonas de Envío, Mesas y un Pedido #ORD-1001 de muestra.
"""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models.saas_models import (
    CategoriaMenu,
    ClienteFrecuente,
    DetallePedido,
    IngredienteOpcionPlato,
    ItemInventario,
    MovimientoInventario,
    MensajeChatWhatsApp,
    MesaZona,
    PagoTransaccion,
    Pedido,
    PlanSaaS,
    ProductoPlato,
    SesionChatWhatsApp,
    SucursalComercio,
    SuscripcionFacturaSaaS,
    TenantComercio,
    Usuario,
    ZonaEnvioSucursal,
)


def init_database() -> None:
    """Crea el esquema aislado en PostgreSQL (si aplica), crea las tablas y carga la semilla inicial."""
    if settings.is_postgres and settings.DB_SCHEMA:
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}"'))
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_initial_data(db)
        _migrate_existing_data_to_nio(db)
    finally:
        db.close()


def _seed_initial_data(db: Session) -> None:
    # 1. Verificar si ya existen los Planes del SaaS
    if db.query(PlanSaaS).count() == 0:
        plan_basico = PlanSaaS(
            codigo="PLAN_BASICO",
            nombre="Plan Básico / Local Pequeño",
            descripcion="Hasta 200 pedidos/mes en WhatsApp, 1 sucursal, menú con ingredientes y pagos Modo 1.",
            precio_mensual_usd=29.0,
            limite_pedidos_mes=200,
            porcentaje_comision_pedido=0.0,
            tarifa_fija_pedido_extra_usd=0.25,
            max_sucursales=1,
            incluye_intervencion_humana=True,
            incluye_impresion_termica=True,
            incluye_reservacion_mesas=False,
        )
        plan_pro = PlanSaaS(
            codigo="PLAN_PRO",
            nombre="Plan Restaurante Pro",
            descripcion="Hasta 800 pedidos/mes, hasta 3 sucursales, control humano del chat, ticket térmico y reservación de mesas.",
            precio_mensual_usd=59.0,
            limite_pedidos_mes=800,
            porcentaje_comision_pedido=0.0,
            tarifa_fija_pedido_extra_usd=0.20,
            max_sucursales=3,
            incluye_intervencion_humana=True,
            incluye_impresion_termica=True,
            incluye_reservacion_mesas=True,
        )
        plan_hibrido = PlanSaaS(
            codigo="PLAN_HIBRIDO_PLUS",
            nombre="Plan Híbrido / Multi-Sucursal",
            descripcion="Cuota base accesible ($25/mes con 100 pedidos incluidos) + 2.5% por pedido adicional y múltiples sucursales.",
            precio_mensual_usd=25.0,
            limite_pedidos_mes=0,  # Ilimitados con comisión híbrida
            porcentaje_comision_pedido=2.5,
            tarifa_fija_pedido_extra_usd=0.0,
            max_sucursales=10,
            incluye_intervencion_humana=True,
            incluye_impresion_termica=True,
            incluye_reservacion_mesas=True,
        )
        db.add_all([plan_basico, plan_pro, plan_hibrido])
        db.commit()

    # 2. Verificar si existe tu usuario SUPER_ADMIN
    super_admin = (
        db.query(Usuario)
        .filter(Usuario.email == settings.SUPERADMIN_EMAIL)
        .first()
    )
    if not super_admin:
        super_admin = Usuario(
            tenant_id=None,
            sucursal_id=None,
            nombre_completo=settings.SUPERADMIN_NAME,
            email=settings.SUPERADMIN_EMAIL,
            password_hash=hash_password(settings.SUPERADMIN_PASSWORD),
            rol="SUPER_ADMIN",
            activo=True,
        )
        db.add(super_admin)
        db.commit()

    # 3. Crear Comercio Demo ("Pizzería & Burger Napoli") con 2 Sucursales si no hay comercios
    if db.query(TenantComercio).count() == 0:
        plan_pro = db.query(PlanSaaS).filter(PlanSaaS.codigo == "PLAN_PRO").first()
        comercio = TenantComercio(
            nombre_comercial="Pizzería & Burger Napoli",
            slug="napoli",
            giro="Restaurante / Pizzería y Hamburguesas",
            telefono_propietario="+525511223344",
            email_contacto="dueno@napoli.com",
            plan_id=plan_pro.id,
            estado_suscripcion="ACTIVO",
            fecha_vencimiento_suscripcion=datetime.now(timezone.utc) + timedelta(days=30),
            pedidos_mes_actual=1,
            moneda="NIO",
            simbolo_moneda="C$",
            acepta_efectivo=True,
            acepta_transferencia=True,
            datos_bancarios_texto=(
                "Banco: BAC Credomatic Nicaragua | Cuenta Córdobas: 365-123456-0 | "
                "Titular: Pizzería Napoli S.A."
            ),
            modo_pago_api_activo=False,  # Inicia en Modo 1 tal como se definió
            tiene_mesas_fisicas=True,
            reservaciones_habilitadas=False,  # Se habilita cuando se active Modo 2 de pagos
            monto_anticipo_reserva_default=350.0,
        )
        db.add(comercio)
        db.commit()
        db.refresh(comercio)

        # Factura SaaS activa del comercio hacia ti
        factura_saas = SuscripcionFacturaSaaS(
            tenant_id=comercio.id,
            periodo_mes=datetime.now(timezone.utc).strftime("%Y-%m"),
            pedidos_contabilizados=1,
            monto_mensualidad_fija=59.0,
            monto_comision_pedidos=0.0,
            total_a_pagar=59.0,
            estado_pago="PAGADO",
            referencia_pago="SUSC-INICIAL-001",
        )
        db.add(factura_saas)

        # Sucursal 1: Sucursal Centro (Principal)
        suc_centro = SucursalComercio(
            tenant_id=comercio.id,
            nombre_sucursal="Sucursal Centro",
            codigo_whatsapp="Napoli-Centro",
            es_principal=True,
            direccion_fisica="Av. Juárez #120, Col. Centro",
            telefono_sucursal="+525511223344",
            latitud=19.4326,
            longitud=-99.1332,
            horario_apertura="09:00",
            horario_cierre="23:00",
            abierto_ahora=True,
            tiempo_estimado_entrega_min=35,
            costo_envio_base=50.00,
            pedido_minimo_domicilio=150.00,
            envio_gratis_desde=600.00,
            ancho_ticket_termico="80mm",
            encabezado_ticket="PIZZERÍA & BURGER NAPOLI - CENTRO",
        )
        # Sucursal 2: Sucursal Norte
        suc_norte = SucursalComercio(
            tenant_id=comercio.id,
            nombre_sucursal="Sucursal Norte",
            codigo_whatsapp="Napoli-Norte",
            es_principal=False,
            direccion_fisica="Blvd. Norte #450, Plaza Las Torres",
            telefono_sucursal="+525599887766",
            latitud=19.4850,
            longitud=-99.1450,
            horario_apertura="10:00",
            horario_cierre="22:30",
            abierto_ahora=True,
            tiempo_estimado_entrega_min=40,
            costo_envio_base=60.00,
            pedido_minimo_domicilio=200.00,
            envio_gratis_desde=700.00,
            ancho_ticket_termico="58mm",
            encabezado_ticket="PIZZERÍA & BURGER NAPOLI - NORTE",
        )
        db.add_all([suc_centro, suc_norte])
        db.commit()
        db.refresh(suc_centro)
        db.refresh(suc_norte)

        # Zonas de Envío para Sucursal Centro
        db.add_all(
            [
                ZonaEnvioSucursal(
                    sucursal_id=suc_centro.id,
                    nombre_zona="Colonia Centro",
                    costo_envio=50.00,
                    tiempo_extra_min=0,
                ),
                ZonaEnvioSucursal(
                    sucursal_id=suc_centro.id,
                    nombre_zona="Zona Sur / Roma",
                    costo_envio=80.00,
                    tiempo_extra_min=10,
                ),
            ]
        )

        # Mesas físicas (listas en la estructura para cuando se active el Modo 2 de pagos)
        db.add_all(
            [
                MesaZona(
                    tenant_id=comercio.id,
                    sucursal_id=suc_centro.id,
                    nombre_mesa="Mesa 1 - Terraza Principal",
                    capacidad_personas=4,
                    monto_anticipo_reserva=10.0,
                ),
                MesaZona(
                    tenant_id=comercio.id,
                    sucursal_id=suc_centro.id,
                    nombre_mesa="Mesa 2 - Salón VIP",
                    capacidad_personas=6,
                    monto_anticipo_reserva=15.0,
                ),
            ]
        )

        # Usuarios del Restaurante (Dueño general y Cocina de Sucursal Centro)
        dueno_napoli = Usuario(
            tenant_id=comercio.id,
            sucursal_id=None,  # Ve todas las sucursales de su restaurante
            nombre_completo="Marco Rossi (Dueño Napoli)",
            email="dueno@napoli.com",
            password_hash=hash_password("Napoli#2026!"),
            rol="COMERCIO_ADMIN",
            activo=True,
        )
        cocina_centro = Usuario(
            tenant_id=comercio.id,
            sucursal_id=suc_centro.id,
            nombre_completo="Cocina Sucursal Centro",
            email="cocina.centro@napoli.com",
            password_hash=hash_password("Cocina#2026!"),
            rol="SUCURSAL_STAFF",
            activo=True,
        )
        db.add_all([dueno_napoli, cocina_centro])

        # Categorías del Menú
        cat_burgers = CategoriaMenu(
            tenant_id=comercio.id, nombre="Hamburguesas Artesanales", orden=1
        )
        cat_pizzas = CategoriaMenu(
            tenant_id=comercio.id, nombre="Pizzas al Horno", orden=2
        )
        cat_bebidas = CategoriaMenu(
            tenant_id=comercio.id, nombre="Bebidas", orden=3
        )
        db.add_all([cat_burgers, cat_pizzas, cat_bebidas])
        db.commit()
        db.refresh(cat_burgers)
        db.refresh(cat_pizzas)
        db.refresh(cat_bebidas)

        # Platos con su lista de Ingredientes Removibles (C$ 0) y Extras con costo
        plato_burger = ProductoPlato(
            tenant_id=comercio.id,
            categoria_id=cat_burgers.id,
            nombre="Hamburguesa Clásica Napoli",
            descripcion="Carne angus 200g, queso americano, lechuga, tomate, cebolla, pepinillos y mayonesa de la casa.",
            precio_base=180.00,
            tiempo_preparacion_min=15,
        )
        plato_pizza = ProductoPlato(
            tenant_id=comercio.id,
            categoria_id=cat_pizzas.id,
            nombre="Pizza Pepperoni Artesanal",
            descripcion="Masa madre, salsa pomodoro, mozzarella fresco, pepperoni, orégano y aceitunas negras.",
            precio_base=280.00,
            tiempo_preparacion_min=20,
        )
        plato_refresco = ProductoPlato(
            tenant_id=comercio.id,
            categoria_id=cat_bebidas.id,
            nombre="Refresco Lata 355ml",
            descripcion="Coca-Cola, Coca-Cola Sin Azúcar o Sprite bien frío.",
            precio_base=35.00,
            tiempo_preparacion_min=2,
        )
        db.add_all([plato_burger, plato_pizza, plato_refresco])
        db.commit()
        db.refresh(plato_burger)
        db.refresh(plato_pizza)
        db.refresh(plato_refresco)

        # Ingredientes y Opciones de la Hamburguesa (Para que el Bot sepa qué se puede quitar o agregar)
        db.add_all(
            [
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Cebolla",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Tomate",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Lechuga",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Mayonesa",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Pepinillos",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Extra Tocino Ahumado",
                    tipo="EXTRA",
                    precio_adicional=40.00,
                    es_removible=False,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_burger.id,
                    nombre="Extra Queso Cheddar",
                    tipo="EXTRA",
                    precio_adicional=30.00,
                    es_removible=False,
                ),
                # Ingredientes y variantes de la Pizza
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_pizza.id,
                    nombre="Aceitunas Negras",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_pizza.id,
                    nombre="Orégano",
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_pizza.id,
                    nombre="Tamaño Familiar",
                    tipo="VARIANTE",
                    precio_adicional=120.00,
                    es_removible=False,
                ),
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato_pizza.id,
                    nombre="Orilla Rellena de Queso",
                    tipo="EXTRA",
                    precio_adicional=60.00,
                    es_removible=False,
                ),
            ]
        )

        # Cliente frecuente y Sesión de Chat con un Pedido de Muestra (#ORD-1001)
        cliente_demo = ClienteFrecuente(
            tenant_id=comercio.id,
            telefono_whatsapp="5215544332211",
            nombre_cliente="Carlos Ruiz",
            ultima_direccion="Calle 10 #45, Col. Centro",
            ultima_zona_envio="Colonia Centro",
            ultima_latitud_gps=19.4335,
            ultima_longitud_gps=-99.1345,
            ultimo_pedido_texto="1x Hamburguesa Clásica (SIN Cebolla, EXTRA Tocino) + 2x Refresco",
            total_pedidos_realizados=1,
        )
        db.add(cliente_demo)
        db.commit()
        db.refresh(cliente_demo)

        sesion_demo = SesionChatWhatsApp(
            tenant_id=comercio.id,
            sucursal_id=suc_centro.id,
            telefono_cliente="5215544332211",
            nombre_cliente="Carlos Ruiz",
            modo_atencion="BOT_ACTIVO",
            estado_conversacion="COMPLETADO",
            resumen_ia_chat=(
                "El cliente Carlos Ruiz entró por el link directo de Sucursal Centro. "
                "Solicitó 1 Hamburguesa Clásica especificando que NO quiere cebolla ni tomate, "
                "y pidió agregar Extra Tocino (+C$ 40.00) más 2 Refrescos. Envió su ubicación GPS "
                "en Colonia Centro y eligió pagar en Efectivo con un billete de C$ 500 (requiere C$ 160.00 de cambio)."
            ),
            como_se_realizo_resumen=(
                "1. Entrada por QR/Link [Napoli-Centro] -> 2. Selección en menú + exclusión de ingredientes "
                "detectada por el bot ('sin cebolla, sin tomate') -> 3. Envío de Pin GPS en WhatsApp -> "
                "4. Confirmación de pago en Efectivo (Modo 1 en Córdobas)."
            ),
            activa=True,
        )
        db.add(sesion_demo)
        db.commit()
        db.refresh(sesion_demo)

        # Mensajes de esa conversación de WhatsApp
        db.add_all(
            [
                MensajeChatWhatsApp(
                    sesion_id=sesion_demo.id,
                    tenant_id=comercio.id,
                    remitente="CLIENTE",
                    tipo_contenido="TEXTO",
                    contenido="Hola! Quiero hacer un pedido en Pizzería & Burger Napoli [Napoli-Centro]",
                ),
                MensajeChatWhatsApp(
                    sesion_id=sesion_demo.id,
                    tenant_id=comercio.id,
                    remitente="BOT",
                    tipo_contenido="BOTON",
                    contenido="¡Bienvenido a Pizzería & Burger Napoli (Sucursal Centro)! 🍔🍕 Tiempo estimado hoy: 35 min. ¿Qué te gustaría ordenar?",
                ),
                MensajeChatWhatsApp(
                    sesion_id=sesion_demo.id,
                    tenant_id=comercio.id,
                    remitente="CLIENTE",
                    tipo_contenido="TEXTO",
                    contenido="Quiero 1 Hamburguesa Clásica pero sin cebolla y sin tomate, ponle extra tocino, y 2 refrescos de lata para Calle 10 #45 Col. Centro. Pago en efectivo con billete de C$ 500.",
                ),
                MensajeChatWhatsApp(
                    sesion_id=sesion_demo.id,
                    tenant_id=comercio.id,
                    remitente="BOT",
                    tipo_contenido="TEXTO",
                    contenido="¡Perfecto Carlos! Registré tu pedido #ORD-1001:\n• 1x Hamburguesa Clásica (C$ 180.00)\n  🚫 SIN: Cebolla, Tomate\n  ➕ EXTRA: Extra Tocino Ahumado (+C$ 40.00)\n• 2x Refresco Lata 355ml (C$ 70.00)\n• Envío Colonia Centro: C$ 50.00\n💰 Total: C$ 340.00 (Pagas con C$ 500.00 -> Cambio: C$ 160.00).\nEstado actual: 📥 RECIBIDO.",
                ),
            ]
        )

        # Pedido #ORD-1001
        pedido_demo = Pedido(
            folio="ORD-1001",
            tenant_id=comercio.id,
            sucursal_id=suc_centro.id,
            sesion_chat_id=sesion_demo.id,
            cliente_frecuente_id=cliente_demo.id,
            telefono_cliente="5215544332211",
            nombre_cliente="Carlos Ruiz",
            tipo_entrega="DOMICILIO",
            direccion_entrega="Calle 10 #45, Col. Centro",
            zona_envio_nombre="Colonia Centro",
            latitud_gps=19.4335,
            longitud_gps=-99.1345,
            google_maps_link="https://www.google.com/maps?q=19.4335,-99.1345",
            estado_pedido="RECIBIDO",
            subtotal=290.00,
            costo_envio=50.00,
            total=340.00,
            resumen_chat_ia=sesion_demo.resumen_ia_chat,
            como_se_realizo_pedido=sesion_demo.como_se_realizo_resumen,
            notas_especiales="Tocar el timbre 2 veces. Llevar cambio de C$ 500.",
            metodo_pago="EFECTIVO",
            estado_pago="PENDIENTE",
            paga_con_monto=500.00,
            cambio_a_entregar=160.00,
        )
        db.add(pedido_demo)
        db.commit()
        db.refresh(pedido_demo)

        db.add_all(
            [
                DetallePedido(
                    pedido_id=pedido_demo.id,
                    plato_id=plato_burger.id,
                    nombre_plato="Hamburguesa Clásica Napoli",
                    cantidad=1,
                    precio_unitario_base=180.00,
                    sin_ingredientes_json=json.dumps(["Cebolla", "Tomate"], ensure_ascii=False),
                    extras_json=json.dumps(
                        [{"nombre": "Extra Tocino Ahumado", "precio": 40.00}],
                        ensure_ascii=False,
                    ),
                    precio_extras_unitario=40.00,
                    subtotal_linea=220.00,
                    notas_plato="Bien cocida",
                ),
                DetallePedido(
                    pedido_id=pedido_demo.id,
                    plato_id=plato_refresco.id,
                    nombre_plato="Refresco Lata 355ml",
                    cantidad=2,
                    precio_unitario_base=35.00,
                    sin_ingredientes_json="[]",
                    extras_json="[]",
                    precio_extras_unitario=0.0,
                    subtotal_linea=70.00,
                ),
            ]
        )

        db.add(
            PagoTransaccion(
                tenant_id=comercio.id,
                pedido_id=pedido_demo.id,
                modo_operacion="MODO_1_DIRECTO",
                metodo="EFECTIVO",
                monto=340.00,
                moneda="NIO",
                estado="PENDIENTE",
                paga_con=500.00,
                cambio=160.00,
            )
        )
        db.commit()


def _migrate_existing_data_to_nio(db: Session) -> None:
    """
    Migra comercios existentes con moneda 'USD' / '$' a Córdobas ('NIO' / 'C$')
    y escala los precios de muestra a moneda nacional (Córdobas nicaragüenses).
    """
    comercios = db.query(TenantComercio).all()
    for c in comercios:
        if c.moneda == "USD" or c.simbolo_moneda == "$":
            c.moneda = "NIO"
            c.simbolo_moneda = "C$"
            if not c.datos_bancarios_texto or "BBVA" in c.datos_bancarios_texto or "0000" in c.datos_bancarios_texto:
                c.datos_bancarios_texto = (
                    "Banco: BAC Credomatic Nicaragua | Cuenta Córdobas: 365-123456-0 | "
                    "Titular: Pizzería Napoli S.A."
                )

    # Actualizar platos de muestra si siguen en precios bajos de dólares (< 30)
    platos = db.query(ProductoPlato).all()
    for pl in platos:
        if pl.precio_base < 30.0:
            if "Hamburguesa" in pl.nombre:
                pl.precio_base = 180.00
            elif "Pizza" in pl.nombre:
                pl.precio_base = 280.00
            elif "Refresco" in pl.nombre:
                pl.precio_base = 35.00
            else:
                pl.precio_base = round(pl.precio_base * 36.5, 2)

    # Actualizar extras si estaban en dólares (< 15)
    extras = db.query(IngredienteOpcionPlato).all()
    for ex in extras:
        if 0 < ex.precio_adicional < 15.0:
            if "Tocino" in ex.nombre:
                ex.precio_adicional = 40.00
            elif "Cheddar" in ex.nombre:
                ex.precio_adicional = 30.00
            elif "Familiar" in ex.nombre:
                ex.precio_adicional = 120.00
            elif "Orilla" in ex.nombre:
                ex.precio_adicional = 60.00
            else:
                ex.precio_adicional = round(ex.precio_adicional * 36.5, 2)

    # Actualizar sucursales
    sucursales = db.query(SucursalComercio).all()
    for suc in sucursales:
        if suc.costo_envio_base < 10.0:
            if "Centro" in suc.nombre_sucursal:
                suc.costo_envio_base = 50.00
                suc.pedido_minimo_domicilio = 150.00
                suc.envio_gratis_desde = 600.00
            else:
                suc.costo_envio_base = 60.00
                suc.pedido_minimo_domicilio = 200.00
                suc.envio_gratis_desde = 700.00

    # Zonas de envío
    zonas = db.query(ZonaEnvioSucursal).all()
    for z in zonas:
        if z.costo_envio < 10.0:
            if "Centro" in z.nombre_zona:
                z.costo_envio = 50.00
            else:
                z.costo_envio = 80.00

    # Pedido demo ORD-1001
    pedidos = db.query(Pedido).filter(Pedido.folio == "ORD-1001").all()
    for ped in pedidos:
        if ped.total < 50.0:
            ped.subtotal = 290.00
            ped.costo_envio = 50.00
            ped.total = 340.00
            ped.paga_con_monto = 500.00
            ped.cambio_a_entregar = 160.00
            ped.notas_especiales = "Tocar el timbre 2 veces. Llevar cambio de C$ 500."
            ped.resumen_chat_ia = (
                "El cliente Carlos Ruiz solicitó 1 Hamburguesa Clásica Napoli SIN cebolla ni tomate "
                "y con Extra Tocino (+C$ 40), más 2 Refrescos de lata. Total C$ 340.00. Pagará en Efectivo con C$ 500.00."
            )

    detalles = db.query(DetallePedido).all()
    for d in detalles:
        if "Hamburguesa" in d.nombre_plato and d.precio_unitario_base < 25.0:
            d.precio_unitario_base = 180.00
            d.precio_extras_unitario = 40.00
            d.subtotal_linea = 220.00
            d.extras_json = json.dumps([{"nombre": "Extra Tocino Ahumado", "precio": 40.00}], ensure_ascii=False)
        elif "Refresco" in d.nombre_plato and d.precio_unitario_base < 10.0:
            d.precio_unitario_base = 35.00
            d.subtotal_linea = 70.00

    # Inicializar insumos de inventario si no existen
    for c in comercios:
        if db.query(ItemInventario).filter(ItemInventario.tenant_id == c.id).count() == 0:
            suc = db.query(SucursalComercio).filter(SucursalComercio.tenant_id == c.id).first()
            suc_id = suc.id if suc else None
            db.add_all([
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Carne Angus Molida",
                    categoria="Carnes",
                    unidad_medida="kg",
                    stock_actual=24.5,
                    stock_minimo=5.0,
                    costo_unitario=220.00,
                    proveedor="Carnicería San Martín",
                    notas="Para medallones de hamburguesas 200g"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Pan Brioche Artesanal",
                    categoria="Panadería",
                    unidad_medida="unidades",
                    stock_actual=60.0,
                    stock_minimo=15.0,
                    costo_unitario=18.00,
                    proveedor="Panadería La Unión",
                    notas="Entrega fresca diaria"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Queso Cheddar Americano",
                    categoria="Lácteos",
                    unidad_medida="láminas",
                    stock_actual=80.0,
                    stock_minimo=20.0,
                    costo_unitario=8.00,
                    proveedor="Lácteos del Norte"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Tocino Ahumado Especial",
                    categoria="Carnes",
                    unidad_medida="porciones",
                    stock_actual=45.0,
                    stock_minimo=10.0,
                    costo_unitario=15.00,
                    proveedor="Embutidos La Famosa"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Masa Madre para Pizza",
                    categoria="Panadería",
                    unidad_medida="bolas",
                    stock_actual=28.0,
                    stock_minimo=8.0,
                    costo_unitario=25.00,
                    proveedor="Producción Propia Cocina",
                    notas="Fermentación 48 horas"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Queso Mozzarella Fresco",
                    categoria="Lácteos",
                    unidad_medida="kg",
                    stock_actual=18.0,
                    stock_minimo=5.0,
                    costo_unitario=160.00,
                    proveedor="Lácteos La Sureña"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Pepperoni Artesanal Rodajas",
                    categoria="Carnes",
                    unidad_medida="paquetes",
                    stock_actual=12.0,
                    stock_minimo=3.0,
                    costo_unitario=95.00,
                    proveedor="Embutidos La Famosa"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Salsa Pomodoro Casera",
                    categoria="Salsas",
                    unidad_medida="litros",
                    stock_actual=15.0,
                    stock_minimo=4.0,
                    costo_unitario=45.00,
                    proveedor="Producción Propia Cocina"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Coca-Cola Regular Lata 355ml",
                    categoria="Bebidas",
                    unidad_medida="latas",
                    stock_actual=48.0,
                    stock_minimo=12.0,
                    costo_unitario=20.00,
                    proveedor="Coca-Cola FEMSA Nicaragua"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Sprite Lata 355ml",
                    categoria="Bebidas",
                    unidad_medida="latas",
                    stock_actual=4.0,
                    stock_minimo=12.0,
                    costo_unitario=20.00,
                    proveedor="Coca-Cola FEMSA Nicaragua",
                    notas="⚠️ Alerta: Reorden urgente"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Aceitunas Negras Rebanadas",
                    categoria="Vegetales",
                    unidad_medida="latas",
                    stock_actual=0.0,
                    stock_minimo=2.0,
                    costo_unitario=65.00,
                    proveedor="Distribuidora España",
                    notas="🚨 Agotado: Próximo despacho viernes"
                ),
                ItemInventario(
                    tenant_id=c.id,
                    sucursal_id=suc_id,
                    nombre_insumo="Caja de Pizza Térmica",
                    categoria="Empaques",
                    unidad_medida="cajas",
                    stock_actual=110.0,
                    stock_minimo=25.0,
                    costo_unitario=12.00,
                    proveedor="Empaques Modernos Managua"
                ),
            ])

    db.commit()

