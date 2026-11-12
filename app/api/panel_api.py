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
from app.core.deps import (
    get_current_user,
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
    MensajeChatWhatsApp,
    MesaZona,
    Pedido,
    PlanSaaS,
    ProductoPlato,
    ReservacionMesa,
    SesionChatWhatsApp,
    SucursalComercio,
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
    email_dueno: EmailStr
    nombre_dueno: str
    password_dueno: str
    nombre_sucursal_principal: str = "Sucursal Principal"
    direccion_sucursal: str = "Dirección por definir"
    costo_envio_base: float = 50.00


class ConfigurarComercioRequest(BaseModel):
    moneda: Optional[str] = None
    simbolo_moneda: Optional[str] = None
    nombre_comercial: Optional[str] = None
    telefono_propietario: Optional[str] = None
    datos_bancarios_texto: Optional[str] = None
    acepta_efectivo: Optional[bool] = None
    acepta_transferencia: Optional[bool] = None


class ActualizarPrecioPlatoRequest(BaseModel):
    precio_base: float


class ActualizarPrecioIngredienteRequest(BaseModel):
    precio_adicional: float


class CambiarEstadoSuscripcionRequest(BaseModel):
    estado_suscripcion: str  # ACTIVO | TRIAL | POR_VENCER | SUSPENDIDO
    plan_id: Optional[int] = None


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
    """Devuelve toda la información global del SaaS para tu Panel de Dueño."""
    planes = db.query(PlanSaaS).order_by(PlanSaaS.precio_mensual_usd).all()
    comercios = db.query(TenantComercio).order_by(TenantComercio.id.desc()).all()
    total_pedidos = db.query(Pedido).count()
    logs = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(20).all()

    ingresos_mensuales_estimados = sum(
        c.plan.precio_mensual_usd
        for c in comercios
        if c.estado_suscripcion == "ACTIVO" and c.plan
    )

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
        comercios_data.append(
            {
                "id": c.id,
                "nombre_comercial": c.nombre_comercial,
                "slug": c.slug,
                "giro": c.giro,
                "email_contacto": c.email_contacto,
                "estado_suscripcion": c.estado_suscripcion,
                "pedidos_mes_actual": c.pedidos_mes_actual,
                "plan": {
                    "id": c.plan.id if c.plan else None,
                    "nombre": c.plan.nombre if c.plan else "Sin Plan",
                    "precio_mensual_usd": c.plan.precio_mensual_usd if c.plan else 0,
                    "limite_pedidos_mes": c.plan.limite_pedidos_mes if c.plan else 0,
                    "porcentaje_comision": c.plan.porcentaje_comision_pedido if c.plan else 0,
                },
                "sucursales": sucs,
            }
        )

    return {
        "metricas_saas": {
            "total_comercios": len(comercios),
            "comercios_activos": sum(
                1 for c in comercios if c.estado_suscripcion == "ACTIVO"
            ),
            "ingresos_fijos_mensuales_usd": round(ingresos_mensuales_estimados, 2),
            "total_pedidos_procesados": total_pedidos,
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
                "max_sucursales": p.max_sucursales,
            }
            for p in planes
        ],
        "comercios": comercios_data,
        "audit_logs": [
            {
                "id": l.id,
                "accion": l.accion,
                "actor_email": l.actor_email,
                "recurso_tipo": l.recurso_tipo,
                "recurso_id": l.recurso_id,
                "ip_origen": l.ip_origen,
                "creado_en": l.creado_en.strftime("%Y-%m-%d %H:%M:%S")
                if l.creado_en
                else "",
            }
            for l in logs
        ],
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

    nuevo_comercio = TenantComercio(
        nombre_comercial=payload.nombre_comercial.strip(),
        slug=slug_clean,
        giro=payload.giro,
        email_contacto=payload.email_dueno.lower().strip(),
        plan_id=plan.id,
        estado_suscripcion="ACTIVO",
        fecha_vencimiento_suscripcion=datetime.now(timezone.utc) + timedelta(days=30),
        moneda=payload.moneda,
        simbolo_moneda=payload.simbolo_moneda,
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

    cat_inicial = CategoriaMenu(
        tenant_id=nuevo_comercio.id, nombre="Menú Principal", orden=1
    )
    db.add(cat_inicial)
    db.commit()

    record_audit_log(
        db=db,
        accion="CREAR_COMERCIO_SAAS",
        recurso_tipo="TENANT_COMERCIO",
        recurso_id=str(nuevo_comercio.id),
        usuario_id=current_user.id,
        actor_email=current_user.email,
        ip_origen=request.client.host if request.client else "127.0.0.1",
        detalles={"nombre": nuevo_comercio.nombre_comercial, "slug": slug_clean},
    )

    return {"status": "ok", "comercio_id": nuevo_comercio.id, "codigo_whatsapp": codigo_wa}


@router.patch("/superadmin/comercios/{tenant_id}/suscripcion")
def cambiar_estado_suscripcion_comercio(
    tenant_id: int,
    payload: CambiarEstadoSuscripcionRequest,
    request: Request,
    current_user: Usuario = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Permite activar o suspender un comercio (corte automático si no paga su mensualidad)."""
    comercio = (
        db.query(TenantComercio).filter(TenantComercio.id == tenant_id).first()
    )
    if not comercio:
        raise HTTPException(status_code=404, detail="Comercio no encontrado.")

    comercio.estado_suscripcion = payload.estado_suscripcion
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


# =====================================================================
# 2. ENDPOINTS PARA EL PANEL DEL RESTAURANTE / COMERCIO
# =====================================================================
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
    sucursales_data = []
    for s in comercio.sucursales:
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

    estados_validos = {"RECIBIDO", "EN_PROCESO", "LISTO", "ENTREGADO", "CANCELADO"}
    nuevo = payload.nuevo_estado.upper().strip()
    if nuevo not in estados_validos:
        raise HTTPException(status_code=400, detail="Estado de pedido no válido.")

    estado_anterior = pedido.estado_pedido
    pedido.estado_pedido = nuevo
    pedido.actualizado_en = datetime.now(timezone.utc)

    # Notificación automática al chat de WhatsApp del cliente
    mensajes_estado = {
        "RECIBIDO": f"📥 ¡Hola {pedido.nombre_cliente}! Tu pedido #{pedido.folio} ha sido *RECIBIDO* por el restaurante.",
        "EN_PROCESO": f"👨‍🍳 ¡Tu pedido #{pedido.folio} ya está *EN PROCESO* de preparación en cocina!",
        "LISTO": f"✅ ¡Tu pedido #{pedido.folio} está *LISTO*! {'Pronto saldrá a tu domicilio.' if pedido.tipo_entrega == 'DOMICILIO' else 'Ya puedes pasar a recogerlo.'}",
        "ENTREGADO": f"🙌 Tu pedido #{pedido.folio} fue marcado como *ENTREGADO*. ¡Muchas gracias por tu preferencia!",
        "CANCELADO": f"⚠️ Tu pedido #{pedido.folio} ha sido *CANCELADO*. Si tienes dudas, escríbenos por este chat.",
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
    current_user: Usuario = Depends(get_current_user),
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
    current_user: Usuario = Depends(get_current_user),
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
    current_user: Usuario = Depends(get_current_user),
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
    current_user: Usuario = Depends(get_current_user),
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
    current_user: Usuario = Depends(get_current_user),
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
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permite editar la moneda (C$, $, etc.), nombre comercial, datos bancarios y formas de pago del restaurante."""
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
            "datos_bancarios_texto": comercio.datos_bancarios_texto,
            "acepta_efectivo": comercio.acepta_efectivo,
            "acepta_transferencia": comercio.acepta_transferencia,
        },
    }


@router.post("/comercio/{tenant_id}/sucursales")
def crear_sucursal_comercio(
    tenant_id: int,
    payload: CrearSucursalRequest,
    current_user: Usuario = Depends(get_current_user),
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
    suc.abierto_ahora = payload.abierto_ahora
    suc.tiempo_estimado_entrega_min = payload.tiempo_estimado_entrega_min
    suc.ancho_ticket_termico = payload.ancho_ticket_termico
    db.commit()
    return {"status": "ok", "sucursal_id": suc.id}

