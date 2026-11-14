"""
Modelos de Base de Datos SaaS Multi-Tenant y Multi-Sucursal (SQLAlchemy 2.0).
Incluye:
- Planes y Suscripciones del SaaS (Nivel A de Monetización)
- Comercios (Tenants), Múltiples Sucursales y Zonas de Envío
- Usuarios con Roles (SUPER_ADMIN, COMERCIO_ADMIN, SUCURSAL_STAFF) y 2FA
- Menú Inteligente: Platos, Ingredientes Removibles ($0), Variantes, Extras y Stock ("Agotado hoy")
- Clientes Frecuentes (Memoria de nombre, dirección y GPS)
- Sesiones de Chat WhatsApp con Interruptor Bot/Humano y Resumen con IA
- Pedidos, Detalles de Pedido (con "SIN X ingrediente") y Pagos (Modo 1 y Modo 2)
- Reservación de Mesas (Con pago obligatorio al habilitar Modo 2)
- Registro Inmutable de Auditoría de Seguridad (ISO/IEC 27001)
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
# 1. MONETIZACIÓN DEL SAAS: PLANES Y SUSCRIPCIONES DE COMERCIOS
# =====================================================================
class PlanSaaS(Base):
    """Planes que tú (SUPER_ADMIN) ofreces a los restaurantes/comercios."""

    __tablename__ = "planes_saas"

    id = Column(Integer, primary_key=True, index=True)
    codigo = Column(String(50), unique=True, nullable=False, index=True)
    nombre = Column(String(120), nullable=False)
    descripcion = Column(Text, nullable=True)

    # Esquema de cobro: Mensualidad fija, Porcentaje por pedido o Híbrido
    precio_mensual_usd = Column(Float, default=29.0, nullable=False)
    limite_pedidos_mes = Column(Integer, default=200, nullable=False)  # 0 = Ilimitados
    porcentaje_comision_pedido = Column(Float, default=0.0, nullable=False)  # Ej. 3.0%
    tarifa_fija_pedido_extra_usd = Column(Float, default=0.20, nullable=False)

    # Capacidades incluidas en el plan
    max_sucursales = Column(Integer, default=1, nullable=False)
    incluye_intervencion_humana = Column(Boolean, default=True, nullable=False)
    incluye_impresion_termica = Column(Boolean, default=True, nullable=False)
    incluye_reservacion_mesas = Column(Boolean, default=True, nullable=False)
    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercios = relationship("TenantComercio", back_populates="plan")


class TenantComercio(Base):
    """Cada Restaurante o Comercio cliente de tu SaaS (Multi-Tenant)."""

    __tablename__ = "tenants_comercios"

    id = Column(Integer, primary_key=True, index=True)
    nombre_comercial = Column(String(150), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    giro = Column(String(80), default="Restaurante", nullable=False)
    telefono_propietario = Column(String(30), nullable=True)
    email_contacto = Column(String(150), nullable=True)

    # Suscripción al SaaS
    plan_id = Column(Integer, ForeignKey("planes_saas.id"), nullable=False)
    estado_suscripcion = Column(
        String(30), default="ACTIVO", nullable=False, index=True
    )  # TRIAL | ACTIVO | POR_VENCER | SUSPENDIDO
    fecha_vencimiento_suscripcion = Column(DateTime(timezone=True), nullable=True)
    pedidos_mes_actual = Column(Integer, default=0, nullable=False)

    # Configuración Monetaria y de Pagos del Comercio (Nivel B)
    moneda = Column(String(10), default="NIO", nullable=False)
    simbolo_moneda = Column(String(10), default="C$", nullable=False)

    # Modo 1 (Directo al Restaurante: Efectivo y Transferencia con foto de comprobante)
    acepta_efectivo = Column(Boolean, default=True, nullable=False)
    acepta_transferencia = Column(Boolean, default=True, nullable=False)
    datos_bancarios_texto = Column(
        Text,
        nullable=True,
        default="Banco: Por definir | Cuenta/CLABE: 0000000000 | Titular: Restaurante",
    )

    # Modo 2 (Conector API de Pasarela de Pagos - Listo en estructura para activarse después)
    modo_pago_api_activo = Column(Boolean, default=False, nullable=False)
    proveedor_pago_api = Column(String(50), default="none", nullable=False)  # stripe | mercadopago | none
    credenciales_pago_cifradas = Column(Text, nullable=True)  # Cifrado AES-256

    # Módulo de Reservación de Mesas (Se habilita cuando se activa el método de pago API)
    tiene_mesas_fisicas = Column(Boolean, default=True, nullable=False)
    reservaciones_habilitadas = Column(Boolean, default=False, nullable=False)
    monto_anticipo_reserva_default = Column(Float, default=10.0, nullable=False)

    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relaciones
    plan = relationship("PlanSaaS", back_populates="comercios")
    sucursales = relationship(
        "SucursalComercio", back_populates="comercio", cascade="all, delete-orphan"
    )
    usuarios = relationship(
        "Usuario", back_populates="comercio", cascade="all, delete-orphan"
    )
    categorias = relationship(
        "CategoriaMenu", back_populates="comercio", cascade="all, delete-orphan"
    )
    platos = relationship(
        "ProductoPlato", back_populates="comercio", cascade="all, delete-orphan"
    )
    clientes_frecuentes = relationship(
        "ClienteFrecuente", back_populates="comercio", cascade="all, delete-orphan"
    )
    facturas_saas = relationship(
        "SuscripcionFacturaSaaS", back_populates="comercio", cascade="all, delete-orphan"
    )


class SuscripcionFacturaSaaS(Base):
    """Historial de pagos que los restaurantes te hacen a ti por usar el SaaS."""

    __tablename__ = "suscripciones_facturas_saas"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    periodo_mes = Column(String(20), nullable=False)  # Ej. "2026-11"
    pedidos_contabilizados = Column(Integer, default=0, nullable=False)
    monto_mensualidad_fija = Column(Float, default=0.0, nullable=False)
    monto_comision_pedidos = Column(Float, default=0.0, nullable=False)
    total_a_pagar = Column(Float, default=0.0, nullable=False)
    estado_pago = Column(
        String(30), default="PAGADO", nullable=False
    )  # PENDIENTE | PAGADO | VENCIDO
    referencia_pago = Column(String(120), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercio = relationship("TenantComercio", back_populates="facturas_saas")


# =====================================================================
# 2. ARQUITECTURA MULTI-SUCURSAL Y ZONAS DE ENVÍO
# =====================================================================
class SucursalComercio(Base):
    """
    Soporte Multi-Sucursal:
    - Si el comercio tiene 1 solo local, usa su 'Sucursal Principal'.
    - Si tiene varias sucursales, cada una tiene su propio código/QR de WhatsApp,
      horarios, stock de 'Agotado hoy', impresora térmica y tablero de pedidos.
    """

    __tablename__ = "sucursales_comercio"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    nombre_sucursal = Column(String(120), nullable=False)
    # Código único para el Link/QR Opción B: ej. "Napoli-Centro"
    codigo_whatsapp = Column(String(80), unique=True, nullable=False, index=True)
    es_principal = Column(Boolean, default=True, nullable=False)

    direccion_fisica = Column(String(255), nullable=True)
    telefono_sucursal = Column(String(30), nullable=True)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)

    # Horarios automáticos y tiempo estimado de entrega en vivo
    horario_apertura = Column(String(10), default="09:00", nullable=False)
    horario_cierre = Column(String(10), default="23:00", nullable=False)
    abierto_ahora = Column(Boolean, default=True, nullable=False)
    tiempo_estimado_entrega_min = Column(Integer, default=35, nullable=False)

    # Tarifas base de envío y mínimo de compra
    permite_domicilio = Column(Boolean, default=True, nullable=False)
    permite_recoger_local = Column(Boolean, default=True, nullable=False)
    costo_envio_base = Column(Float, default=2.50, nullable=False)
    pedido_minimo_domicilio = Column(Float, default=8.0, nullable=False)
    envio_gratis_desde = Column(Float, default=30.0, nullable=False)

    # Configuración de Impresión de Ticket Térmico (58mm / 80mm)
    ancho_ticket_termico = Column(String(10), default="80mm", nullable=False)  # 58mm | 80mm
    encabezado_ticket = Column(String(200), nullable=True)
    pie_ticket = Column(
        String(200), default="¡Gracias por tu pedido por WhatsApp!", nullable=True
    )

    activa = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercio = relationship("TenantComercio", back_populates="sucursales")
    zonas_envio = relationship(
        "ZonaEnvioSucursal", back_populates="sucursal", cascade="all, delete-orphan"
    )
    mesas = relationship(
        "MesaZona", back_populates="sucursal", cascade="all, delete-orphan"
    )
    pedidos = relationship("Pedido", back_populates="sucursal")
    sesiones_chat = relationship("SesionChatWhatsApp", back_populates="sucursal")


class ZonaEnvioSucursal(Base):
    """Zonas o colonias de reparto con costo de envío específico por sucursal."""

    __tablename__ = "zonas_envio_sucursal"

    id = Column(Integer, primary_key=True, index=True)
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    nombre_zona = Column(String(120), nullable=False)  # Ej. "Colonia Centro", "Zona Norte"
    costo_envio = Column(Float, default=2.50, nullable=False)
    tiempo_extra_min = Column(Integer, default=0, nullable=False)
    activa = Column(Boolean, default=True, nullable=False)

    sucursal = relationship("SucursalComercio", back_populates="zonas_envio")


# =====================================================================
# 3. USUARIOS Y CONTROL DE ACCESO (RBAC + 2FA)
# =====================================================================
class Usuario(Base):
    """
    Usuarios del Panel Web:
    - SUPER_ADMIN: Tú (dueño del SaaS, tenant_id = None)
    - COMERCIO_ADMIN: Dueño de un restaurante (ve todas las sucursales de su tenant_id)
    - SUCURSAL_STAFF: Cajero/Cocina asignado a una sucursal específica
    """

    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=True, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=True, index=True
    )

    nombre_completo = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # Argon2id
    rol = Column(
        String(30), default="COMERCIO_ADMIN", nullable=False, index=True
    )  # SUPER_ADMIN | COMERCIO_ADMIN | SUCURSAL_STAFF

    # Seguridad 2FA (ISO 27001 / OWASP)
    totp_secret_encrypted = Column(Text, nullable=True)  # Cifrado AES-256
    is_2fa_enabled = Column(Boolean, default=False, nullable=False)

    activo = Column(Boolean, default=True, nullable=False)
    ultimo_acceso = Column(DateTime(timezone=True), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercio = relationship("TenantComercio", back_populates="usuarios")


# =====================================================================
# 4. MENÚ INTELIGENTE: PLATOS, INGREDIENTES REMOVIBLES, EXTRAS Y STOCK
# =====================================================================
class CategoriaMenu(Base):
    """Categorías del menú del comercio (ej. Hamburguesas, Pizzas, Bebidas)."""

    __tablename__ = "categorias_menu"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    nombre = Column(String(100), nullable=False)
    orden = Column(Integer, default=1, nullable=False)
    activa = Column(Boolean, default=True, nullable=False)

    comercio = relationship("TenantComercio", back_populates="categorias")
    platos = relationship(
        "ProductoPlato", back_populates="categoria", cascade="all, delete-orphan"
    )


class ProductoPlato(Base):
    """Plato o producto del restaurante con precio base y lista de ingredientes."""

    __tablename__ = "productos_platos"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    categoria_id = Column(
        Integer, ForeignKey("categorias_menu.id"), nullable=False, index=True
    )

    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text, nullable=True)
    precio_base = Column(Float, nullable=False)
    tiempo_preparacion_min = Column(Integer, default=15, nullable=False)
    disponible_global = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercio = relationship("TenantComercio", back_populates="platos")
    categoria = relationship("CategoriaMenu", back_populates="platos")
    ingredientes_opciones = relationship(
        "IngredienteOpcionPlato", back_populates="plato", cascade="all, delete-orphan"
    )


class IngredienteOpcionPlato(Base):
    """
    Lista de ingredientes y modificadores de cada plato para que el Bot sea 100% consciente:
    - INGREDIENTE_BASE: Viene incluido en el plato y el cliente puede pedir quitarlo sin costo ("sin cebolla $0").
    - VARIANTE: Cambia el tamaño o presentación ("Tamaño Familiar +$5.00").
    - EXTRA: Ingrediente adicional con costo extra ("+Tocino +$2.00", "+Queso extra +$1.50").
    """

    __tablename__ = "ingredientes_opciones_plato"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    plato_id = Column(
        Integer, ForeignKey("productos_platos.id"), nullable=False, index=True
    )

    nombre = Column(String(100), nullable=False)  # Ej. "Cebolla", "Tomate", "Tocino"
    tipo = Column(
        String(30), default="INGREDIENTE_BASE", nullable=False
    )  # INGREDIENTE_BASE | VARIANTE | EXTRA
    precio_adicional = Column(Float, default=0.0, nullable=False)  # 0.0 si es removible base
    es_removible = Column(Boolean, default=True, nullable=False)
    disponible_global = Column(Boolean, default=True, nullable=False)

    plato = relationship("ProductoPlato", back_populates="ingredientes_opciones")


class StockSucursal(Base):
    """
    Interruptor rápido de 'Agotado hoy' en 1 clic por sucursal:
    Permite apagar un plato o un ingrediente únicamente en una sucursal sin afectar a las demás.
    """

    __tablename__ = "stock_sucursal"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    plato_id = Column(
        Integer, ForeignKey("productos_platos.id"), nullable=True, index=True
    )
    ingrediente_id = Column(
        Integer, ForeignKey("ingredientes_opciones_plato.id"), nullable=True, index=True
    )
    agotado_hoy = Column(Boolean, default=True, nullable=False)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# =====================================================================
# 4.B CONTROL DE INVENTARIO Y STOCK PARA RESTAURANTES
# =====================================================================
class ItemInventario(Base):
    """
    Control de inventario de materias primas, insumos y productos del restaurante.
    """

    __tablename__ = "items_inventario"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=True, index=True
    )
    nombre_insumo = Column(String(120), nullable=False, index=True)
    categoria = Column(String(60), default="General", nullable=False, index=True)
    unidad_medida = Column(String(30), default="unidades", nullable=False)
    stock_actual = Column(Float, default=0.0, nullable=False)
    stock_minimo = Column(Float, default=5.0, nullable=False)
    costo_unitario = Column(Float, default=0.0, nullable=False)  # En Córdobas C$
    proveedor = Column(String(100), nullable=True)
    notas = Column(Text, nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    actualizado_en = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class MovimientoInventario(Base):
    """
    Kardex / Historial de entradas (compras), salidas (ventas) y ajustes de merma o conteo físico.
    """

    __tablename__ = "movimientos_inventario"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    item_id = Column(
        Integer, ForeignKey("items_inventario.id"), nullable=False, index=True
    )
    tipo_movimiento = Column(
        String(30), nullable=False
    )  # ENTRADA | SALIDA | AJUSTE | MERMA
    cantidad = Column(Float, nullable=False)
    stock_anterior = Column(Float, nullable=False)
    stock_nuevo = Column(Float, nullable=False)
    motivo = Column(String(150), nullable=True)
    actor_email = Column(String(150), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    item = relationship("ItemInventario", backref="movimientos")


class RecetaProductoInventario(Base):
    """
    Vincula un producto del catálogo con uno o más ítems de inventario
    para descontar stock automáticamente cuando un pedido pasa a ENTREGADO.
    """

    __tablename__ = "recetas_producto_inventario"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    plato_id = Column(
        Integer, ForeignKey("productos_platos.id"), nullable=False, index=True
    )
    item_inventario_id = Column(
        Integer, ForeignKey("items_inventario.id"), nullable=False, index=True
    )
    cantidad_por_unidad = Column(Float, default=1.0, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    plato = relationship("ProductoPlato")
    item_inventario = relationship("ItemInventario")


# =====================================================================
# 5. CLIENTES FRECUENTES Y SESIONES DE CHAT WHATSAPP (CON CONTROL HUMANO)
# =====================================================================
class ClienteFrecuente(Base):
    """Memoria por teléfono de WhatsApp para saludar al cliente y recordar su dirección."""

    __tablename__ = "clientes_frecuentes"
    __table_args__ = (
        Index("ix_cliente_tenant_telefono", "tenant_id", "telefono_whatsapp"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    telefono_whatsapp = Column(String(40), nullable=False, index=True)
    nombre_cliente = Column(String(150), nullable=True)
    ultima_direccion = Column(String(255), nullable=True)
    ultima_zona_envio = Column(String(120), nullable=True)
    ultima_latitud_gps = Column(Float, nullable=True)
    ultima_longitud_gps = Column(Float, nullable=True)
    ultimo_pedido_texto = Column(Text, nullable=True)
    total_pedidos_realizados = Column(Integer, default=0, nullable=False)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    comercio = relationship("TenantComercio", back_populates="clientes_frecuentes")


class SesionChatWhatsApp(Base):
    """
    Representa una conversación activa de un cliente en tu WhatsApp con una sucursal:
    - Incluye el interruptor 'modo_atencion':
      * BOT_ACTIVO: El bot responde automáticamente.
      * HUMANO_ACTIVO: El dueño/cajero tomó el control de este chat desde la web y el bot
        se desactiva ÚNICAMENTE para esta conversación.
    """

    __tablename__ = "sesiones_chat_whatsapp"
    __table_args__ = (
        Index("ix_sesion_telefono_activa", "telefono_cliente", "activa"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    telefono_cliente = Column(String(40), nullable=False, index=True)
    nombre_cliente = Column(String(150), nullable=True)

    # Control de Intervención Humana ("Human Takeover")
    modo_atencion = Column(
        String(30), default="BOT_ACTIVO", nullable=False, index=True
    )  # BOT_ACTIVO | HUMANO_ACTIVO
    tomado_por_usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)
    fecha_toma_control = Column(DateTime(timezone=True), nullable=True)

    # Estado de armado del pedido en el chat
    estado_conversacion = Column(
        String(40), default="EXPLORANDO_MENU", nullable=False
    )  # EXPLORANDO_MENU | CONFIRMANDO_CARRITO | PIDIENDO_DIRECCION | PIDIENDO_PAGO | ESPERANDO_COMPROBANTE | COMPLETADO
    carrito_json = Column(Text, default="[]", nullable=False)
    resumen_ia_chat = Column(
        Text,
        default="Conversación iniciada en WhatsApp.",
        nullable=False,
    )
    como_se_realizo_resumen = Column(
        Text,
        default="Entrada por link directo de WhatsApp (Opción B).",
        nullable=False,
    )

    activa = Column(Boolean, default=True, nullable=False)
    ultimo_mensaje_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    sucursal = relationship("SucursalComercio", back_populates="sesiones_chat")
    mensajes = relationship(
        "MensajeChatWhatsApp",
        back_populates="sesion",
        cascade="all, delete-orphan",
        order_by="MensajeChatWhatsApp.id",
    )
    pedidos = relationship("Pedido", back_populates="sesion_chat")


class MensajeChatWhatsApp(Base):
    """Cada mensaje individual dentro de la conversación de WhatsApp."""

    __tablename__ = "mensajes_chat_whatsapp"

    id = Column(Integer, primary_key=True, index=True)
    sesion_id = Column(
        Integer, ForeignKey("sesiones_chat_whatsapp.id"), nullable=False, index=True
    )
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )

    remitente = Column(
        String(30), nullable=False
    )  # CLIENTE | BOT | HUMANO_COMERCIO
    tipo_contenido = Column(
        String(30), default="TEXTO", nullable=False
    )  # TEXTO | BOTON | UBICACION_GPS | IMAGEN_COMPROBANTE
    contenido = Column(Text, nullable=False)
    latitud_gps = Column(Float, nullable=True)
    longitud_gps = Column(Float, nullable=True)
    archivo_url = Column(String(500), nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    sesion = relationship("SesionChatWhatsApp", back_populates="mensajes")


# =====================================================================
# 6. PEDIDOS, DETALLES CON EXCLUSIÓN DE INGREDIENTES Y ESTADOS
# =====================================================================
class Pedido(Base):
    """
    Pedido guardado desde WhatsApp que se visualiza en el Panel Web del Restaurante:
    - Incluye el resumen del chat con IA, cómo se realizó el pedido, ubicación GPS,
      método de pago (Modo 1 o Modo 2) y estados ('RECIBIDO', 'EN_PROCESO', 'LISTO').
    """

    __tablename__ = "pedidos"

    id = Column(Integer, primary_key=True, index=True)
    folio = Column(String(40), unique=True, nullable=False, index=True)  # Ej. ORD-1001
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    sesion_chat_id = Column(
        Integer, ForeignKey("sesiones_chat_whatsapp.id"), nullable=True, index=True
    )
    cliente_frecuente_id = Column(
        Integer, ForeignKey("clientes_frecuentes.id"), nullable=True
    )

    # Datos del cliente y entrega
    telefono_cliente = Column(String(40), nullable=False, index=True)
    nombre_cliente = Column(String(150), nullable=False)
    tipo_entrega = Column(
        String(30), default="DOMICILIO", nullable=False
    )  # DOMICILIO | RECOGER_LOCAL
    direccion_entrega = Column(String(255), nullable=True)
    zona_envio_nombre = Column(String(120), nullable=True)
    latitud_gps = Column(Float, nullable=True)
    longitud_gps = Column(Float, nullable=True)
    google_maps_link = Column(String(500), nullable=True)

    # Estado operativo controlado por el restaurante en la Web
    # RECIBIDO -> EN_PROCESO -> LISTO (-> ENTREGADO / CANCELADO)
    estado_pedido = Column(
        String(30), default="RECIBIDO", nullable=False, index=True
    )

    # Totales calculados de forma blindada en Python (jamás por la IA libremente)
    subtotal = Column(Float, default=0.0, nullable=False)
    costo_envio = Column(Float, default=0.0, nullable=False)
    total = Column(Float, default=0.0, nullable=False)

    # Información contextual del Chat de WhatsApp
    resumen_chat_ia = Column(Text, nullable=False)
    como_se_realizo_pedido = Column(
        Text,
        default="Pedido tomado en WhatsApp (Híbrido Menú + IA).",
        nullable=False,
    )
    notas_especiales = Column(Text, nullable=True)

    # Información de Pago (Modo 1 activo hoy / Modo 2 listo en estructura)
    metodo_pago = Column(
        String(40), default="EFECTIVO", nullable=False
    )  # EFECTIVO | TRANSFERENCIA | PASARELA_API
    estado_pago = Column(
        String(40), default="PENDIENTE", nullable=False
    )  # PENDIENTE | COMPROBANTE_RECIBIDO | PAGADO | RECHAZADO
    paga_con_monto = Column(Float, nullable=True)  # Ej. Paga con billete de $500
    cambio_a_entregar = Column(Float, nullable=True)  # Ej. Cambio: $280
    comprobante_url = Column(String(500), nullable=True)  # Foto del comprobante en Modo 1

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    sucursal = relationship("SucursalComercio", back_populates="pedidos")
    sesion_chat = relationship("SesionChatWhatsApp", back_populates="pedidos")
    detalles = relationship(
        "DetallePedido", back_populates="pedido", cascade="all, delete-orphan"
    )
    transaccion_pago = relationship(
        "PagoTransaccion",
        back_populates="pedido",
        uselist=False,
        cascade="all, delete-orphan",
    )


class DetallePedido(Base):
    """Renglón de cada plato dentro de un pedido con sus exclusiones ('SIN X') y extras."""

    __tablename__ = "detalles_pedido"

    id = Column(Integer, primary_key=True, index=True)
    pedido_id = Column(
        Integer, ForeignKey("pedidos.id"), nullable=False, index=True
    )
    plato_id = Column(Integer, ForeignKey("productos_platos.id"), nullable=True)

    nombre_plato = Column(String(150), nullable=False)
    cantidad = Column(Integer, default=1, nullable=False)
    precio_unitario_base = Column(Float, nullable=False)

    # Lista JSON de ingredientes base que el cliente pidió quitar: ej. '["Cebolla", "Mayonesa"]'
    sin_ingredientes_json = Column(Text, default="[]", nullable=False)
    # Lista JSON de variantes o extras con costo: ej. '[{"nombre": "Extra Tocino", "precio": 2.0}]'
    extras_json = Column(Text, default="[]", nullable=False)

    precio_extras_unitario = Column(Float, default=0.0, nullable=False)
    subtotal_linea = Column(Float, nullable=False)
    notas_plato = Column(String(255), nullable=True)

    pedido = relationship("Pedido", back_populates="detalles")


# =====================================================================
# 7. MÓDULO DE RESERVACIÓN DE MESAS (ACTIVO CON PASARELA DE PAGOS MODO 2)
# =====================================================================
class MesaZona(Base):
    """Mesas o zonas reservables en una sucursal física."""

    __tablename__ = "mesas_zonas"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    nombre_mesa = Column(String(100), nullable=False)  # Ej. "Mesa 4 - Terraza"
    capacidad_personas = Column(Integer, default=4, nullable=False)
    monto_anticipo_reserva = Column(Float, default=10.0, nullable=False)
    activa = Column(Boolean, default=True, nullable=False)

    sucursal = relationship("SucursalComercio", back_populates="mesas")
    reservaciones = relationship("ReservacionMesa", back_populates="mesa")


class ReservacionMesa(Base):
    """
    Reservación de mesa solicitada por WhatsApp.
    Por regla de negocio definida en el Plan, exige un pago de reserva y se habilita
    cuando se integren los métodos de pago (Modo 2).
    """

    __tablename__ = "reservaciones_mesa"

    id = Column(Integer, primary_key=True, index=True)
    folio_reserva = Column(String(40), unique=True, nullable=False, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    sucursal_id = Column(
        Integer, ForeignKey("sucursales_comercio.id"), nullable=False, index=True
    )
    mesa_id = Column(Integer, ForeignKey("mesas_zonas.id"), nullable=True)
    sesion_chat_id = Column(
        Integer, ForeignKey("sesiones_chat_whatsapp.id"), nullable=True
    )

    nombre_cliente = Column(String(150), nullable=False)
    telefono_cliente = Column(String(40), nullable=False, index=True)
    fecha_hora_reserva = Column(String(50), nullable=False)  # Ej. "2026-11-15 20:00"
    cantidad_personas = Column(Integer, default=2, nullable=False)

    monto_pago_requerido = Column(Float, nullable=False)
    estado_pago = Column(
        String(40), default="PENDIENTE_PAGO_API", nullable=False
    )  # PENDIENTE_PAGO_API | PAGADO | CANCELADO
    estado_reserva = Column(
        String(40), default="ESPERANDO_PAGO", nullable=False
    )  # ESPERANDO_PAGO | CONFIRMADA | COMPLETADA | CANCELADA
    link_pago_api = Column(String(500), nullable=True)
    notas = Column(Text, nullable=True)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    mesa = relationship("MesaZona", back_populates="reservaciones")
    transaccion_pago = relationship(
        "PagoTransaccion",
        back_populates="reservacion",
        uselist=False,
        cascade="all, delete-orphan",
    )


# =====================================================================
# 8. ESTRUCTURA UNIFICADA DE PAGOS (MODO 1 HOY + MODO 2 API FUTURA)
# =====================================================================
class PagoTransaccion(Base):
    """
    Tabla enchufable para pagos de Pedidos y Reservaciones de Mesa:
    - En Modo 1 guarda el detalle de efectivo/cambio o la foto del comprobante de transferencia.
    - En Modo 2 guarda el ID de transacción, el link de cobro y el webhook de Stripe/MercadoPago.
    """

    __tablename__ = "pagos_transacciones"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(
        Integer, ForeignKey("tenants_comercios.id"), nullable=False, index=True
    )
    pedido_id = Column(Integer, ForeignKey("pedidos.id"), nullable=True, index=True)
    reservacion_id = Column(
        Integer, ForeignKey("reservaciones_mesa.id"), nullable=True, index=True
    )

    modo_operacion = Column(
        String(30), default="MODO_1_DIRECTO", nullable=False
    )  # MODO_1_DIRECTO | MODO_2_API
    metodo = Column(
        String(40), default="EFECTIVO", nullable=False
    )  # EFECTIVO | TRANSFERENCIA | STRIPE | MERCADOPAGO
    monto = Column(Float, nullable=False)
    moneda = Column(String(10), default="USD", nullable=False)
    estado = Column(
        String(40), default="PENDIENTE", nullable=False
    )  # PENDIENTE | EN_VERIFICACION | APROBADO | RECHAZADO

    # Campos Modo 1
    paga_con = Column(Float, nullable=True)
    cambio = Column(Float, nullable=True)
    comprobante_imagen_url = Column(String(500), nullable=True)
    validado_por_usuario_id = Column(Integer, ForeignKey("usuarios.id"), nullable=True)

    # Campos Modo 2 (Listos para cuando se integre la API de pagos)
    proveedor_api = Column(String(50), nullable=True)
    referencia_externa_api = Column(String(150), nullable=True, index=True)
    url_link_pago = Column(String(500), nullable=True)
    webhook_payload_json = Column(Text, nullable=True)

    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    actualizado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    pedido = relationship("Pedido", back_populates="transaccion_pago")
    reservacion = relationship("ReservacionMesa", back_populates="transaccion_pago")


# =====================================================================
# 9. AUDITORÍA DE SEGURIDAD INMUTABLE (ISO/IEC 27001 A.12.4)
# =====================================================================
class AuditLog(Base):
    """
    Registro de seguridad de todas las acciones críticas:
    Inicios de sesión, cambios de estado de pedido, toma de control humano de un chat,
    modificación de precios/planes y validaciones de pago.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, nullable=True, index=True)
    usuario_id = Column(Integer, nullable=True, index=True)
    actor_email = Column(String(150), nullable=True)
    accion = Column(String(100), nullable=False, index=True)
    recurso_tipo = Column(String(80), nullable=False)
    recurso_id = Column(String(80), nullable=True)
    ip_origen = Column(String(60), nullable=True)
    detalles_json = Column(Text, default="{}", nullable=False)
    creado_en = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

