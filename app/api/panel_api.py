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
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.audit import record_audit_log
from app.core.config import settings
from app.core.database import get_db
from app.core.demo_seeds import normalize_giro_code, populate_tenant_demo_data
from app.core.deps import (
    get_current_user,
    require_comercio_admin,
    require_super_admin,
    verify_tenant_isolation,
)
from app.core.security import (
    build_whatsapp_branch_deep_link,
    generate_qr_base64_data_uri,
    hash_password,
)
from app.models.saas_models import (
    AuditLog,
    CategoriaMenu,
    DetallePedido,
    IngredienteOpcionPlato,
    ItemInventario,
    MovimientoInventario,
    MensajeChatWhatsApp,
    MesaZona,
    Pedido,
    PlanSaaS,
    ProductoPlato,
    ReservacionMesa,
    SesionChatWhatsApp,
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
    usa_mesas = giro_norm in ("RESTAURANTE", "CAFETERIA")

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
        costo_envio_base=payload.costo_envio_base,
    )
    db.add(sucursal)

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
                "ancho_ticket_termico": s.ancho_ticket_termico,
                "deep_link_whatsapp": deep_link,
                "qr_data_uri": generate_qr_base64_data_uri(deep_link),
                "zonas_envio": [
                    {"nombre_zona": z.nombre_zona, "costo_envio": z.costo_envio}
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

    return {
        "comercio": {
            "id": comercio.id,
            "nombre_comercial": comercio.nombre_comercial,
            "slug": comercio.slug,
            "giro": comercio.giro,
            "estado_suscripcion": comercio.estado_suscripcion,
            "moneda": comercio.moneda,
            "simbolo_moneda": comercio.simbolo_moneda,
            "datos_bancarios_texto": comercio.datos_bancarios_texto,
            "modo_pago_api_activo": comercio.modo_pago_api_activo,
            "reservaciones_habilitadas": comercio.reservaciones_habilitadas,
            "plan_nombre": comercio.plan.nombre if comercio.plan else "Plan Pro",
            "pedidos_mes_actual": comercio.pedidos_mes_actual,
            "limite_pedidos_mes": comercio.plan.limite_pedidos_mes if comercio.plan else 800,
        },
        "sucursales": sucursales_data,
        "pedidos": pedidos_data,
        "platos": platos_data,
        "reservaciones_mesa": [
            {
                "folio": r.folio_reserva,
                "nombre_cliente": r.nombre_cliente,
                "fecha_hora": r.fecha_hora_reserva,
                "personas": r.cantidad_personas,
                "monto_anticipo": r.monto_pago_requerido,
                "estado_reserva": r.estado_reserva,
            }
            for r in reservaciones
        ],
    }


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

    # Notificación automática al chat de WhatsApp del cliente
    nombre_com = (
        pedido.sucursal.comercio.nombre_comercial
        if pedido.sucursal and pedido.sucursal.comercio
        else "el comercio"
    )
    mensajes_estado = {
        "RECIBIDO": f"Hola {pedido.nombre_cliente}, tu pedido #{pedido.folio} ha sido RECIBIDO por {nombre_com}.",
        "EN_PROCESO": f"Tu pedido #{pedido.folio} ya está EN PROCESO / preparación en {nombre_com}.",
        "LISTO": f"Tu pedido #{pedido.folio} está LISTO. {'Pronto saldrá a tu domicilio.' if pedido.tipo_entrega == 'DOMICILIO' else 'Ya puedes pasar a recogerlo.'}",
        "ENTREGADO": f"Tu pedido #{pedido.folio} fue marcado como ENTREGADO. Muchas gracias por tu compra.",
        "CANCELADO": f"Tu pedido #{pedido.folio} ha sido CANCELADO. Si tienes dudas, escríbenos por este chat.",
    }
    texto_aviso = mensajes_estado.get(nuevo, f"Actualización de pedido #{pedido.folio}: {nuevo}")

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

    db.commit()

    record_audit_log(
        db=db,
        accion="CAMBIO_ESTADO_PEDIDO",
        recurso_tipo="PEDIDO",
        recurso_id=pedido.folio,
        usuario_id=current_user.id,
        tenant_id=pedido.tenant_id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"de": estado_anterior, "a": nuevo, "notificacion_wa": texto_aviso},
    )

    return {
        "status": "ok",
        "folio": pedido.folio,
        "estado_pedido": pedido.estado_pedido,
        "mensaje_enviado_whatsapp": texto_aviso,
    }


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

    return {
        "comercio": {
            "id": comercio.id,
            "nombre_comercial": comercio.nombre_comercial,
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


