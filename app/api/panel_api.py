"""
APIs Operativas del Panel Web SaaS (Fase 2):
1. Rutas para SUPER_ADMIN (Dueño del SaaS):
   - Dashboard global de comercios, planes, ingresos y auditoría ISO 27001
   - Alta de nuevos restaurantes/comercios con sucursal y usuario dueño
   - Cambio de estado de suscripción (ACTIVO / SUSPENDIDO) y planes
2. Rutas para COMERCIO_ADMIN y SUCURSAL_STAFF (Dueños de Restaurantes y Cocina):
   - Dashboard operativo con aislamiento Multi-Tenant estricto
   - Cambio de estados del pedido: RECIBIDO -> EN_PROCESO -> LISTO (-> ENTREGADO)
     con notificación automática al chat de WhatsApp del cliente
   - Interruptor de Intervención Humana ('Tomar control del chat') que pausa el bot
     ÚNICAMENTE para esa conversación y permite enviar mensajes manuales desde la web
   - Gestión de Menú con Ingredientes Removibles ($0), Variantes, Extras y botón 'Agotado hoy'
   - Gestión Multi-Sucursal, Tiempo Estimado de Entrega y Códigos QR de WhatsApp (Opción B)
"""
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.audit import record_audit_log
from app.core.config import settings
from app.core.database import get_db
from app.core.demo_seeds import normalize_giro_code, populate_tenant_demo_data
from app.core.inventory_sync import deduct_inventory_for_delivered_order
from app.core.realtime import publish_tenant_event, subscribe_tenant_events
from app.services.whatsapp_provider import send_whatsapp_text
from app.core.deps import (
    get_current_user,
    require_comercio_admin,
    require_super_admin,
    verify_tenant_isolation,
)
from app.core.security import (
    build_whatsapp_branch_deep_link,
    decode_jwt_token,
    generate_qr_base64_data_uri,
    hash_password,
)
from app.models.saas_models import (
    AsignacionDelivery,
    AuditLog,
    CategoriaMenu,
    ComercioLogo,
    ConfigDeliveryComercio,
    DetallePedido,
    IngredienteOpcionPlato,
    ItemInventario,
    MovimientoInventario,
    MensajeChatWhatsApp,
    MesaZona,
    ModuloComercio,
    Pedido,
    PlanSaaS,
    ProductoImagen,
    ProductoPlato,
    ReservacionMesa,
    SesionChatWhatsApp,
    StockSucursal,
    SucursalComercio,
    SuscripcionFacturaSaaS,
    TenantComercio,
    Usuario,
    ZonaEnvioSucursal,
)

router = APIRouter(prefix="/api/v1/panel", tags=["Panel Web SaaS y Restaurantes"])


# =====================================================================
# ESQUEMAS DE ENTRADA (PYDANTIC V2)
# =====================================================================
class CrearComercioRequest(BaseModel):
    nombre_comercial: str
    slug: str
    giro: str = "Restaurante"
    plan_codigo: str = "PLAN_PRO"
    moneda: str = "NIO"
    simbolo_moneda: str = "C$"
    telefono_propietario: Optional[str] = None
    email_dueno: EmailStr
    nombre_dueno: str
    password_dueno: str
    nombre_sucursal_principal: str = "Sucursal Principal"
    direccion_sucursal: str = "Dirección por definir"
    costo_envio_base: float = 50.00
    poblar_datos_demo: bool = False


class ConfigurarComercioRequest(BaseModel):
    moneda: Optional[str] = None
    simbolo_moneda: Optional[str] = None
    nombre_comercial: Optional[str] = None
    giro: Optional[str] = None
    telefono_propietario: Optional[str] = None
    datos_bancarios_texto: Optional[str] = None
    acepta_efectivo: Optional[bool] = None
    acepta_transferencia: Optional[bool] = None


class CrearStaffComercioRequest(BaseModel):
    nombre_completo: str
    email: EmailStr
    password: str
    sucursal_id: Optional[int] = None


class ActualizarStaffComercioRequest(BaseModel):
    activo: Optional[bool] = None
    nueva_password: Optional[str] = None
    sucursal_id: Optional[int] = None


class ActualizarPrecioPlatoRequest(BaseModel):
    precio_base: float


class ActualizarPrecioIngredienteRequest(BaseModel):
    precio_adicional: float


class CrearItemInventarioRequest(BaseModel):
    nombre_insumo: str
    categoria: str = "General"
    unidad_medida: str = "unidades"
    stock_actual: float = 0.0
    stock_minimo: float = 5.0
    costo_unitario: float = 0.0
    proveedor: Optional[str] = None
    notas: Optional[str] = None
    sucursal_id: Optional[int] = None


class AjustarStockRequest(BaseModel):
    tipo: str  # SUMAR | RESTAR | FIJAR
    cantidad: float
    motivo: Optional[str] = "Ajuste manual"


class CambiarEstadoSuscripcionRequest(BaseModel):
    estado_suscripcion: str  # ACTIVO | TRIAL | POR_VENCER | SUSPENDIDO
    plan_id: Optional[int] = None


class EditarComercioAdminRequest(BaseModel):
    nombre_comercial: Optional[str] = None
    giro: Optional[str] = None
    email_contacto: Optional[EmailStr] = None
    telefono_propietario: Optional[str] = None
    moneda: Optional[str] = None
    simbolo_moneda: Optional[str] = None
    plan_id: Optional[int] = None
    estado_suscripcion: Optional[str] = None
    extender_dias: Optional[int] = None
    modo_pago_api_activo: Optional[bool] = None
    reservaciones_habilitadas: Optional[bool] = None
    inventario_habilitado: Optional[bool] = None
    activo: Optional[bool] = None


class PlanSaaSInput(BaseModel):
    codigo: str
    nombre: str
    descripcion: str = ""
    precio_mensual_usd: float = 29.0
    limite_pedidos_mes: int = 200
    porcentaje_comision_pedido: float = 0.0
    tarifa_fija_pedido_extra_usd: float = 0.20
    max_sucursales: int = 1
    incluye_intervencion_humana: bool = True
    incluye_impresion_termica: bool = True
    incluye_reservacion_mesas: bool = True
    activo: bool = True


class CrearUsuarioComercioAdminRequest(BaseModel):
    nombre_completo: str
    email: EmailStr
    password: str
    rol: str = "SUCURSAL_STAFF"  # COMERCIO_ADMIN | SUCURSAL_STAFF
    sucursal_id: Optional[int] = None


class ActualizarUsuarioAdminRequest(BaseModel):
    activo: Optional[bool] = None
    nueva_password: Optional[str] = None
    rol: Optional[str] = None
    sucursal_id: Optional[int] = None


class GenerarFacturaSaaSRequest(BaseModel):
    periodo_mes: Optional[str] = None
    estado_pago: str = "PENDIENTE"  # PENDIENTE | PAGADO | VENCIDO
    referencia_pago: Optional[str] = None


class ActualizarFacturaSaaSRequest(BaseModel):
    estado_pago: str  # PENDIENTE | PAGADO | VENCIDO
    referencia_pago: Optional[str] = None
    reactivar_comercio: bool = True


class CambiarEstadoPedidoRequest(BaseModel):
    nuevo_estado: str  # RECIBIDO | EN_PROCESO | LISTO | ENTREGADO | CANCELADO


class ControlChatRequest(BaseModel):
    modo_atencion: str  # BOT_ACTIVO | HUMANO_ACTIVO


class MensajeHumanoRequest(BaseModel):
    mensaje: str


class IngredienteInput(BaseModel):
    nombre: str
    tipo: str = "INGREDIENTE_BASE"  # INGREDIENTE_BASE | VARIANTE | EXTRA
    precio_adicional: float = 0.0


class CrearPlatoRequest(BaseModel):
    categoria_nombre: str = "Especialidades"
    nombre: str
    descripcion: str = ""
    precio_base: float
    tiempo_preparacion_min: int = 15
    ingredientes_removibles: List[str] = []
    extras_con_costo: List[IngredienteInput] = []


classCrearSucursalBase = BaseModel


class CrearSucursalRequest(BaseModel):
    nombre_sucursal: str
    codigo_whatsapp: str
    direccion_fisica: str
    horario_apertura: str = "09:00"
    horario_cierre: str = "23:00"
    tiempo_estimado_entrega_min: int = 35
    costo_envio_base: float = 2.50
    ancho_ticket_termico: str = "80mm"


class ActualizarSucursalOperacionRequest(BaseModel):
    abierto_ahora: bool
    tiempo_estimado_entrega_min: int
    ancho_ticket_termico: str = "80mm"


# =====================================================================
# 1. ENDPOINTS PARA SUPER_ADMIN (DUEÑO DEL SAAS)
# =====================================================================
def _is_inventario_habilitado(db: Session, tenant_id: int) -> bool:
    """Devuelve True solo si el SuperAdmin activó explícitamente el módulo de inventario para este comercio."""
    mod = db.query(ModuloComercio).filter(ModuloComercio.tenant_id == tenant_id).first()
    return bool(mod and mod.inventario_habilitado)


@router.get("/superadmin/overview")
def superadmin_overview(
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Devuelve toda la información global del SaaS agrupada para el Panel Maestro."""
    planes = db.query(PlanSaaS).order_by(PlanSaaS.precio_mensual_usd).all()
    comercios = db.query(TenantComercio).order_by(TenantComercio.id.desc()).all()
    total_pedidos = db.query(Pedido).count()
    total_usuarios = db.query(Usuario).count()
    facturas = (
        db.query(SuscripcionFacturaSaaS)
        .order_by(SuscripcionFacturaSaaS.id.desc())
        .limit(100)
        .all()
    )
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(60).all()

    ingresos_mensuales_estimados = sum(
        c.plan.precio_mensual_usd
        for c in comercios
        if c.estado_suscripcion == "ACTIVO" and c.activo and c.plan
    )
    facturas_pendientes_list = [f for f in facturas if f.estado_pago in ("PENDIENTE", "VENCIDO")]
    facturas_pagadas_list = [f for f in facturas if f.estado_pago == "PAGADO"]

    comercios_por_plan = {}
    for c in comercios:
        if c.plan_id:
            comercios_por_plan[c.plan_id] = comercios_por_plan.get(c.plan_id, 0) + 1

    comercios_data = []
    for c in comercios:
        sucs = []
        for s in c.sucursales:
            deep_link = build_whatsapp_branch_deep_link(
                settings.WHATSAPP_BOT_PUBLIC_NUMBER,
                s.codigo_whatsapp,
                c.nombre_comercial,
            )
            sucs.append(
                {
                    "id": s.id,
                    "nombre_sucursal": s.nombre_sucursal,
                    "codigo_whatsapp": s.codigo_whatsapp,
                    "abierto_ahora": s.abierto_ahora,
                    "tiempo_estimado_min": s.tiempo_estimado_entrega_min,
                    "ancho_ticket": s.ancho_ticket_termico,
                    "deep_link_whatsapp": deep_link,
                    "qr_data_uri": generate_qr_base64_data_uri(deep_link),
                }
            )
        pend_count = sum(
            1
            for f in c.facturas_saas
            if f.estado_pago in ("PENDIENTE", "VENCIDO")
        )
        comercios_data.append(
            {
                "id": c.id,
                "nombre_comercial": c.nombre_comercial,
                "slug": c.slug,
                "giro": c.giro,
                "email_contacto": c.email_contacto,
                "telefono_propietario": c.telefono_propietario,
                "moneda": c.moneda,
                "simbolo_moneda": c.simbolo_moneda,
                "estado_suscripcion": c.estado_suscripcion,
                "activo": c.activo,
                "fecha_vencimiento": c.fecha_vencimiento_suscripcion.strftime("%Y-%m-%d")
                if c.fecha_vencimiento_suscripcion
                else None,
                "pedidos_mes_actual": c.pedidos_mes_actual,
                "modo_pago_api_activo": c.modo_pago_api_activo,
                "reservaciones_habilitadas": c.reservaciones_habilitadas,
                "inventario_habilitado": _is_inventario_habilitado(db, c.id),
                "usuarios_count": len(c.usuarios),
                "facturas_pendientes": pend_count,
                "plan": {
                    "id": c.plan.id if c.plan else None,
                    "codigo": c.plan.codigo if c.plan else None,
                    "nombre": c.plan.nombre if c.plan else "Sin Plan",
                    "precio_mensual_usd": c.plan.precio_mensual_usd if c.plan else 0,
                    "limite_pedidos_mes": c.plan.limite_pedidos_mes if c.plan else 0,
                    "porcentaje_comision": c.plan.porcentaje_comision_pedido if c.plan else 0,
                },
                "sucursales": sucs,
            }
        )

    facturas_data = [
        {
            "id": f.id,
            "tenant_id": f.tenant_id,
            "comercio_nombre": f.comercio.nombre_comercial if f.comercio else f"Comercio #{f.tenant_id}",
            "periodo_mes": f.periodo_mes,
            "pedidos_contabilizados": f.pedidos_contabilizados,
            "monto_mensualidad_fija": f.monto_mensualidad_fija,
            "monto_comision_pedidos": f.monto_comision_pedidos,
            "total_a_pagar": f.total_a_pagar,
            "estado_pago": f.estado_pago,
            "referencia_pago": f.referencia_pago,
            "creado_en": f.creado_en.strftime("%Y-%m-%d %H:%M") if f.creado_en else "",
        }
        for f in facturas
    ]

    logs_data = []
    for l in logs:
        try:
            det_parsed = json.loads(l.detalles_json or "{}")
        except Exception:
            det_parsed = {}
        logs_data.append(
            {
                "id": l.id,
                "accion": l.accion,
                "actor_email": l.actor_email,
                "recurso_tipo": l.recurso_tipo,
                "recurso_id": l.recurso_id,
                "tenant_id": l.tenant_id,
                "ip_origen": l.ip_origen,
                "detalles": det_parsed,
                "creado_en": l.creado_en.strftime("%Y-%m-%d %H:%M:%S")
                if l.creado_en
                else "",
            }
        )

    return {
        "metricas_saas": {
            "total_comercios": len(comercios),
            "comercios_activos": sum(1 for c in comercios if c.estado_suscripcion == "ACTIVO"),
            "comercios_trial": sum(1 for c in comercios if c.estado_suscripcion == "TRIAL"),
            "comercios_por_vencer": sum(1 for c in comercios if c.estado_suscripcion == "POR_VENCER"),
            "comercios_suspendidos": sum(1 for c in comercios if c.estado_suscripcion == "SUSPENDIDO"),
            "ingresos_fijos_mensuales_usd": round(ingresos_mensuales_estimados, 2),
            "facturas_pendientes_count": len(facturas_pendientes_list),
            "facturas_pendientes_usd": round(sum(f.total_a_pagar for f in facturas_pendientes_list), 2),
            "facturas_pagadas_usd": round(sum(f.total_a_pagar for f in facturas_pagadas_list), 2),
            "total_pedidos_procesados": total_pedidos,
            "total_usuarios": total_usuarios,
            "whatsapp_bot_number": settings.WHATSAPP_BOT_PUBLIC_NUMBER,
            "whatsapp_provider": settings.WHATSAPP_PROVIDER,
            "payment_mode": settings.PAYMENT_OPERATION_MODE,
            "ai_priority": settings.AI_PROVIDER_PRIORITY,
            "database_schema": settings.DB_SCHEMA if settings.is_postgres else "sqlite_local",
        },
        "planes": [
            {
                "id": p.id,
                "codigo": p.codigo,
                "nombre": p.nombre,
                "descripcion": p.descripcion,
                "precio_mensual_usd": p.precio_mensual_usd,
                "limite_pedidos_mes": p.limite_pedidos_mes,
                "porcentaje_comision_pedido": p.porcentaje_comision_pedido,
                "tarifa_fija_pedido_extra_usd": p.tarifa_fija_pedido_extra_usd,
                "max_sucursales": p.max_sucursales,
                "incluye_intervencion_humana": p.incluye_intervencion_humana,
                "incluye_impresion_termica": p.incluye_impresion_termica,
                "incluye_reservacion_mesas": p.incluye_reservacion_mesas,
                "activo": p.activo,
                "comercios_suscritos": comercios_por_plan.get(p.id, 0),
            }
            for p in planes
        ],
        "comercios": comercios_data,
        "facturas": facturas_data,
        "audit_logs": logs_data,
    }


@router.post("/superadmin/comercios")
def crear_nuevo_comercio(
    payload: CrearComercioRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Da de alta un nuevo Restaurante cliente de tu SaaS con su sucursal y cuenta de dueño."""
    slug_clean = payload.slug.lower().strip().replace(" ", "-")
    if db.query(TenantComercio).filter(TenantComercio.slug == slug_clean).first():
        raise HTTPException(
            status_code=400, detail="Ya existe un comercio con ese código/slug."
        )
    if (
        db.query(Usuario)
        .filter(Usuario.email == payload.email_dueno.lower().strip())
        .first()
    ):
        raise HTTPException(
            status_code=400, detail="Ese correo de dueño ya está registrado."
        )

    plan = (
        db.query(PlanSaaS).filter(PlanSaaS.codigo == payload.plan_codigo).first()
        or db.query(PlanSaaS).first()
    )

    giro_norm = normalize_giro_code(payload.giro)
    es_citas = giro_norm == "SERVICIOS_CITAS"
    usa_mesas = giro_norm in ("RESTAURANTE", "CAFETERIA", "SERVICIOS_CITAS")

    nuevo_comercio = TenantComercio(
        nombre_comercial=payload.nombre_comercial.strip(),
        slug=slug_clean,
        giro=giro_norm,
        telefono_propietario=payload.telefono_propietario,
        email_contacto=payload.email_dueno.lower().strip(),
        plan_id=plan.id,
        estado_suscripcion="ACTIVO",
        fecha_vencimiento_suscripcion=datetime.now(timezone.utc) + timedelta(days=30),
        moneda=payload.moneda,
        simbolo_moneda=payload.simbolo_moneda,
        tiene_mesas_fisicas=usa_mesas,
        reservaciones_habilitadas=es_citas,
    )
    db.add(nuevo_comercio)
    db.commit()
    db.refresh(nuevo_comercio)

    codigo_wa = f"{slug_clean.capitalize()}-Principal"
    sucursal = SucursalComercio(
        tenant_id=nuevo_comercio.id,
        nombre_sucursal=payload.nombre_sucursal_principal,
        codigo_whatsapp=codigo_wa,
        es_principal=True,
        direccion_fisica=payload.direccion_sucursal,
        costo_envio_base=0.0 if es_citas else payload.costo_envio_base,
        permite_domicilio=not es_citas,
    )
    db.add(sucursal)
    db.flush()

    if es_citas:
        # Auto-configuración para 1 sola persona que administra y atiende (puede agregar más después)
        db.add(
            ConfigDeliveryComercio(
                tenant_id=nuevo_comercio.id,
                modo_delivery="DESACTIVADO",
                requiere_pin_entrega=False,
                notificar_whatsapp_repartidor=False,
            )
        )
        db.add(
            MesaZona(
                tenant_id=nuevo_comercio.id,
                sucursal_id=sucursal.id,
                nombre_mesa=f"Atención Principal — {payload.nombre_dueno.strip()}"[:80],
                capacidad_personas=1,
                monto_anticipo_reserva=0.0,
                activa=True,
            )
        )

    dueno = Usuario(
        tenant_id=nuevo_comercio.id,
        sucursal_id=None,
        nombre_completo=payload.nombre_dueno,
        email=payload.email_dueno.lower().strip(),
        password_hash=hash_password(payload.password_dueno),
        rol="COMERCIO_ADMIN",
        activo=True,
    )
    db.add(dueno)
    db.commit()

    demo_stats = {"productos_creados": 0, "insumos_creados": 0, "pedidos_creados": 0}
    if payload.poblar_datos_demo:
        demo_stats = populate_tenant_demo_data(db, nuevo_comercio, giro_norm)
    else:
        cat_inicial_map = {
            "SERVICIOS_CITAS": "Consultas y Servicios",
            "FARMACIA": "Analgésicos y Salud",
            "TIENDA": "Catálogo Principal",
            "SUPERMERCADO": "Abarrotes y Canasta Básica",
            "CAFETERIA": "Cafés y Repostería",
            "RESTAURANTE": "Menú Principal",
        }
        cat_inicial = CategoriaMenu(
            tenant_id=nuevo_comercio.id,
            nombre=cat_inicial_map.get(giro_norm, "Catálogo Principal"),
            orden=1,
        )
        db.add(cat_inicial)
        db.commit()

    record_audit_log(
        db=db,
        accion="CREAR_COMERCIO_SAAS",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(nuevo_comercio.id),
        usuario_id=current_user.id,
        tenant_id=nuevo_comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"nombre": nuevo_comercio.nombre_comercial, "slug": slug_clean, "plan": plan.codigo},
    )

    return {
        "status": "ok",
        "comercio_id": nuevo_comercio.id,
        "codigo_whatsapp": codigo_wa,
        "demo_stats": demo_stats,
    }


@router.post("/superadmin/comercios/{tenant_id}/poblar-demo")
def poblar_demo_comercio_admin(
    tenant_id: int,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Puebla un comercio existente con catalogo, inventario y pedidos de muestra segun su giro."""
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")
    giro_norm = normalize_giro_code(comercio.giro)
    comercio.giro = giro_norm
    stats = populate_tenant_demo_data(db, comercio, giro_norm)
    record_audit_log(
        db=db,
        accion="POBLAR_DEMO_COMERCIO",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(comercio.id),
        usuario_id=current_user.id,
        tenant_id=comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"giro": giro_norm, **stats},
    )
    return {"status": "ok", "giro": giro_norm, **stats}


@router.put("/superadmin/comercios/{tenant_id}")
def editar_comercio_admin(
    tenant_id: int,
    payload: EditarComercioAdminRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Edita datos generales, plan, estado de suscripción, vencimiento y módulos de un comercio."""
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    if payload.nombre_comercial is not None:
        comercio.nombre_comercial = payload.nombre_comercial.strip()
    if payload.giro is not None:
        comercio.giro = payload.giro.strip()
    if payload.email_contacto is not None:
        comercio.email_contacto = payload.email_contacto.lower().strip()
    if payload.telefono_propietario is not None:
        comercio.telefono_propietario = payload.telefono_propietario.strip()
    if payload.moneda is not None:
        comercio.moneda = payload.moneda.strip().upper()
    if payload.simbolo_moneda is not None:
        comercio.simbolo_moneda = payload.simbolo_moneda.strip()
    if payload.plan_id is not None:
        plan = db.query(PlanSaaS).filter(PlanSaaS.id == payload.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Plan seleccionado no existe.")
        comercio.plan_id = plan.id
    if payload.estado_suscripcion is not None:
        comercio.estado_suscripcion = payload.estado_suscripcion
        comercio.activo = payload.estado_suscripcion != "SUSPENDIDO"
    if payload.extender_dias and payload.extender_dias > 0:
        base_dt = comercio.fecha_vencimiento_suscripcion or datetime.now(timezone.utc)
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        comercio.fecha_vencimiento_suscripcion = max(base_dt, now_dt) + timedelta(days=payload.extender_dias)
    if payload.modo_pago_api_activo is not None:
        comercio.modo_pago_api_activo = payload.modo_pago_api_activo
    if payload.reservaciones_habilitadas is not None:
        comercio.reservaciones_habilitadas = payload.reservaciones_habilitadas
    if payload.inventario_habilitado is not None:
        mod = db.query(ModuloComercio).filter(ModuloComercio.tenant_id == comercio.id).first()
        if not mod:
            mod = ModuloComercio(
                tenant_id=comercio.id,
                inventario_habilitado=bool(payload.inventario_habilitado),
            )
            db.add(mod)
        else:
            mod.inventario_habilitado = bool(payload.inventario_habilitado)
            mod.actualizado_en = datetime.now(timezone.utc)
    if payload.activo is not None:
        comercio.activo = payload.activo

    db.commit()

    record_audit_log(
        db=db,
        accion="EDITAR_COMERCIO_ADMIN",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(comercio.id),
        usuario_id=current_user.id,
        tenant_id=comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={
            "estado_suscripcion": comercio.estado_suscripcion,
            "plan_id": comercio.plan_id,
            "modo_pago_api_activo": comercio.modo_pago_api_activo,
        },
    )
    return {"status": "ok", "comercio_id": comercio.id}


@router.patch("/superadmin/comercios/{tenant_id}/suscripcion")
def cambiar_estado_suscripcion_comercio(
    tenant_id: int,
    payload: CambiarEstadoSuscripcionRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Permite activar o suspender un comercio (corte automático aislado por tenant_id)."""
    comercio = (
        db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    )
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    comercio.estado_suscripcion = payload.estado_suscripcion
    comercio.activo = payload.estado_suscripcion != "SUSPENDIDO"
    if payload.plan_id:
        comercio.plan_id = payload.plan_id
    db.commit()

    record_audit_log(
        db=db,
        accion="CAMBIO_SUSCRIPCION_COMERCIO",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(comercio.id),
        usuario_id=current_user.id,
        tenant_id=comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"nuevo_estado": payload.estado_suscripcion},
    )
    return {"status": "ok", "estado_suscripcion": comercio.estado_suscripcion}


@router.get("/superadmin/comercios/{tenant_id}/usuarios")
def listar_usuarios_comercio_admin(
    tenant_id: int,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Lista los usuarios registrados de un restaurante y sus sucursales para soporte técnico."""
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    usuarios = db.query(Usuario).filter(Usuario.tenant_id == tenant_id).order_by(Usuario.id).all()
    sucursales = db.query(SucursalComercio).filter(SucursalComercio.tenant_id == tenant_id).all()
    suc_map = {s.id: s.nombre_sucursal for s in sucursales}

    return {
        "comercio_id": tenant_id,
        "comercio_nombre": comercio.nombre_comercial,
        "sucursales": [{"id": s.id, "nombre_sucursal": s.nombre_sucursal} for s in sucursales],
        "usuarios": [
            {
                "id": u.id,
                "nombre_completo": u.nombre_completo,
                "email": u.email,
                "rol": u.rol,
                "sucursal_id": u.sucursal_id,
                "sucursal_nombre": suc_map.get(u.sucursal_id, "Todas las sucursales"),
                "activo": u.activo,
                "is_2fa_enabled": u.is_2fa_enabled,
            }
            for u in usuarios
        ],
    }


@router.post("/superadmin/comercios/{tenant_id}/usuarios")
def crear_usuario_comercio_admin(
    tenant_id: int,
    payload: CrearUsuarioComercioAdminRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Permite al SuperAdmin crear cuentas adicionales (Dueño o Cocina/Sucursal) para un comercio."""
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    email_clean = payload.email.lower().strip()
    if db.query(Usuario).filter(Usuario.email == email_clean).first():
        raise HTTPException(status_code=400, detail="Ese correo ya está registrado.")

    rol_validado = payload.rol if payload.rol in ("COMERCIO_ADMIN", "SUCURSAL_STAFF") else "SUCURSAL_STAFF"
    nuevo_u = Usuario(
        tenant_id=tenant_id,
        sucursal_id=payload.sucursal_id if rol_validado == "SUCURSAL_STAFF" else None,
        nombre_completo=payload.nombre_completo.strip(),
        email=email_clean,
        password_hash=hash_password(payload.password),
        rol=rol_validado,
        activo=True,
    )
    db.add(nuevo_u)
    db.commit()
    db.refresh(nuevo_u)

    record_audit_log(
        db=db,
        accion="CREAR_USUARIO_COMERCIO",
        recurso_tipo="USUARIO",
        recurso_id=str(nuevo_u.id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"email": email_clean, "rol": rol_validado},
    )
    return {"status": "ok", "usuario_id": nuevo_u.id}


@router.patch("/superadmin/usuarios/{usuario_id}")
def actualizar_usuario_admin(
    usuario_id: int,
    payload: ActualizarUsuarioAdminRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Permite activar/bloquear o restablecer la contraseña de un usuario del comercio."""
    u = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    if not u or u.rol == "SUPER_ADMIN":
        raise HTTPException(status_code=404, detail="Usuario de comercio no encontrado.")

    if payload.activo is not None:
        u.activo = payload.activo
    if payload.nueva_password:
        u.password_hash = hash_password(payload.nueva_password)
    if payload.rol and payload.rol in ("COMERCIO_ADMIN", "SUCURSAL_STAFF"):
        u.rol = payload.rol
    if payload.sucursal_id is not None:
        u.sucursal_id = payload.sucursal_id or None

    db.commit()

    record_audit_log(
        db=db,
        accion="ACTUALIZAR_USUARIO_ADMIN",
        recurso_tipo="USUARIO",
        recurso_id=str(u.id),
        usuario_id=current_user.id,
        tenant_id=u.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"activo": u.activo, "password_reset": bool(payload.nueva_password)},
    )
    return {"status": "ok", "usuario_id": u.id, "activo": u.activo}


@router.post("/superadmin/planes")
def crear_plan_saas(
    payload: PlanSaaSInput,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Crea un nuevo Plan SaaS en la base de datos."""
    cod = payload.codigo.upper().strip().replace(" ", "_")
    if db.query(PlanSaaS).filter(PlanSaaS.codigo == cod).first():
        raise HTTPException(status_code=400, detail="Ya existe un plan con ese código.")

    plan = PlanSaaS(
        codigo=cod,
        nombre=payload.nombre.strip(),
        descripcion=(payload.descripcion or "").strip(),
        precio_mensual_usd=max(0.0, float(payload.precio_mensual_usd)),
        limite_pedidos_mes=max(0, int(payload.limite_pedidos_mes)),
        porcentaje_comision_pedido=max(0.0, float(payload.porcentaje_comision_pedido)),
        tarifa_fija_pedido_extra_usd=max(0.0, float(payload.tarifa_fija_pedido_extra_usd)),
        max_sucursales=max(1, int(payload.max_sucursales)),
        incluye_intervencion_humana=payload.incluye_intervencion_humana,
        incluye_impresion_termica=payload.incluye_impresion_termica,
        incluye_reservacion_mesas=payload.incluye_reservacion_mesas,
        activo=payload.activo,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    record_audit_log(
        db=db,
        accion="CREAR_PLAN_SAAS",
        recurso_tipo="PLAN_SAAS",
        recurso_id=str(plan.id),
        usuario_id=current_user.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"codigo": plan.codigo, "precio_usd": plan.precio_mensual_usd},
    )
    return {"status": "ok", "plan_id": plan.id}


@router.put("/superadmin/planes/{plan_id}")
def editar_plan_saas(
    plan_id: int,
    payload: PlanSaaSInput,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Actualiza tarifas, límites y capacidades de un Plan SaaS existente."""
    plan = db.query(PlanSaaS).filter(PlanSaaS.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan SaaS no encontrado.")

    plan.nombre = payload.nombre.strip()
    plan.descripcion = (payload.descripcion or "").strip()
    plan.precio_mensual_usd = max(0.0, float(payload.precio_mensual_usd))
    plan.limite_pedidos_mes = max(0, int(payload.limite_pedidos_mes))
    plan.porcentaje_comision_pedido = max(0.0, float(payload.porcentaje_comision_pedido))
    plan.tarifa_fija_pedido_extra_usd = max(0.0, float(payload.tarifa_fija_pedido_extra_usd))
    plan.max_sucursales = max(1, int(payload.max_sucursales))
    plan.incluye_intervencion_humana = payload.incluye_intervencion_humana
    plan.incluye_impresion_termica = payload.incluye_impresion_termica
    plan.incluye_reservacion_mesas = payload.incluye_reservacion_mesas
    plan.activo = payload.activo
    db.commit()

    record_audit_log(
        db=db,
        accion="EDITAR_PLAN_SAAS",
        recurso_tipo="PLAN_SAAS",
        recurso_id=str(plan.id),
        usuario_id=current_user.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"codigo": plan.codigo, "precio_usd": plan.precio_mensual_usd},
    )
    return {"status": "ok", "plan_id": plan.id}


@router.post("/superadmin/comercios/{tenant_id}/facturas")
def generar_factura_saas(
    tenant_id: int,
    payload: GenerarFacturaSaaSRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Genera una factura mensual de cobro SaaS en suscripciones_facturas_saas."""
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    periodo = payload.periodo_mes or datetime.now(timezone.utc).strftime("%Y-%m")
    plan = comercio.plan
    fijo = round(plan.precio_mensual_usd if plan else 0.0, 2)
    pedidos_cnt = int(comercio.pedidos_mes_actual or 0)

    comision = 0.0
    if plan:
        if plan.porcentaje_comision_pedido > 0:
            # Estimación de comisión sobre pedidos contabilizados del mes
            comision = round(pedidos_cnt * 0.50 * (plan.porcentaje_comision_pedido / 100.0), 2)
        elif plan.limite_pedidos_mes > 0 and pedidos_cnt > plan.limite_pedidos_mes:
            excedente = pedidos_cnt - plan.limite_pedidos_mes
            comision = round(excedente * plan.tarifa_fija_pedido_extra_usd, 2)

    total = round(fijo + comision, 2)
    estado = payload.estado_pago if payload.estado_pago in ("PENDIENTE", "PAGADO", "VENCIDO") else "PENDIENTE"

    factura = SuscripcionFacturaSaaS(
        tenant_id=comercio.id,
        periodo_mes=periodo,
        pedidos_contabilizados=pedidos_cnt,
        monto_mensualidad_fija=fijo,
        monto_comision_pedidos=comision,
        total_a_pagar=total,
        estado_pago=estado,
        referencia_pago=payload.referencia_pago,
    )
    db.add(factura)
    db.commit()
    db.refresh(factura)

    record_audit_log(
        db=db,
        accion="GENERAR_FACTURA_SAAS",
        recurso_tipo="SUSCRIPCION_FACTURA_SAAS",
        recurso_id=str(factura.id),
        usuario_id=current_user.id,
        tenant_id=comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"periodo": periodo, "total_usd": total, "estado": estado},
    )
    return {"status": "ok", "factura_id": factura.id, "total_a_pagar": total}


@router.patch("/superadmin/facturas/{factura_id}")
def actualizar_factura_saas(
    factura_id: int,
    payload: ActualizarFacturaSaaSRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Marca una factura SaaS como PAGADO / PENDIENTE / VENCIDO y opcionalmente reactiva +30 días al comercio."""
    factura = (
        db.query(SuscripcionFacturaSaaS)
        .filter(SuscripcionFacturaSaaS.id == factura_id)
        .first()
    )
    if not factura:
        raise HTTPException(status_code=404, detail="Factura no encontrada.")

    estado = payload.estado_pago if payload.estado_pago in ("PENDIENTE", "PAGADO", "VENCIDO") else "PENDIENTE"
    factura.estado_pago = estado
    if payload.referencia_pago is not None:
        factura.referencia_pago = payload.referencia_pago.strip()

    if estado == "PAGADO" and payload.reactivar_comercio and factura.comercio:
        comercio = factura.comercio
        comercio.estado_suscripcion = "ACTIVO"
        comercio.activo = True
        now_dt = datetime.now(timezone.utc)
        base_dt = comercio.fecha_vencimiento_suscripcion or now_dt
        if base_dt.tzinfo is None:
            base_dt = base_dt.replace(tzinfo=timezone.utc)
        comercio.fecha_vencimiento_suscripcion = max(base_dt, now_dt) + timedelta(days=30)

    db.commit()

    record_audit_log(
        db=db,
        accion="ACTUALIZAR_FACTURA_SAAS",
        recurso_tipo="SUSCRIPCION_FACTURA_SAAS",
        recurso_id=str(factura.id),
        usuario_id=current_user.id,
        tenant_id=factura.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"estado_pago": estado, "referencia": factura.referencia_pago},
    )
    return {"status": "ok", "factura_id": factura.id, "estado_pago": factura.estado_pago}


# =====================================================================
# 2. ENDPOINTS PARA EL PANEL DEL RESTAURANTE / COMERCIO
# =====================================================================
def _verify_staff_branch_scope(current_user: Usuario, target_sucursal_id: Optional[int]) -> None:
    """Si el usuario es SUCURSAL_STAFF asignado a una sucursal fija, impide operar sobre otra sucursal."""
    if (
        current_user.rol == "SUCURSAL_STAFF"
        and current_user.sucursal_id
        and target_sucursal_id is not None
        and target_sucursal_id != current_user.sucursal_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Aislamiento por sucursal: Tu usuario solo tiene acceso a su sucursal asignada.",
        )


@router.get("/comercio/{tenant_id}/dashboard")
def comercio_dashboard(
    tenant_id: int,
    sucursal_id: Optional[int] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Devuelve todos los pedidos, resúmenes de chat con IA, menú con ingredientes,
    sucursales con QR y reservaciones de mesa de un restaurante (con aislamiento Multi-Tenant).
    """
    verify_tenant_isolation(current_user, tenant_id)

    comercio = (
        db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    )
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    # Si el usuario es personal de una sola sucursal, forzar filtro a su sucursal
    if current_user.rol == "SUCURSAL_STAFF" and current_user.sucursal_id:
        sucursal_id = current_user.sucursal_id

    pedidos_query = db.query(Pedido).filter(Pedido.tenant_id == tenant_id)
    if sucursal_id:
        pedidos_query = pedidos_query.filter(Pedido.sucursal_id == sucursal_id)
    pedidos = pedidos_query.order_by(Pedido.id.desc()).all()

    asig_rows = (
        db.query(AsignacionDelivery)
        .filter(AsignacionDelivery.tenant_id == tenant_id)
        .all()
    )
    asig_by_ped = {a.pedido_id: a for a in asig_rows}

    from app.models.saas_models import BeneficioCliente, ClienteFrecuente, FidelidadCliente
    from app.services.fidelidad_service import get_or_create_config_fidelidad

    vip_tels = set()
    for tel_r, eg in (
        db.query(ClienteFrecuente.telefono_whatsapp, BeneficioCliente.envio_gratis)
        .join(BeneficioCliente, BeneficioCliente.cliente_id == ClienteFrecuente.id)
        .filter(ClienteFrecuente.tenant_id == tenant_id, BeneficioCliente.envio_gratis.is_(True))
        .all()
    ):
        if tel_r:
            vip_tels.add(tel_r)
    for tel_r, ev, dp in (
        db.query(ClienteFrecuente.telefono_whatsapp, FidelidadCliente.es_vip, FidelidadCliente.descuento_pct)
        .join(FidelidadCliente, FidelidadCliente.cliente_id == ClienteFrecuente.id)
        .filter(ClienteFrecuente.tenant_id == tenant_id)
        .all()
    ):
        if tel_r and (ev or float(dp or 0.0) > 0):
            vip_tels.add(tel_r)

    fid_cfg = get_or_create_config_fidelidad(db, tenant_id)

    pedidos_data = []
    for ped in pedidos:
        sesion = ped.sesion_chat
        mensajes_chat = []
        if sesion:
            for m in sesion.mensajes:
                mensajes_chat.append(
                    {
                        "id": m.id,
                        "remitente": m.remitente,
                        "tipo_contenido": m.tipo_contenido,
                        "contenido": m.contenido,
                        "hora": m.creado_en.strftime("%H:%M") if m.creado_en else "",
                    }
                )

        items_data = []
        for det in ped.detalles:
            try:
                sin_list = json.loads(det.sin_ingredientes_json or "[]")
            except Exception:
                sin_list = []
            try:
                extras_list = json.loads(det.extras_json or "[]")
            except Exception:
                extras_list = []

            items_data.append(
                {
                    "id": det.id,
                    "nombre_plato": det.nombre_plato,
                    "cantidad": det.cantidad,
                    "precio_unitario_base": det.precio_unitario_base,
                    "sin_ingredientes": sin_list,
                    "extras": extras_list,
                    "subtotal_linea": det.subtotal_linea,
                    "notas_plato": det.notas_plato,
                }
            )

        pedidos_data.append(
            {
                "id": ped.id,
                "folio": ped.folio,
                "sucursal_id": ped.sucursal_id,
                "sucursal_nombre": ped.sucursal.nombre_sucursal
                if ped.sucursal
                else "Principal",
                "telefono_cliente": ped.telefono_cliente,
                "nombre_cliente": ped.nombre_cliente,
                "cliente_es_vip": bool(ped.telefono_cliente and ped.telefono_cliente in vip_tels),
                "tipo_entrega": ped.tipo_entrega,
                "direccion_entrega": ped.direccion_entrega,
                "zona_envio_nombre": ped.zona_envio_nombre,
                "google_maps_link": ped.google_maps_link,
                "estado_pedido": ped.estado_pedido,
                "subtotal": ped.subtotal,
                "costo_envio": ped.costo_envio,
                "total": ped.total,
                "metodo_pago": ped.metodo_pago,
                "estado_pago": ped.estado_pago,
                "paga_con_monto": ped.paga_con_monto,
                "cambio_a_entregar": ped.cambio_a_entregar,
                "comprobante_url": ped.comprobante_url,
                "resumen_chat_ia": ped.resumen_chat_ia,
                "como_se_realizo_pedido": ped.como_se_realizo_pedido,
                "notas_especiales": ped.notas_especiales,
                "delivery_info": (
                    {
                        "estado_delivery": asig_by_ped[ped.id].estado_delivery,
                        "repartidor_nombre": asig_by_ped[ped.id].repartidor_nombre,
                        "repartidor_telefono": asig_by_ped[ped.id].repartidor_telefono,
                        "pin_entrega": asig_by_ped[ped.id].pin_entrega,
                    }
                    if ped.id in asig_by_ped
                    else None
                ),
                "creado_en": ped.creado_en.strftime("%d/%m/%Y %H:%M")
                if ped.creado_en
                else "",
                "sesion_chat_id": sesion.id if sesion else None,
                "modo_atencion_chat": sesion.modo_atencion
                if sesion
                else "BOT_ACTIVO",
                "mensajes_chat": mensajes_chat,
                "items": items_data,
            }
        )

    # Platos con sus ingredientes removibles y extras
    platos = (
        db.query(ProductoPlato)
        .filter(ProductoPlato.tenant_id == tenant_id)
        .order_by(ProductoPlato.id.desc())
        .all()
    )
    agotados_rows = (
        db.query(StockSucursal)
        .filter(
            StockSucursal.tenant_id == tenant_id,
            StockSucursal.plato_id.isnot(None),
            StockSucursal.agotado_hoy.is_(True),
        )
        .all()
    )
    agotados_por_plato: Dict[int, List[int]] = {}
    for row in agotados_rows:
        agotados_por_plato.setdefault(row.plato_id, []).append(row.sucursal_id)

    img_rows = (
        db.query(ProductoImagen.plato_id, ProductoImagen.actualizado_en)
        .filter(ProductoImagen.tenant_id == tenant_id)
        .all()
    )
    img_map = {
        pid: f"/api/v1/public/img/{pid}?v={int(ts.timestamp()) if ts else 1}"
        for pid, ts in img_rows
    }

    platos_data = []
    for pl in platos:
        platos_data.append(
            {
                "id": pl.id,
                "nombre": pl.nombre,
                "categoria": pl.categoria.nombre if pl.categoria else "General",
                "descripcion": pl.descripcion,
                "precio_base": pl.precio_base,
                "tiempo_preparacion_min": pl.tiempo_preparacion_min,
                "disponible_global": pl.disponible_global,
                "imagen_url": img_map.get(pl.id),
                "agotado_sucursal_ids": agotados_por_plato.get(pl.id, []),
                "ingredientes_removibles": [
                    {"id": ing.id, "nombre": ing.nombre, "disponible": ing.disponible_global}
                    for ing in pl.ingredientes_opciones
                    if ing.tipo == "INGREDIENTE_BASE"
                ],
                "extras_y_variantes": [
                    {
                        "id": ing.id,
                        "nombre": ing.nombre,
                        "tipo": ing.tipo,
                        "precio_adicional": ing.precio_adicional,
                        "disponible": ing.disponible_global,
                    }
                    for ing in pl.ingredientes_opciones
                    if ing.tipo in ("EXTRA", "VARIANTE")
                ],
            }
        )

    # Sucursales con sus links y códigos QR para WhatsApp (Opción B)
    sucursales_visibles = comercio.sucursales
    if current_user.rol == "SUCURSAL_STAFF" and current_user.sucursal_id:
        sucursales_visibles = [s for s in comercio.sucursales if s.id == current_user.sucursal_id]

    sucursales_data = []
    for s in sucursales_visibles:
        deep_link = build_whatsapp_branch_deep_link(
            settings.WHATSAPP_BOT_PUBLIC_NUMBER,
            s.codigo_whatsapp,
            comercio.nombre_comercial,
        )
        sucursales_data.append(
            {
                "id": s.id,
                "nombre_sucursal": s.nombre_sucursal,
                "codigo_whatsapp": s.codigo_whatsapp,
                "direccion_fisica": s.direccion_fisica,
                "horario_apertura": s.horario_apertura,
                "horario_cierre": s.horario_cierre,
                "abierto_ahora": s.abierto_ahora,
                "tiempo_estimado_entrega_min": s.tiempo_estimado_entrega_min,
                "costo_envio_base": s.costo_envio_base,
                "pedido_minimo_domicilio": s.pedido_minimo_domicilio,
                "telefono_sucursal": s.telefono_sucursal,
                "permite_domicilio": s.permite_domicilio,
                "permite_recoger_local": s.permite_recoger_local,
                "ancho_ticket_termico": s.ancho_ticket_termico,
                "deep_link_whatsapp": deep_link,
                "qr_data_uri": generate_qr_base64_data_uri(deep_link),
                "zonas_envio": [
                    {
                        "id": z.id,
                        "nombre_zona": z.nombre_zona,
                        "costo_envio": z.costo_envio,
                        "tiempo_extra_min": z.tiempo_extra_min,
                    }
                    for z in s.zonas_envio
                ],
                "mesas": [
                    {
                        "id": m.id,
                        "nombre_mesa": m.nombre_mesa,
                        "capacidad_personas": m.capacidad_personas,
                        "monto_anticipo_reserva": m.monto_anticipo_reserva,
                    }
                    for m in s.mesas
                ],
            }
        )

    reservaciones = (
        db.query(ReservacionMesa)
        .filter(ReservacionMesa.tenant_id == tenant_id)
        .all()
    )

    from app.api.delivery_api import get_or_create_delivery_config

    deliv_cfg = get_or_create_delivery_config(db, tenant_id)
    logo_row = (
        db.query(ComercioLogo.actualizado_en)
        .filter(ComercioLogo.tenant_id == tenant_id)
        .first()
    )
    logo_url = (
        f"/api/v1/public/logo/{tenant_id}?v={int(logo_row[0].timestamp()) if logo_row[0] else 1}"
        if logo_row
        else None
    )

    return {
        "comercio": {
            "id": comercio.id,
            "nombre_comercial": comercio.nombre_comercial,
            "logo_url": logo_url,
            "delivery_config": {
                "modo_delivery": deliv_cfg.modo_delivery,
                "requiere_pin_entrega": deliv_cfg.requiere_pin_entrega,
                "notificar_whatsapp_repartidor": deliv_cfg.notificar_whatsapp_repartidor,
                "permite_repartidor_propio": bool(__import__("app.api.delivery_api", fromlist=["get_or_create_permiso_delivery"]).get_or_create_permiso_delivery(db, comercio.id).permite_repartidor_propio),
                "max_repartidores_propios": int(__import__("app.api.delivery_api", fromlist=["get_or_create_permiso_delivery"]).get_or_create_permiso_delivery(db, comercio.id).max_repartidores_propios or 1),
            },
            "fidelidad_config": {
                "activo": bool(fid_cfg.activo),
                "sellos_meta": int(fid_cfg.sellos_meta or 5),
                "monto_minimo_sello": float(fid_cfg.monto_minimo_sello or 150.0),
                "premio_descuento_pct": float(fid_cfg.premio_descuento_pct or 15.0),
            },
            "slug": comercio.slug,
            "giro": comercio.giro,
            "estado_suscripcion": comercio.estado_suscripcion,
            "moneda": comercio.moneda,
            "simbolo_moneda": comercio.simbolo_moneda,
            "datos_bancarios_texto": comercio.datos_bancarios_texto,
            "modo_pago_api_activo": comercio.modo_pago_api_activo,
            "reservaciones_habilitadas": comercio.reservaciones_habilitadas,
            "inventario_habilitado": _is_inventario_habilitado(db, comercio.id),
            "plan_nombre": comercio.plan.nombre if comercio.plan else "Plan Pro",
            "pedidos_mes_actual": comercio.pedidos_mes_actual,
            "limite_pedidos_mes": comercio.plan.limite_pedidos_mes if comercio.plan else 800,
            "bot_config": __import__(
                "app.services.bot_config_service", fromlist=["get_bot_config_dict"]
            ).get_bot_config_dict(db, comercio.id),
        },
        "sucursales": sucursales_data,
        "pedidos": pedidos_data,
        "platos": platos_data,
        "reservaciones_mesa": [
            {
                "id": r.id,
                "folio": r.folio_reserva,
                "sucursal_id": r.sucursal_id,
                "mesa_nombre": r.mesa.nombre_mesa if r.mesa else "Sin asignar",
                "nombre_cliente": r.nombre_cliente,
                "telefono_cliente": r.telefono_cliente,
                "fecha_hora": r.fecha_hora_reserva,
                "personas": r.cantidad_personas,
                "monto_anticipo": r.monto_pago_requerido,
                "estado_reserva": r.estado_reserva,
            }
            for r in reservaciones
        ],
    }


class BotConfigUpdateRequest(BaseModel):
    bot_activo: Optional[bool] = None
    nombre_asistente: Optional[str] = None
    tono_bot: Optional[str] = None
    saludo_personalizado: Optional[str] = None
    mensaje_cerrado: Optional[str] = None
    permitir_fuera_horario: Optional[bool] = None
    promo_del_dia: Optional[str] = None
    ubicacion_texto: Optional[str] = None
    ubicacion_maps_url: Optional[str] = None
    politicas_faq: Optional[str] = None
    pedido_minimo: Optional[float] = None
    palabras_escalamiento: Optional[str] = None
    reglas_extra: Optional[str] = None


@router.put("/comercio/{tenant_id}/bot-config")
def actualizar_bot_config_comercio(
    tenant_id: int,
    payload: BotConfigUpdateRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Guarda la configuración personalizada del Bot de WhatsApp del comercio."""
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("COMERCIO_ADMIN", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo el administrador del comercio puede configurar el bot.")
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")
    from app.services.bot_config_service import save_bot_config

    saved = save_bot_config(db, tenant_id, payload.model_dump())
    return {"status": "ok", "bot_config": saved}


class CrearReservaManualRequest(BaseModel):
    sucursal_id: int
    mesa_id: Optional[int] = None
    nombre_cliente: str
    telefono_cliente: str = ""
    fecha_hora_reserva: str
    cantidad_personas: int = 1
    notas: Optional[str] = None


@router.post("/comercio/{tenant_id}/reservaciones/manual")
def crear_reservacion_manual(
    tenant_id: int,
    payload: CrearReservaManualRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permite a recepción/barbero/admin agendar una cita o reserva presencial/telefónica en la agenda."""
    verify_tenant_isolation(current_user, tenant_id)
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    from app.services.whatsapp_bot_service import _has_reservation_conflict

    fh = (payload.fecha_hora_reserva or "").strip()[:48]
    if not fh:
        raise HTTPException(status_code=400, detail="Indica fecha y hora de la cita.")
    if payload.mesa_id and _has_reservation_conflict(db, payload.sucursal_id, payload.mesa_id, fh):
        raise HTTPException(status_code=409, detail=f"Ese horario ({fh}) ya está ocupado en ese espacio/consultorio.")

    mesa = db.query(MesaZona).filter(MesaZona.id == payload.mesa_id).first() if payload.mesa_id else None
    anticipo = float(mesa.monto_anticipo_reserva or 0.0) if mesa else 0.0
    es_cita = normalize_giro_code(comercio.giro) == "SERVICIOS_CITAS"
    folio = f"{'CIT' if es_cita else 'RES'}-{datetime.now(timezone.utc).strftime('%H%M')}-{random.randint(100, 999)}"
    tel_clean = "".join(ch for ch in (payload.telefono_cliente or "") if ch.isdigit())

    res = ReservacionMesa(
        folio_reserva=folio,
        tenant_id=tenant_id,
        sucursal_id=payload.sucursal_id,
        mesa_id=payload.mesa_id,
        nombre_cliente=payload.nombre_cliente.strip()[:80],
        telefono_cliente=tel_clean or "00000000",
        fecha_hora_reserva=fh,
        cantidad_personas=max(1, int(payload.cantidad_personas or 1)),
        monto_pago_requerido=round(anticipo, 2),
        estado_pago="PAGADO" if anticipo <= 0 else "PENDIENTE_PAGO_API",
        estado_reserva="CONFIRMADA",
        notas=(payload.notas or "").strip() or None,
    )
    db.add(res)
    db.commit()
    db.refresh(res)
    return {"status": "ok", "id": res.id, "folio": res.folio_reserva}


@router.post("/comercio/reservaciones/{reserva_id}/recordatorio")
def enviar_recordatorio_cita_whatsapp(
    reserva_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Envía un recordatorio inmediato por WhatsApp al paciente/cliente sobre su cita o reservación."""
    res = db.query(ReservacionMesa).filter(ReservacionMesa.id == reserva_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Cita o reservación no encontrada.")
    verify_tenant_isolation(current_user, res.tenant_id)
    comercio = db.query(TenantComercio).filter(TenantComercio.id == res.tenant_id).first()
    mesa_nom = res.mesa.nombre_mesa if res.mesa else "Consultorio / Espacio asignado"
    es_cita = normalize_giro_code(comercio.giro if comercio else "") == "SERVICIOS_CITAS"
    titulo = "Recordatorio de Cita" if es_cita else "Recordatorio de Reservación"
    msg = (
        f"📅 *{titulo} — {comercio.nombre_comercial if comercio else 'Tu Comercio'}*\n"
        f"Hola {res.nombre_cliente}, te recordamos tu {'cita' if es_cita else 'reservación'} *{res.folio_reserva}*:\n"
        f"• Horario: *{res.fecha_hora_reserva}*\n"
        f"• Lugar: *{mesa_nom}*\n"
        f"¡Te esperamos!"
    )
    if res.telefono_cliente:
        send_whatsapp_text(res.telefono_cliente, msg)
    return {"status": "ok", "folio": res.folio_reserva, "enviado_a": res.telefono_cliente}


@router.patch("/comercio/pedidos/{pedido_id}/estado")
def actualizar_estado_pedido(
    pedido_id: int,
    payload: CambiarEstadoPedidoRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cambia el estado del pedido ('RECIBIDO' -> 'EN_PROCESO' -> 'LISTO' -> 'ENTREGADO')
    y envía el aviso automático al chat de WhatsApp del cliente.
    """
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")

    verify_tenant_isolation(current_user, pedido.tenant_id)
    _verify_staff_branch_scope(current_user, pedido.sucursal_id)

    estados_validos = {"RECIBIDO", "EN_PROCESO", "LISTO", "ENTREGADO", "CANCELADO"}
    nuevo = payload.nuevo_estado.upper().strip()
    if nuevo not in estados_validos:
        raise HTTPException(status_code=400, detail="Estado de pedido no válido.")

    estado_anterior = pedido.estado_pedido
    pedido.estado_pedido = nuevo
    pedido.actualizado_en = datetime.now(timezone.utc)

    # Notificación automática al chat de WhatsApp del cliente adaptada al giro
    com_obj = pedido.sucursal.comercio if pedido.sucursal and pedido.sucursal.comercio else None
    nombre_com = com_obj.nombre_comercial if com_obj else "el comercio"
    from app.core.demo_seeds import get_giro_notification_copy

    gc = get_giro_notification_copy(com_obj.giro if com_obj else "RESTAURANTE")
    noun = gc["item_noun"]
    mensajes_estado = {
        "RECIBIDO": f"Hola {pedido.nombre_cliente}, tu {noun} #{pedido.folio} {gc['recibido']} en {nombre_com}.",
        "EN_PROCESO": f"Tu {noun} #{pedido.folio} {gc['en_proceso']} en {nombre_com}.",
        "LISTO": f"Tu {noun} #{pedido.folio} {gc['listo_dom'] if pedido.tipo_entrega == 'DOMICILIO' else gc['listo_loc']}.",
        "ENTREGADO": f"Tu {noun} #{pedido.folio} {gc['entregado']}",
        "CANCELADO": f"Tu {noun} #{pedido.folio} {gc['cancelado']}. Si tienes dudas, escríbenos por este chat.",
    }
    texto_aviso = mensajes_estado.get(nuevo, f"Actualización de tu {noun} #{pedido.folio}: {nuevo}")

    if pedido.sesion_chat_id:
        db.add(
            MensajeChatWhatsApp(
                sesion_id=pedido.sesion_chat_id,
                tenant_id=pedido.tenant_id,
                remitente="BOT",
                tipo_contenido="TEXTO",
                contenido=texto_aviso,
            )
        )
        if nuevo in ("ENTREGADO", "CANCELADO"):
            sesion_cierre = (
                db.query(SesionChatWhatsApp)
                .filter(SesionChatWhatsApp.id == pedido.sesion_chat_id)
                .first()
            )
            if sesion_cierre:
                sesion_cierre.estado_conversacion = "COMPLETADO"
                sesion_cierre.activa = False

    if nuevo == "LISTO" and pedido.tipo_entrega == "DOMICILIO":
        try:
            from app.api.delivery_api import notify_repartidores_pedido_listo

            notify_repartidores_pedido_listo(db, pedido)
        except Exception:
            pass

    stock_descontado = []
    if nuevo == "ENTREGADO":
        stock_descontado = deduct_inventory_for_delivered_order(
            db, pedido, current_user.email
        )
        try:
            from app.services.fidelidad_service import otorgar_sello_por_entrega

            otorgar_sello_por_entrega(db, pedido)
        except Exception:
            pass

    db.commit()

    # Envío saliente real por Evolution API / Meta Cloud API (no bloquea si está en modo simulado)
    if texto_aviso and pedido.telefono_cliente:
        send_whatsapp_text(pedido.telefono_cliente, texto_aviso)

    publish_tenant_event(
        pedido.tenant_id,
        "ESTADO_PEDIDO",
        {"pedido_id": pedido.id, "folio": pedido.folio, "estado": pedido.estado_pedido},
    )

    record_audit_log(
        db=db,
        accion="CAMBIO_ESTADO_PEDIDO",
        recurso_tipo="PEDIDO",
        recurso_id=pedido.folio,
        usuario_id=current_user.id,
        tenant_id=pedido.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={
            "de": estado_anterior,
            "a": nuevo,
            "notificacion_wa": texto_aviso,
            "stock_descontado": stock_descontado,
        },
    )

    return {
        "status": "ok",
        "folio": pedido.folio,
        "estado_pedido": pedido.estado_pedido,
        "mensaje_enviado_whatsapp": texto_aviso,
        "stock_descontado": stock_descontado,
    }


@router.get("/comercio/{tenant_id}/eventos")
async def stream_eventos_comercio(
    tenant_id: int,
    request: Request,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Stream SSE en tiempo real para actualizar el tablero Kanban y chat al instante
    cuando ocurre un nuevo pedido, cambio de estado o mensaje de WhatsApp.
    """
    raw_tok = token or request.cookies.get("saas_access_token")
    if not raw_tok:
        auth_h = request.headers.get("authorization") or ""
        if auth_h.lower().startswith("bearer "):
            raw_tok = auth_h.split(" ", 1)[1].strip()
    payload = decode_jwt_token(raw_tok) if raw_tok else None
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="No autenticado para eventos en tiempo real.")

    user = (
        db.query(Usuario)
        .filter(Usuario.id == int(payload.get("sub")), Usuario.activo.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="Usuario inválido.")
    verify_tenant_isolation(user, tenant_id)

    return StreamingResponse(
        subscribe_tenant_events(tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/comercio/chats/{sesion_id}/control")
def cambiar_control_conversacion(
    sesion_id: int,
    payload: ControlChatRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Interruptor 'Tomar control del chat':
    Pausa el bot ÚNICAMENTE para esta conversación (HUMANO_ACTIVO) o lo reactiva (BOT_ACTIVO).
    """
    sesion = (
        db.query(SesionChatWhatsApp)
        .filter(SesionChatWhatsApp.id == sesion_id)
        .first()
    )
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")

    verify_tenant_isolation(current_user, sesion.tenant_id)
    _verify_staff_branch_scope(current_user, sesion.sucursal_id)

    modo = payload.modo_atencion.upper().strip()
    if modo not in ("BOT_ACTIVO", "HUMANO_ACTIVO"):
        raise HTTPException(status_code=400, detail="Modo inválido.")

    sesion.modo_atencion = modo
    if modo == "HUMANO_ACTIVO":
        sesion.tomado_por_usuario_id = current_user.id
        sesion.fecha_toma_control = datetime.now(timezone.utc)
        aviso_sistema = (
            f"👨‍💼 [{current_user.nombre_completo} tomó el control manual de esta conversación. "
            "El bot automático está pausado únicamente para este chat.]"
        )
    else:
        sesion.tomado_por_usuario_id = None
        aviso_sistema = "🤖 [El Bot automático ha sido reactivado para esta conversación.]"

    db.add(
        MensajeChatWhatsApp(
            sesion_id=sesion.id,
            tenant_id=sesion.tenant_id,
            remitente="HUMANO_COMERCIO",
            tipo_contenido="TEXTO",
            contenido=aviso_sistema,
        )
    )
    db.commit()

    record_audit_log(
        db=db,
        accion=f"CHAT_{modo}",
        recurso_tipo="SESION_CHAT_WA",
        recurso_id=str(sesion.id),
        usuario_id=current_user.id,
        tenant_id=sesion.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"telefono_cliente": sesion.telefono_cliente, "modo": modo},
    )

    return {"status": "ok", "sesion_id": sesion.id, "modo_atencion": sesion.modo_atencion}


@router.post("/comercio/chats/{sesion_id}/mensaje-humano")
def enviar_mensaje_humano_al_chat(
    sesion_id: int,
    payload: MensajeHumanoRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Envía un mensaje escrito por el dueño/cajero desde el Panel Web hacia el WhatsApp del cliente."""
    sesion = (
        db.query(SesionChatWhatsApp)
        .filter(SesionChatWhatsApp.id == sesion_id)
        .first()
    )
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesión de chat no encontrada.")

    verify_tenant_isolation(current_user, sesion.tenant_id)
    _verify_staff_branch_scope(current_user, sesion.sucursal_id)

    texto = payload.mensaje.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío.")

    # Asegurar que al responder manualmente quede en modo HUMANO_ACTIVO
    sesion.modo_atencion = "HUMANO_ACTIVO"
    sesion.ultimo_mensaje_en = datetime.now(timezone.utc)

    nuevo_msg = MensajeChatWhatsApp(
        sesion_id=sesion.id,
        tenant_id=sesion.tenant_id,
        remitente="HUMANO_COMERCIO",
        tipo_contenido="TEXTO",
        contenido=texto,
    )
    db.add(nuevo_msg)
    db.commit()
    db.refresh(nuevo_msg)

    # Envío saliente real al cliente por WhatsApp si el proveedor está configurado
    send_whatsapp_text(sesion.telefono_cliente, texto)
    publish_tenant_event(
        sesion.tenant_id,
        "NUEVO_MENSAJE",
        {"sesion_id": sesion.id},
    )

    return {
        "status": "ok",
        "modo_atencion": sesion.modo_atencion,
        "mensaje": {
            "id": nuevo_msg.id,
            "remitente": nuevo_msg.remitente,
            "contenido": nuevo_msg.contenido,
            "hora": nuevo_msg.creado_en.strftime("%H:%M"),
        },
    }


@router.post("/comercio/{tenant_id}/platos")
def crear_plato_con_ingredientes(
    tenant_id: int,
    payload: CrearPlatoRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite al restaurante registrar un plato con su lista de ingredientes removibles ($0) y extras."""
    verify_tenant_isolation(current_user, tenant_id)

    cat = (
        db.query(CategoriaMenu)
        .filter(
            CategoriaMenu.tenant_id == tenant_id,
            CategoriaMenu.nombre == payload.categoria_nombre.strip(),
        )
        .first()
    )
    if not cat:
        cat = CategoriaMenu(
            tenant_id=tenant_id, nombre=payload.categoria_nombre.strip(), orden=1
        )
        db.add(cat)
        db.commit()
        db.refresh(cat)

    plato = ProductoPlato(
        tenant_id=tenant_id,
        categoria_id=cat.id,
        nombre=payload.nombre.strip(),
        descripcion=payload.descripcion.strip(),
        precio_base=payload.precio_base,
        tiempo_preparacion_min=payload.tiempo_preparacion_min,
        disponible_global=True,
    )
    db.add(plato)
    db.commit()
    db.refresh(plato)

    for ing_nombre in payload.ingredientes_removibles:
        if ing_nombre.strip():
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=tenant_id,
                    plato_id=plato.id,
                    nombre=ing_nombre.strip(),
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                )
            )

    for extra in payload.extras_con_costo:
        if extra.nombre.strip():
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=tenant_id,
                    plato_id=plato.id,
                    nombre=extra.nombre.strip(),
                    tipo=extra.tipo,
                    precio_adicional=extra.precio_adicional,
                    es_removible=False,
                )
            )

    db.commit()

    record_audit_log(
        db=db,
        accion="CREAR_PLATO_MENU",
        recurso_tipo="PRODUCTO_PLATO",
        recurso_id=str(plato.id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"plato": plato.nombre, "precio_base": plato.precio_base},
    )

    return {"status": "ok", "plato_id": plato.id}


@router.patch("/comercio/platos/{plato_id}/toggle-disponibilidad")
def toggle_disponibilidad_plato(
    plato_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Botón rápido de 'Agotado hoy' en 1 clic para que el bot de WhatsApp deje de ofrecerlo al instante."""
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Plato no encontrado.")

    verify_tenant_isolation(current_user, plato.tenant_id)
    plato.disponible_global = not plato.disponible_global
    db.commit()
    return {
        "status": "ok",
        "plato_id": plato.id,
        "disponible_global": plato.disponible_global,
    }


@router.put("/comercio/platos/{plato_id}")
def editar_plato_existente(
    plato_id: int,
    payload: CrearPlatoRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Actualiza el nombre, precio base, descripción, ingredientes removibles ($0) y extras de un plato existente."""
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Plato no encontrado.")

    verify_tenant_isolation(current_user, plato.tenant_id)

    cat = (
        db.query(CategoriaMenu)
        .filter(
            CategoriaMenu.tenant_id == plato.tenant_id,
            CategoriaMenu.nombre == payload.categoria_nombre.strip(),
        )
        .first()
    )
    if not cat:
        cat = CategoriaMenu(
            tenant_id=plato.tenant_id,
            nombre=payload.categoria_nombre.strip(),
            orden=1,
        )
        db.add(cat)
        db.commit()
        db.refresh(cat)

    plato.categoria_id = cat.id
    plato.nombre = payload.nombre.strip()
    plato.descripcion = payload.descripcion.strip()
    plato.precio_base = payload.precio_base
    plato.tiempo_preparacion_min = payload.tiempo_preparacion_min

    # Reemplazar lista de ingredientes base removibles y extras con los nuevos datos editados
    db.query(IngredienteOpcionPlato).filter(
        IngredienteOpcionPlato.plato_id == plato.id
    ).delete()

    for ing_nombre in payload.ingredientes_removibles:
        if ing_nombre.strip():
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=plato.tenant_id,
                    plato_id=plato.id,
                    nombre=ing_nombre.strip(),
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                )
            )

    for extra in payload.extras_con_costo:
        if extra.nombre.strip():
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=plato.tenant_id,
                    plato_id=plato.id,
                    nombre=extra.nombre.strip(),
                    tipo=extra.tipo,
                    precio_adicional=extra.precio_adicional,
                    es_removible=False,
                )
            )

    db.commit()

    record_audit_log(
        db=db,
        accion="EDITAR_PLATO_MENU",
        recurso_tipo="PRODUCTO_PLATO",
        recurso_id=str(plato.id),
        usuario_id=current_user.id,
        tenant_id=plato.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"plato": plato.nombre, "nuevo_precio_base": plato.precio_base},
    )

    return {"status": "ok", "plato_id": plato.id}


@router.delete("/comercio/platos/{plato_id}")
def eliminar_plato_existente(
    plato_id: int,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Elimina un plato y sus ingredientes del catálogo del restaurante."""
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Plato no encontrado.")

    verify_tenant_isolation(current_user, plato.tenant_id)
    nombre_borrado = plato.nombre
    tenant_id = plato.tenant_id

    db.delete(plato)
    db.commit()

    record_audit_log(
        db=db,
        accion="ELIMINAR_PLATO_MENU",
        recurso_tipo="PRODUCTO_PLATO",
        recurso_id=str(plato_id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"plato_eliminado": nombre_borrado},
    )
    return {"status": "ok", "deleted_id": plato_id}


@router.patch("/comercio/ingredientes/{ingrediente_id}/toggle-disponibilidad")
def toggle_disponibilidad_ingrediente(
    ingrediente_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permite marcar como 'Agotado hoy' un ingrediente o extra individual con 1 clic."""
    ing = (
        db.query(IngredienteOpcionPlato)
        .filter(IngredienteOpcionPlato.id == ingrediente_id)
        .first()
    )
    if not ing:
        raise HTTPException(status_code=404, detail="Ingrediente no encontrado.")

    verify_tenant_isolation(current_user, ing.tenant_id)
    ing.disponible_global = not ing.disponible_global
    db.commit()
    return {
        "status": "ok",
        "ingrediente_id": ing.id,
        "nombre": ing.nombre,
        "disponible_global": ing.disponible_global,
    }


@router.patch("/comercio/platos/{plato_id}/precio")
def actualizar_precio_plato(
    plato_id: int,
    payload: ActualizarPrecioPlatoRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite editar rápidamente el precio base de un plato existente."""
    plato = db.query(ProductoPlato).filter(ProductoPlato.id == plato_id).first()
    if not plato:
        raise HTTPException(status_code=404, detail="Plato no encontrado.")

    verify_tenant_isolation(current_user, plato.tenant_id)
    if payload.precio_base < 0:
        raise HTTPException(status_code=400, detail="El precio no puede ser negativo.")

    precio_anterior = plato.precio_base
    plato.precio_base = round(payload.precio_base, 2)
    db.commit()

    record_audit_log(
        db=db,
        accion="CAMBIO_PRECIO_PLATO",
        recurso_tipo="PRODUCTO_PLATO",
        recurso_id=str(plato.id),
        usuario_id=current_user.id,
        tenant_id=plato.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"plato": plato.nombre, "anterior": precio_anterior, "nuevo": plato.precio_base},
    )

    return {"status": "ok", "plato_id": plato.id, "precio_base": plato.precio_base}


@router.patch("/comercio/ingredientes/{ingrediente_id}/precio")
def actualizar_precio_ingrediente(
    ingrediente_id: int,
    payload: ActualizarPrecioIngredienteRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite editar rápidamente el precio adicional de un extra o variante."""
    ing = (
        db.query(IngredienteOpcionPlato)
        .filter(IngredienteOpcionPlato.id == ingrediente_id)
        .first()
    )
    if not ing:
        raise HTTPException(status_code=404, detail="Ingrediente u opción no encontrado.")

    verify_tenant_isolation(current_user, ing.tenant_id)
    if payload.precio_adicional < 0:
        raise HTTPException(status_code=400, detail="El precio adicional no puede ser negativo.")

    precio_anterior = ing.precio_adicional
    ing.precio_adicional = round(payload.precio_adicional, 2)
    db.commit()

    record_audit_log(
        db=db,
        accion="CAMBIO_PRECIO_EXTRA",
        recurso_tipo="INGREDIENTE_OPCION",
        recurso_id=str(ing.id),
        usuario_id=current_user.id,
        tenant_id=ing.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"extra": ing.nombre, "anterior": precio_anterior, "nuevo": ing.precio_adicional},
    )

    return {"status": "ok", "ingrediente_id": ing.id, "precio_adicional": ing.precio_adicional}


@router.patch("/comercio/{tenant_id}/configuracion")
def actualizar_configuracion_comercio(
    tenant_id: int,
    payload: ConfigurarComercioRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite editar la moneda (C$, $, etc.), nombre comercial, giro, datos bancarios y formas de pago del comercio."""
    verify_tenant_isolation(current_user, tenant_id)

    comercio = (
        db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    )
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    cambios = {}
    if payload.moneda is not None and payload.moneda.strip():
        cambios["moneda"] = f"{comercio.moneda} -> {payload.moneda.strip().upper()}"
        comercio.moneda = payload.moneda.strip().upper()
    if payload.simbolo_moneda is not None and payload.simbolo_moneda.strip():
        cambios["simbolo_moneda"] = f"{comercio.simbolo_moneda} -> {payload.simbolo_moneda.strip()}"
        comercio.simbolo_moneda = payload.simbolo_moneda.strip()
    if payload.nombre_comercial is not None and payload.nombre_comercial.strip():
        cambios["nombre_comercial"] = f"{comercio.nombre_comercial} -> {payload.nombre_comercial.strip()}"
        comercio.nombre_comercial = payload.nombre_comercial.strip()
    if payload.giro is not None and payload.giro.strip():
        cambios["giro"] = f"{comercio.giro} -> {payload.giro.strip()}"
        comercio.giro = payload.giro.strip()
    if payload.telefono_propietario is not None:
        comercio.telefono_propietario = payload.telefono_propietario.strip()
    if payload.datos_bancarios_texto is not None:
        comercio.datos_bancarios_texto = payload.datos_bancarios_texto.strip()
    if payload.acepta_efectivo is not None:
        comercio.acepta_efectivo = payload.acepta_efectivo
    if payload.acepta_transferencia is not None:
        comercio.acepta_transferencia = payload.acepta_transferencia

    db.commit()
    db.refresh(comercio)

    record_audit_log(
        db=db,
        accion="CONFIGURACION_COMERCIO",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(comercio.id),
        usuario_id=current_user.id,
        tenant_id=comercio.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles=cambios,
    )

    return {
        "status": "ok",
        "comercio": {
            "id": comercio.id,
            "moneda": comercio.moneda,
            "simbolo_moneda": comercio.simbolo_moneda,
            "nombre_comercial": comercio.nombre_comercial,
            "giro": comercio.giro,
            "datos_bancarios_texto": comercio.datos_bancarios_texto,
            "acepta_efectivo": comercio.acepta_efectivo,
            "acepta_transferencia": comercio.acepta_transferencia,
        },
    }


@router.post("/comercio/{tenant_id}/sucursales")
def crear_sucursal_comercio(
    tenant_id: int,
    payload: CrearSucursalRequest,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Agrega una nueva sucursal al restaurante con su propio código y QR para WhatsApp."""
    verify_tenant_isolation(current_user, tenant_id)

    comercio = (
        db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    )
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    if (
        comercio.plan
        and len(comercio.sucursales) >= comercio.plan.max_sucursales
        and current_user.rol != "SUPER_ADMIN"
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Tu plan actual ({comercio.plan.nombre}) permite hasta {comercio.plan.max_sucursales} sucursales.",
        )

    sucursal = SucursalComercio(
        tenant_id=tenant_id,
        nombre_sucursal=payload.nombre_sucursal.strip(),
        codigo_whatsapp=payload.codigo_whatsapp.strip().replace(" ", "-"),
        es_principal=False,
        direccion_fisica=payload.direccion_fisica.strip(),
        horario_apertura=payload.horario_apertura,
        horario_cierre=payload.horario_cierre,
        tiempo_estimado_entrega_min=payload.tiempo_estimado_entrega_min,
        costo_envio_base=payload.costo_envio_base,
        ancho_ticket_termico=payload.ancho_ticket_termico,
    )
    db.add(sucursal)
    db.commit()
    db.refresh(sucursal)
    return {"status": "ok", "sucursal_id": sucursal.id}


@router.patch("/comercio/sucursales/{sucursal_id}/operacion")
def actualizar_operacion_sucursal(
    sucursal_id: int,
    payload: ActualizarSucursalOperacionRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Actualiza en vivo si la sucursal está abierta, su tiempo estimado (ej. 35 -> 50 min) y ancho de ticket."""
    suc = (
        db.query(SucursalComercio)
        .filter(SucursalComercio.id == sucursal_id)
        .first()
    )
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    verify_tenant_isolation(current_user, suc.tenant_id)
    _verify_staff_branch_scope(current_user, suc.id)
    suc.abierto_ahora = payload.abierto_ahora
    suc.tiempo_estimado_entrega_min = payload.tiempo_estimado_entrega_min
    suc.ancho_ticket_termico = payload.ancho_ticket_termico
    db.commit()
    return {"status": "ok", "sucursal_id": suc.id}


# =====================================================================
# 5. CONTROL DE INVENTARIO Y STOCK PARA RESTAURANTES
# =====================================================================
@router.get("/comercio/{tenant_id}/inventario")
def listar_inventario_comercio(
    tenant_id: int,
    sucursal_id: Optional[int] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consulta el inventario completo, alertas de bajo stock y valorización en Córdobas."""
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol == "SUCURSAL_STAFF" and current_user.sucursal_id:
        sucursal_id = current_user.sucursal_id

    es_staff = current_user.rol == "SUCURSAL_STAFF"

    query = db.query(ItemInventario).filter(
        ItemInventario.tenant_id == tenant_id,
        ItemInventario.activo == True,
    )
    if sucursal_id:
        query = query.filter(
            (ItemInventario.sucursal_id == sucursal_id)
            | (ItemInventario.sucursal_id == None)
        )

    items = query.order_by(ItemInventario.categoria, ItemInventario.nombre_insumo).all()

    items_data = []
    total_items = len(items)
    items_bajo_stock = 0
    items_agotados = 0
    valor_total_inventario = 0.0
    categorias_set = set()

    for it in items:
        categorias_set.add(it.categoria)
        valor_item = round(it.stock_actual * it.costo_unitario, 2)
        valor_total_inventario += valor_item

        if it.stock_actual <= 0:
            estado = "AGOTADO"
            items_agotados += 1
        elif it.stock_actual <= it.stock_minimo:
            estado = "BAJO_STOCK"
            items_bajo_stock += 1
        else:
            estado = "NORMAL"

        items_data.append(
            {
                "id": it.id,
                "nombre_insumo": it.nombre_insumo,
                "categoria": it.categoria,
                "unidad_medida": it.unidad_medida,
                "stock_actual": it.stock_actual,
                "stock_minimo": it.stock_minimo,
                "costo_unitario": None if es_staff else it.costo_unitario,
                "valor_total": None if es_staff else valor_item,
                "estado": estado,
                "proveedor": it.proveedor or "No especificado",
                "notas": it.notas or "",
                "actualizado_en": (
                    it.actualizado_en.strftime("%d/%m/%Y %H:%M")
                    if it.actualizado_en
                    else ""
                ),
            }
        )

    return {
        "items": items_data,
        "metricas": {
            "total_items": total_items,
            "items_bajo_stock": items_bajo_stock,
            "items_agotados": items_agotados,
            "items_optimos": total_items - (items_bajo_stock + items_agotados),
            "valor_total_inventario": None if es_staff else round(valor_total_inventario, 2),
            "categorias_contabilizadas": len(categorias_set),
        },
        "categorias": sorted(list(categorias_set)),
    }


@router.post("/comercio/{tenant_id}/inventario")
def crear_item_inventario(
    tenant_id: int,
    payload: CrearItemInventarioRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Registra una nueva materia prima o insumo en el inventario del restaurante."""
    verify_tenant_isolation(current_user, tenant_id)

    item = ItemInventario(
        tenant_id=tenant_id,
        sucursal_id=payload.sucursal_id,
        nombre_insumo=payload.nombre_insumo.strip(),
        categoria=payload.categoria.strip() or "General",
        unidad_medida=payload.unidad_medida.strip() or "unidades",
        stock_actual=max(0.0, float(payload.stock_actual)),
        stock_minimo=max(0.0, float(payload.stock_minimo)),
        costo_unitario=max(0.0, float(payload.costo_unitario)),
        proveedor=payload.proveedor.strip() if payload.proveedor else None,
        notas=payload.notas.strip() if payload.notas else None,
        activo=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    if item.stock_actual > 0:
        mov = MovimientoInventario(
            tenant_id=tenant_id,
            item_id=item.id,
            tipo_movimiento="ENTRADA",
            cantidad=item.stock_actual,
            stock_anterior=0.0,
            stock_nuevo=item.stock_actual,
            motivo="Carga inicial de inventario",
            actor_email=current_user.email,
        )
        db.add(mov)
        db.commit()

    record_audit_log(
        db=db,
        accion="CREAR_ITEM_INVENTARIO",
        recurso_tipo="ITEM_INVENTARIO",
        recurso_id=str(item.id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"nombre": item.nombre_insumo, "stock_inicial": item.stock_actual},
    )

    return {"status": "ok", "item_id": item.id}


@router.patch("/comercio/inventario/{item_id}/ajustar")
def ajustar_stock_item(
    item_id: int,
    payload: AjustarStockRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Ajuste rápido de stock: SUMAR (compra), RESTAR (merma/consumo) o FIJAR (conteo)."""
    item = db.query(ItemInventario).filter(ItemInventario.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Insumo no encontrado.")

    verify_tenant_isolation(current_user, item.tenant_id)
    _verify_staff_branch_scope(current_user, item.sucursal_id)

    tipo = payload.tipo.upper().strip()
    cant = abs(float(payload.cantidad))
    stock_anterior = item.stock_actual

    if tipo == "SUMAR":
        stock_nuevo = round(stock_anterior + cant, 2)
        tipo_mov = "ENTRADA"
    elif tipo == "RESTAR":
        stock_nuevo = max(0.0, round(stock_anterior - cant, 2))
        tipo_mov = "SALIDA"
    elif tipo == "FIJAR":
        stock_nuevo = max(0.0, round(float(payload.cantidad), 2))
        tipo_mov = "AJUSTE"
    else:
        raise HTTPException(
            status_code=400,
            detail="Tipo de ajuste inválido (SUMAR, RESTAR o FIJAR).",
        )

    item.stock_actual = stock_nuevo
    item.actualizado_en = datetime.now(timezone.utc)

    mov = MovimientoInventario(
        tenant_id=item.tenant_id,
        item_id=item.id,
        tipo_movimiento=tipo_mov,
        cantidad=cant if tipo != "FIJAR" else abs(stock_nuevo - stock_anterior),
        stock_anterior=stock_anterior,
        stock_nuevo=stock_nuevo,
        motivo=payload.motivo or "Ajuste desde Panel Web",
        actor_email=current_user.email,
    )
    db.add(mov)
    db.commit()

    record_audit_log(
        db=db,
        accion="AJUSTE_STOCK_INVENTARIO",
        recurso_tipo="ITEM_INVENTARIO",
        recurso_id=str(item.id),
        usuario_id=current_user.id,
        tenant_id=item.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={
            "insumo": item.nombre_insumo,
            "anterior": stock_anterior,
            "nuevo": stock_nuevo,
            "tipo": tipo,
            "motivo": payload.motivo,
        },
    )

    return {
        "status": "ok",
        "item_id": item.id,
        "stock_anterior": stock_anterior,
        "stock_nuevo": stock_nuevo,
    }


@router.put("/comercio/inventario/{item_id}")
def actualizar_item_inventario(
    item_id: int,
    payload: CrearItemInventarioRequest,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Edita los datos maestros de un insumo."""
    item = db.query(ItemInventario).filter(ItemInventario.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Insumo no encontrado.")

    verify_tenant_isolation(current_user, item.tenant_id)

    item.nombre_insumo = payload.nombre_insumo.strip()
    item.categoria = payload.categoria.strip() or "General"
    item.unidad_medida = payload.unidad_medida.strip() or "unidades"
    item.stock_minimo = max(0.0, float(payload.stock_minimo))
    item.costo_unitario = max(0.0, float(payload.costo_unitario))
    item.proveedor = payload.proveedor.strip() if payload.proveedor else None
    item.notas = payload.notas.strip() if payload.notas else None
    item.actualizado_en = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok", "item_id": item.id}


@router.delete("/comercio/inventario/{item_id}")
def eliminar_item_inventario(
    item_id: int,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Desactiva un insumo del inventario."""
    item = db.query(ItemInventario).filter(ItemInventario.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Insumo no encontrado.")

    verify_tenant_isolation(current_user, item.tenant_id)
    item.activo = False
    db.commit()

    return {"status": "ok", "deleted_id": item_id}


# =====================================================================
# 6. GESTIÓN DE EQUIPO STAFF POR EL DUEÑO (COMERCIO_ADMIN)
# =====================================================================
@router.get("/comercio/{tenant_id}/usuarios")
def listar_equipo_comercio(
    tenant_id: int,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Lista los usuarios (COMERCIO_ADMIN y SUCURSAL_STAFF) del comercio actual."""
    verify_tenant_isolation(current_user, tenant_id)
    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    usuarios = db.query(Usuario).filter(Usuario.tenant_id == tenant_id).order_by(Usuario.id).all()
    sucursales = db.query(SucursalComercio).filter(SucursalComercio.tenant_id == tenant_id).all()
    suc_map = {s.id: s.nombre_sucursal for s in sucursales}

    return {
        "comercio_id": tenant_id,
        "sucursales": [{"id": s.id, "nombre_sucursal": s.nombre_sucursal} for s in sucursales],
        "usuarios": [
            {
                "id": u.id,
                "nombre_completo": u.nombre_completo,
                "email": u.email,
                "rol": u.rol,
                "sucursal_id": u.sucursal_id,
                "sucursal_nombre": suc_map.get(u.sucursal_id, "Todas las sucursales"),
                "activo": u.activo,
            }
            for u in usuarios
            if u.rol in ("COMERCIO_ADMIN", "SUCURSAL_STAFF")
        ],
    }


@router.post("/comercio/{tenant_id}/usuarios")
def crear_staff_comercio(
    tenant_id: int,
    payload: CrearStaffComercioRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite al Dueño (COMERCIO_ADMIN) crear cuentas de personal operativo (SUCURSAL_STAFF)."""
    verify_tenant_isolation(current_user, tenant_id)

    email_clean = payload.email.lower().strip()
    if db.query(Usuario).filter(Usuario.email == email_clean).first():
        raise HTTPException(status_code=400, detail="Ese correo ya está registrado.")

    if payload.sucursal_id:
        suc = (
            db.query(SucursalComercio)
            .filter(SucursalComercio.id == payload.sucursal_id, SucursalComercio.tenant_id == tenant_id)
            .first()
        )
        if not suc:
            raise HTTPException(status_code=404, detail="La sucursal seleccionada no pertenece a este comercio.")

    nuevo_staff = Usuario(
        tenant_id=tenant_id,
        sucursal_id=payload.sucursal_id or None,
        nombre_completo=payload.nombre_completo.strip(),
        email=email_clean,
        password_hash=hash_password(payload.password),
        rol="SUCURSAL_STAFF",
        activo=True,
    )
    db.add(nuevo_staff)
    db.commit()
    db.refresh(nuevo_staff)

    record_audit_log(
        db=db,
        accion="CREAR_STAFF_COMERCIO",
        recurso_tipo="USUARIO",
        recurso_id=str(nuevo_staff.id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"email": email_clean, "sucursal_id": nuevo_staff.sucursal_id},
    )
    return {"status": "ok", "usuario_id": nuevo_staff.id}


@router.patch("/comercio/{tenant_id}/usuarios/{usuario_id}")
def actualizar_staff_comercio(
    tenant_id: int,
    usuario_id: int,
    payload: ActualizarStaffComercioRequest,
    request: Request,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Permite al Dueño activar/pausar, cambiar sucursal o restablecer contraseña de su personal SUCURSAL_STAFF."""
    verify_tenant_isolation(current_user, tenant_id)

    staff = (
        db.query(Usuario)
        .filter(Usuario.id == usuario_id, Usuario.tenant_id == tenant_id)
        .first()
    )
    if not staff:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en este comercio.")
    if staff.rol != "SUCURSAL_STAFF":
        raise HTTPException(status_code=403, detail="Solo puedes gestionar cuentas operativas de tipo SUCURSAL_STAFF.")

    if payload.activo is not None:
        staff.activo = payload.activo
    if payload.nueva_password:
        staff.password_hash = hash_password(payload.nueva_password)
    if payload.sucursal_id is not None:
        if payload.sucursal_id > 0:
            suc = (
                db.query(SucursalComercio)
                .filter(SucursalComercio.id == payload.sucursal_id, SucursalComercio.tenant_id == tenant_id)
                .first()
            )
            if not suc:
                raise HTTPException(status_code=404, detail="Sucursal inválida para este comercio.")
            staff.sucursal_id = suc.id
        else:
            staff.sucursal_id = None

    db.commit()

    record_audit_log(
        db=db,
        accion="ACTUALIZAR_STAFF_COMERCIO",
        recurso_tipo="USUARIO",
        recurso_id=str(staff.id),
        usuario_id=current_user.id,
        tenant_id=tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"activo": staff.activo, "password_reset": bool(payload.nueva_password)},
    )
    return {"status": "ok", "usuario_id": staff.id, "activo": staff.activo}


# =====================================================================
# 7. ESTADÍSTICAS DE VENTAS POR NEGOCIO (COMERCIO_ADMIN / SUPER_ADMIN)
# =====================================================================
@router.get("/comercio/{tenant_id}/estadisticas")
def estadisticas_ventas_comercio(
    tenant_id: int,
    periodo: str = "30d",
    sucursal_id: Optional[int] = None,
    current_user: Usuario = Depends(require_comercio_admin),
    db: Session = Depends(get_db),
):
    """Recopila estadísticas agrupadas de ventas, métodos de pago, entrega, sucursales y top productos."""
    verify_tenant_isolation(current_user, tenant_id)

    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    ahora = datetime.now(timezone.utc)
    per = (periodo or "30d").lower().strip()
    desde_dt = None
    etiqueta_periodo = "Últimos 30 días"
    if per == "hoy":
        desde_dt = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
        etiqueta_periodo = "Hoy"
    elif per == "7d":
        desde_dt = ahora - timedelta(days=7)
        etiqueta_periodo = "Últimos 7 días"
    elif per == "mes":
        desde_dt = ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        etiqueta_periodo = "Mes actual"
    elif per == "todo":
        desde_dt = None
        etiqueta_periodo = "Histórico completo"
    else:
        per = "30d"
        desde_dt = ahora - timedelta(days=30)
        etiqueta_periodo = "Últimos 30 días"

    query = db.query(Pedido).filter(Pedido.tenant_id == tenant_id)
    if sucursal_id:
        query = query.filter(Pedido.sucursal_id == sucursal_id)
    if desde_dt is not None:
        query = query.filter(Pedido.creado_en >= desde_dt)

    pedidos = query.order_by(Pedido.creado_en.asc()).all()
    suc_map = {s.id: s.nombre_sucursal for s in comercio.sucursales}

    ventas_totales = 0.0
    subtotal_productos = 0.0
    ingresos_envio = 0.0
    pedidos_validos = 0
    pedidos_cancelados = 0
    pedidos_pagados = 0
    ventas_cobradas = 0.0
    ventas_por_cobrar = 0.0

    metodos_map = {}
    entregas_map = {}
    estados_map = {}
    sucursales_map = {}
    productos_map = {}
    dias_map = {}

    for ped in pedidos:
        est = ped.estado_pedido or "RECIBIDO"
        estados_map.setdefault(est, {"estado": est, "pedidos": 0, "total": 0.0})
        estados_map[est]["pedidos"] += 1
        estados_map[est]["total"] = round(estados_map[est]["total"] + float(ped.total or 0), 2)

        if est == "CANCELADO":
            pedidos_cancelados += 1
            continue

        pedidos_validos += 1
        tot = float(ped.total or 0.0)
        sub = float(ped.subtotal or 0.0)
        env = float(ped.costo_envio or 0.0)

        ventas_totales += tot
        subtotal_productos += sub
        ingresos_envio += env

        if ped.estado_pago == "PAGADO":
            pedidos_pagados += 1
            ventas_cobradas += tot
        else:
            ventas_por_cobrar += tot

        met = ped.metodo_pago or "EFECTIVO"
        metodos_map.setdefault(met, {"metodo": met, "pedidos": 0, "total": 0.0})
        metodos_map[met]["pedidos"] += 1
        metodos_map[met]["total"] = round(metodos_map[met]["total"] + tot, 2)

        ent = ped.tipo_entrega or "DOMICILIO"
        entregas_map.setdefault(ent, {"tipo": ent, "pedidos": 0, "total": 0.0})
        entregas_map[ent]["pedidos"] += 1
        entregas_map[ent]["total"] = round(entregas_map[ent]["total"] + tot, 2)

        sname = suc_map.get(ped.sucursal_id, f"Sucursal #{ped.sucursal_id}")
        sucursales_map.setdefault(
            ped.sucursal_id,
            {"sucursal_id": ped.sucursal_id, "sucursal_nombre": sname, "pedidos": 0, "total": 0.0},
        )
        sucursales_map[ped.sucursal_id]["pedidos"] += 1
        sucursales_map[ped.sucursal_id]["total"] = round(sucursales_map[ped.sucursal_id]["total"] + tot, 2)

        if ped.creado_en:
            fecha_key = ped.creado_en.strftime("%Y-%m-%d")
            fecha_lbl = ped.creado_en.strftime("%d/%m")
        else:
            fecha_key = "0000-00-00"
            fecha_lbl = "N/A"
        dias_map.setdefault(fecha_key, {"fecha": fecha_key, "etiqueta": fecha_lbl, "pedidos": 0, "total": 0.0})
        dias_map[fecha_key]["pedidos"] += 1
        dias_map[fecha_key]["total"] = round(dias_map[fecha_key]["total"] + tot, 2)

        for det in ped.detalles:
            pnombre = det.nombre_plato or "Producto"
            productos_map.setdefault(pnombre, {"nombre": pnombre, "cantidad": 0, "ingresos": 0.0})
            productos_map[pnombre]["cantidad"] += int(det.cantidad or 1)
            productos_map[pnombre]["ingresos"] = round(
                productos_map[pnombre]["ingresos"] + float(det.subtotal_linea or 0.0), 2
            )

    ticket_promedio = round(ventas_totales / pedidos_validos, 2) if pedidos_validos > 0 else 0.0
    top_productos = sorted(productos_map.values(), key=lambda x: (-x["cantidad"], -x["ingresos"]))[:10]
    tendencia_diaria = [dias_map[k] for k in sorted(dias_map.keys())][-14:]

    from app.api.delivery_api import get_or_create_delivery_config

    deliv_cfg = get_or_create_delivery_config(db, tenant_id)
    logo_row = (
        db.query(ComercioLogo.actualizado_en)
        .filter(ComercioLogo.tenant_id == tenant_id)
        .first()
    )
    logo_url = (
        f"/api/v1/public/logo/{tenant_id}?v={int(logo_row[0].timestamp()) if logo_row[0] else 1}"
        if logo_row
        else None
    )

    return {
        "comercio": {
            "id": comercio.id,
            "nombre_comercial": comercio.nombre_comercial,
            "logo_url": logo_url,
            "delivery_config": {
                "modo_delivery": deliv_cfg.modo_delivery,
                "requiere_pin_entrega": deliv_cfg.requiere_pin_entrega,
                "notificar_whatsapp_repartidor": deliv_cfg.notificar_whatsapp_repartidor,
                "permite_repartidor_propio": bool(__import__("app.api.delivery_api", fromlist=["get_or_create_permiso_delivery"]).get_or_create_permiso_delivery(db, comercio.id).permite_repartidor_propio),
                "max_repartidores_propios": int(__import__("app.api.delivery_api", fromlist=["get_or_create_permiso_delivery"]).get_or_create_permiso_delivery(db, comercio.id).max_repartidores_propios or 1),
            },
            "giro": comercio.giro,
            "moneda": comercio.moneda,
            "simbolo_moneda": comercio.simbolo_moneda,
        },
        "periodo": {
            "codigo": per,
            "etiqueta": etiqueta_periodo,
            "desde": desde_dt.isoformat() if desde_dt else None,
        },
        "resumen": {
            "ventas_totales": round(ventas_totales, 2),
            "subtotal_productos": round(subtotal_productos, 2),
            "ingresos_envio": round(ingresos_envio, 2),
            "pedidos_validos": pedidos_validos,
            "pedidos_cancelados": pedidos_cancelados,
            "total_pedidos": len(pedidos),
            "ticket_promedio": ticket_promedio,
            "pedidos_pagados": pedidos_pagados,
            "ventas_cobradas": round(ventas_cobradas, 2),
            "ventas_por_cobrar": round(ventas_por_cobrar, 2),
        },
        "por_metodo_pago": sorted(metodos_map.values(), key=lambda x: -x["total"]),
        "por_tipo_entrega": sorted(entregas_map.values(), key=lambda x: -x["total"]),
        "por_estado": list(estados_map.values()),
        "por_sucursal": sorted(sucursales_map.values(), key=lambda x: -x["total"]),
        "top_productos": top_productos,
        "tendencia_diaria": tendencia_diaria,
    }


class ProductoImagenUploadRequest(BaseModel):
    imagen_base64: str


@router.put("/comercio/{tenant_id}/platos/{plato_id}/imagen")
def subir_imagen_producto(
    tenant_id: int,
    plato_id: int,
    payload: ProductoImagenUploadRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Guarda o reemplaza la miniatura WebP/JPEG comprimida del producto (máx 180 KB)."""
    import base64

    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN", "SUCURSAL_STAFF"):
        raise HTTPException(status_code=403, detail="Sin permiso para editar imágenes.")

    plato = (
        db.query(ProductoPlato)
        .filter(ProductoPlato.id == plato_id, ProductoPlato.tenant_id == tenant_id)
        .first()
    )
    if not plato:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    raw = (payload.imagen_base64 or "").strip()
    mime = "image/webp"
    if "," in raw and raw.startswith("data:"):
        header, b64_part = raw.split(",", 1)
        if "image/jpeg" in header or "image/jpg" in header:
            mime = "image/jpeg"
        elif "image/png" in header:
            mime = "image/png"
    else:
        b64_part = raw

    try:
        img_bytes = base64.b64decode(b64_part, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Imagen Base64 inválida.")

    if len(img_bytes) == 0 or len(img_bytes) > 180 * 1024:
        raise HTTPException(
            status_code=400,
            detail="La imagen comprimida supera el límite de 180 KB. Intenta con otra imagen.",
        )

    existing = (
        db.query(ProductoImagen)
        .filter(ProductoImagen.plato_id == plato.id)
        .first()
    )
    now_dt = datetime.now(timezone.utc)
    if existing:
        existing.mime_type = mime
        existing.imagen_bytes = img_bytes
        existing.actualizado_en = now_dt
    else:
        db.add(
            ProductoImagen(
                tenant_id=tenant_id,
                plato_id=plato.id,
                mime_type=mime,
                imagen_bytes=img_bytes,
                actualizado_en=now_dt,
            )
        )
    db.commit()
    return {
        "status": "ok",
        "plato_id": plato.id,
        "bytes": len(img_bytes),
        "mime_type": mime,
        "imagen_url": f"/api/v1/public/img/{plato.id}?v={int(now_dt.timestamp())}",
    }


@router.delete("/comercio/{tenant_id}/platos/{plato_id}/imagen")
def eliminar_imagen_producto(
    tenant_id: int,
    plato_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Elimina la foto de un producto."""
    verify_tenant_isolation(current_user, tenant_id)
    deleted = (
        db.query(ProductoImagen)
        .filter(
            ProductoImagen.tenant_id == tenant_id,
            ProductoImagen.plato_id == plato_id,
        )
        .delete()
    )
    db.commit()
    return {"status": "ok", "deleted": bool(deleted)}


# =====================================================================
# LOGO PROPIO DEL COMERCIO (256x256 WebP COMPRIMIDO EN NAVEGADOR)
# =====================================================================
@router.put("/comercio/{tenant_id}/logo")
def subir_logo_comercio(
    tenant_id: int,
    payload: ProductoImagenUploadRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Guarda o reemplaza el logo WebP comprimido del comercio (máx 150 KB)."""
    import base64

    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo el administrador del comercio puede cambiar el logo.")

    raw = (payload.imagen_base64 or "").strip()
    mime = "image/webp"
    if "," in raw and raw.startswith("data:"):
        header, b64_part = raw.split(",", 1)
        if "image/jpeg" in header or "image/jpg" in header:
            mime = "image/jpeg"
        elif "image/png" in header:
            mime = "image/png"
    else:
        b64_part = raw

    try:
        img_bytes = base64.b64decode(b64_part, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Logo Base64 inválido.")

    if len(img_bytes) == 0 or len(img_bytes) > 150 * 1024:
        raise HTTPException(status_code=400, detail="El logo comprimido supera el límite de 150 KB.")

    existing = db.query(ComercioLogo).filter(ComercioLogo.tenant_id == tenant_id).first()
    now_dt = datetime.now(timezone.utc)
    if existing:
        existing.mime_type = mime
        existing.imagen_bytes = img_bytes
        existing.actualizado_en = now_dt
    else:
        db.add(
            ComercioLogo(
                tenant_id=tenant_id,
                mime_type=mime,
                imagen_bytes=img_bytes,
                actualizado_en=now_dt,
            )
        )
    db.commit()
    return {
        "status": "ok",
        "bytes": len(img_bytes),
        "logo_url": f"/api/v1/public/logo/{tenant_id}?v={int(now_dt.timestamp())}",
    }


@router.delete("/comercio/{tenant_id}/logo")
def eliminar_logo_comercio(
    tenant_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Elimina el logo personalizado del comercio."""
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo el administrador del comercio puede eliminar el logo.")
    deleted = db.query(ComercioLogo).filter(ComercioLogo.tenant_id == tenant_id).delete()
    db.commit()
    return {"status": "ok", "deleted": bool(deleted)}


# =====================================================================
# CORTE / CIERRE DE CAJA DEL DÍA (LISTO PARA EFECTIVO, TRANSF. Y PAGADITO API)
# =====================================================================
@router.get("/comercio/{tenant_id}/corte-caja")
def obtener_corte_caja_diario(
    tenant_id: int,
    sucursal_id: Optional[int] = None,
    fecha: Optional[str] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Resumen diario de cierre de caja separando Efectivo, Transferencias,
    Pago Online (Simulador / Pagadito API), Anticipos de Reservas, Envíos y Cancelados.
    """
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo administradores pueden consultar el corte de caja.")

    comercio = db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    now_utc = datetime.now(timezone.utc)
    if fecha:
        try:
            day_start = datetime.strptime(fecha.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    q = db.query(Pedido).filter(
        Pedido.tenant_id == tenant_id,
        Pedido.creado_en >= day_start,
        Pedido.creado_en < day_end,
    )
    if sucursal_id:
        q = q.filter(Pedido.sucursal_id == sucursal_id)
    pedidos = q.all()

    efectivo_total = 0.0
    transferencia_total = 0.0
    online_api_total = 0.0
    envios_total = 0.0
    venta_bruta = 0.0
    total_cobrado = 0.0
    pedidos_validos = 0
    pedidos_cancelados = 0
    pedidos_esperando_pago = 0

    for p in pedidos:
        if p.estado_pedido == "CANCELADO":
            pedidos_cancelados += 1
            continue
        if p.estado_pedido == "ESPERANDO_PAGO":
            pedidos_esperando_pago += 1
            continue

        pedidos_validos += 1
        tot = float(p.total or 0.0)
        env = float(p.costo_envio or 0.0)
        venta_bruta += tot
        envios_total += env

        met = (p.metodo_pago or "").upper()
        if "PASARELA" in met or "PAGADITO" in met or "ONLINE" in met or "TARJETA" in met:
            online_api_total += tot
        elif "TRANSF" in met:
            transferencia_total += tot
        else:
            efectivo_total += tot

        if p.estado_pago == "PAGADO" or p.estado_pedido == "ENTREGADO":
            total_cobrado += tot

    # Anticipos de reservaciones pagadas hoy
    rq = db.query(ReservacionMesa).filter(
        ReservacionMesa.tenant_id == tenant_id,
        ReservacionMesa.creado_en >= day_start,
        ReservacionMesa.creado_en < day_end,
        ReservacionMesa.estado_pago == "PAGADO",
    )
    if sucursal_id:
        rq = rq.filter(ReservacionMesa.sucursal_id == sucursal_id)
    reservas_pagadas = rq.all()
    anticipos_reservas = sum(float(r.monto_pago_requerido or 0.0) for r in reservas_pagadas)

    return {
        "fecha": day_start.strftime("%Y-%m-%d"),
        "simbolo_moneda": comercio.simbolo_moneda or "C$",
        "nombre_comercial": comercio.nombre_comercial,
        "resumen": {
            "venta_bruta": round(venta_bruta, 2),
            "total_cobrado": round(total_cobrado, 2),
            "efectivo_en_caja": round(efectivo_total, 2),
            "transferencias": round(transferencia_total, 2),
            "pago_online_api": round(online_api_total, 2),
            "anticipos_reservas": round(anticipos_reservas, 2),
            "envios_total": round(envios_total, 2),
            "pedidos_validos": pedidos_validos,
            "pedidos_cancelados": pedidos_cancelados,
            "pedidos_esperando_pago": pedidos_esperando_pago,
            "reservas_pagadas_count": len(reservas_pagadas),
        },
    }



