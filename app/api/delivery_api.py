"""
API y Lógica de Delivery Multi-Tenant (Flota SaaS Compartida + Repartidores Propios).
Incluye:
- Modo de delivery por comercio (DESACTIVADO | FLOTA_SAAS | PROPIO) sincronizado con sucursales.
- Generación de PIN de entrega de 4 dígitos para confirmar entregas de forma segura.
- Aviso automático por WhatsApp a repartidores disponibles cuando cocina marca LISTO.
- Panel móvil ligero para usuarios con rol REPARTIDOR.
"""
from datetime import datetime, timedelta, timezone
import random
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, verify_tenant_isolation
from app.core.realtime import publish_tenant_event
from app.core.security import hash_password
from app.models.saas_models import (
    AsignacionDelivery,
    ConfigDeliveryComercio,
    MensajeChatWhatsApp,
    Pedido,
    PerfilRepartidor,
    SesionChatWhatsApp,
    SucursalComercio,
    TenantComercio,
    Usuario,
)
from app.services.whatsapp_provider import clean_whatsapp_phone, send_whatsapp_text

router = APIRouter(prefix="/api/v1/delivery", tags=["Delivery & Repartidores"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_delivery_config(db: Session, tenant_id: int) -> ConfigDeliveryComercio:
    cfg = (
        db.query(ConfigDeliveryComercio)
        .filter(ConfigDeliveryComercio.tenant_id == tenant_id)
        .first()
    )
    if not cfg:
        # Inferir estado inicial según si alguna sucursal permite domicilio
        has_dom = (
            db.query(SucursalComercio)
            .filter(
                SucursalComercio.tenant_id == tenant_id,
                SucursalComercio.permite_domicilio.is_(True),
            )
            .first()
            is not None
        )
        cfg = ConfigDeliveryComercio(
            tenant_id=tenant_id,
            modo_delivery="FLOTA_SAAS" if has_dom else "DESACTIVADO",
            requiere_pin_entrega=True,
            notificar_whatsapp_repartidor=True,
        )
        db.add(cfg)
        db.flush()
    return cfg


def ensure_delivery_assignment(db: Session, pedido: Pedido) -> Optional[AsignacionDelivery]:
    """Crea (si no existe) el registro AsignacionDelivery con PIN de 4 dígitos para pedidos a domicilio."""
    if (pedido.tipo_entrega or "").upper() != "DOMICILIO":
        return None
    asig = (
        db.query(AsignacionDelivery)
        .filter(AsignacionDelivery.pedido_id == pedido.id)
        .first()
    )
    if not asig:
        pin = f"{random.randint(1000, 9999)}"
        asig = AsignacionDelivery(
            pedido_id=pedido.id,
            tenant_id=pedido.tenant_id,
            estado_delivery="PENDIENTE",
            pin_entrega=pin,
            costo_envio=float(pedido.costo_envio or 0.0),
        )
        db.add(asig)
        db.flush()
    return asig


def notify_repartidores_pedido_listo(db: Session, pedido: Pedido) -> Optional[AsignacionDelivery]:
    """
    Cuando un pedido a domicilio pasa a LISTO:
    1) Garantiza AsignacionDelivery con PIN de 4 dígitos.
    2) Envía el PIN al cliente por WhatsApp.
    3) Envía aviso automático por WhatsApp a los repartidores disponibles correspondientes
       (FLOTA_SAAS o PROPIO del comercio).
    """
    if (pedido.tipo_entrega or "").upper() != "DOMICILIO":
        return None

    cfg = get_or_create_delivery_config(db, pedido.tenant_id)
    if cfg.modo_delivery == "DESACTIVADO":
        return None

    asig = ensure_delivery_assignment(db, pedido)
    if not asig:
        return None

    comercio = db.query(TenantComercio).filter(TenantComercio.id == pedido.tenant_id).first()
    suc = db.query(SucursalComercio).filter(SucursalComercio.id == pedido.sucursal_id).first()
    sym = (comercio.simbolo_moneda if comercio else "C$") or "C$"

    # 1. Enviar PIN al cliente
    if pedido.telefono_cliente and cfg.requiere_pin_entrega:
        try:
            send_whatsapp_text(
                pedido.telefono_cliente,
                f"¡Tu pedido *#{pedido.folio}* está *LISTO* y esperando repartidor!\n"
                f"Tu código PIN de entrega es: *{asig.pin_entrega}*\n"
                "_Entrégalo únicamente al repartidor cuando recibas tu pedido._",
            )
        except Exception:
            pass

    # 2. Notificar por WhatsApp a los repartidores activos correspondientes
    if cfg.notificar_whatsapp_repartidor and asig.estado_delivery == "PENDIENTE":
        if cfg.modo_delivery == "FLOTA_SAAS":
            rep_users = (
                db.query(Usuario, PerfilRepartidor)
                .join(PerfilRepartidor, PerfilRepartidor.usuario_id == Usuario.id)
                .filter(
                    Usuario.rol == "REPARTIDOR",
                    Usuario.activo.is_(True),
                    PerfilRepartidor.es_flota_saas.is_(True),
                    PerfilRepartidor.disponible.is_(True),
                )
                .all()
            )
        else:
            rep_users = (
                db.query(Usuario, PerfilRepartidor)
                .join(PerfilRepartidor, PerfilRepartidor.usuario_id == Usuario.id)
                .filter(
                    Usuario.rol == "REPARTIDOR",
                    Usuario.activo.is_(True),
                    Usuario.tenant_id == pedido.tenant_id,
                    PerfilRepartidor.disponible.is_(True),
                )
                .all()
            )

        pago_lbl = "PAGADO ONLINE" if pedido.estado_pago == "PAGADO" else f"COBRAR {sym}{pedido.total:.2f} ({pedido.metodo_pago})"
        aviso = (
            f"*Nuevo envío LISTO para recoger* (#{pedido.folio})\n"
            f"Comercio: *{comercio.nombre_comercial if comercio else 'Comercio'}* ({suc.nombre_sucursal if suc else ''})\n"
            f"Dirección cliente: {pedido.direccion_entrega or 'Ver en app'}\n"
            f"Envío: *{sym}{float(pedido.costo_envio or 0):.2f}* · {pago_lbl}\n\n"
            "Abre tu panel de Repartidor para tomar este pedido."
        )
        for _u, perf in rep_users[:5]:
            if perf.telefono_whatsapp:
                try:
                    send_whatsapp_text(perf.telefono_whatsapp, aviso)
                except Exception:
                    pass

    return asig


# =====================================================================
# ESQUEMAS PYDANTIC
# =====================================================================
class ConfigDeliveryUpdate(BaseModel):
    modo_delivery: str = Field(..., pattern="^(DESACTIVADO|FLOTA_SAAS|PROPIO)$")
    requiere_pin_entrega: bool = True
    notificar_whatsapp_repartidor: bool = True


class CrearRepartidorRequest(BaseModel):
    nombre_completo: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=5, max_length=150)
    password: str = Field(..., min_length=6, max_length=100)
    telefono_whatsapp: str = Field(..., min_length=6, max_length=30)
    vehiculo: str = Field(default="Moto", max_length=40)
    es_flota_saas: bool = True
    tenant_id: Optional[int] = None


class EntregarPedidoRequest(BaseModel):
    pin: Optional[str] = ""


class DisponibilidadRequest(BaseModel):
    disponible: bool


# =====================================================================
# ENDPOINTS DE CONFIGURACIÓN Y GESTIÓN DE REPARTIDORES
# =====================================================================
@router.put("/comercio/{tenant_id}/config")
def actualizar_config_delivery_comercio(
    tenant_id: int,
    payload: ConfigDeliveryUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Configura el modo de delivery del comercio y sincroniza permite_domicilio en sus sucursales."""
    verify_tenant_isolation(current_user, tenant_id)
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo administradores pueden cambiar la configuración de delivery.")

    cfg = get_or_create_delivery_config(db, tenant_id)
    cfg.modo_delivery = payload.modo_delivery
    cfg.requiere_pin_entrega = payload.requiere_pin_entrega
    cfg.notificar_whatsapp_repartidor = payload.notificar_whatsapp_repartidor

    # Sincronizar automáticamente todas las sucursales del comercio para que Bot, Menú Web y POS lo sepan al instante
    permite_dom = payload.modo_delivery != "DESACTIVADO"
    db.query(SucursalComercio).filter(SucursalComercio.tenant_id == tenant_id).update(
        {"permite_domicilio": permite_dom}
    )
    db.commit()
    return {
        "status": "ok",
        "modo_delivery": cfg.modo_delivery,
        "permite_domicilio": permite_dom,
        "requiere_pin_entrega": cfg.requiere_pin_entrega,
        "notificar_whatsapp_repartidor": cfg.notificar_whatsapp_repartidor,
    }


@router.get("/repartidores")
def listar_repartidores(
    tenant_id: Optional[int] = None,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista repartidores de Flota SaaS (SuperAdmin) o propios de un comercio."""
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Sin permisos.")

    q = (
        db.query(Usuario, PerfilRepartidor)
        .join(PerfilRepartidor, PerfilRepartidor.usuario_id == Usuario.id)
        .filter(Usuario.rol == "REPARTIDOR")
    )
    if current_user.rol == "COMERCIO_ADMIN":
        q = q.filter(Usuario.tenant_id == current_user.tenant_id)
    elif tenant_id is not None:
        q = q.filter(Usuario.tenant_id == tenant_id)

    rows = q.order_by(Usuario.id.desc()).all()
    return {
        "repartidores": [
            {
                "id": u.id,
                "nombre_completo": u.nombre_completo,
                "email": u.email,
                "activo": u.activo,
                "tenant_id": u.tenant_id,
                "telefono_whatsapp": p.telefono_whatsapp,
                "vehiculo": p.vehiculo,
                "es_flota_saas": p.es_flota_saas,
                "disponible": p.disponible,
            }
            for u, p in rows
        ]
    }


@router.post("/repartidores")
def crear_repartidor(
    payload: CrearRepartidorRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crea un usuario con rol REPARTIDOR (Flota SaaS compartida o propio del comercio)."""
    if current_user.rol not in ("SUPER_ADMIN", "COMERCIO_ADMIN"):
        raise HTTPException(status_code=403, detail="Sin permisos para crear repartidores.")

    email_clean = payload.email.strip().lower()
    if db.query(Usuario).filter(Usuario.email == email_clean).first():
        raise HTTPException(status_code=400, detail="Ya existe un usuario con ese correo.")

    if current_user.rol == "COMERCIO_ADMIN":
        es_flota = False
        target_tid = current_user.tenant_id
    else:
        es_flota = bool(payload.es_flota_saas)
        target_tid = None if es_flota else payload.tenant_id

    u = Usuario(
        tenant_id=target_tid,
        email=email_clean,
        password_hash=hash_password(payload.password),
        nombre_completo=payload.nombre_completo.strip(),
        rol="REPARTIDOR",
        activo=True,
    )
    db.add(u)
    db.flush()

    perf = PerfilRepartidor(
        usuario_id=u.id,
        telefono_whatsapp=clean_whatsapp_phone(payload.telefono_whatsapp) or payload.telefono_whatsapp.strip(),
        vehiculo=(payload.vehiculo or "Moto").strip()[:40],
        es_flota_saas=es_flota,
        disponible=True,
    )
    db.add(perf)
    db.commit()
    return {
        "status": "ok",
        "repartidor_id": u.id,
        "email": u.email,
        "es_flota_saas": es_flota,
    }


# =====================================================================
# PANEL MÓVIL DEL REPARTIDOR
# =====================================================================
def _serialize_delivery_card(
    p: Pedido,
    asig: Optional[AsignacionDelivery],
    comercio: Optional[TenantComercio],
    suc: Optional[SucursalComercio],
    cfg: Optional[ConfigDeliveryComercio],
) -> Dict[str, Any]:
    sym = (comercio.simbolo_moneda if comercio else "C$") or "C$"
    tel_clean = clean_whatsapp_phone(p.telefono_cliente or "")
    com_nom = comercio.nombre_comercial if comercio else "Comercio"
    wa_msg = quote(f"Hola {p.nombre_cliente or ''}, soy tu repartidor de {com_nom}. Voy en camino con tu pedido #{p.folio}.")
    maps_url = (
        f"https://www.google.com/maps/search/?api=1&query={quote(p.direccion_entrega or '')}"
        if not p.google_maps_link
        else p.google_maps_link
    )
    return {
        "pedido_id": p.id,
        "folio": p.folio,
        "estado_pedido": p.estado_pedido,
        "estado_delivery": asig.estado_delivery if asig else "PENDIENTE",
        "comercio_nombre": com_nom,
        "sucursal_nombre": suc.nombre_sucursal if suc else "",
        "sucursal_direccion": suc.direccion_fisica if suc else "",
        "cliente_nombre": p.nombre_cliente or "Cliente",
        "cliente_telefono": tel_clean,
        "direccion_entrega": p.direccion_entrega or "Sin dirección",
        "google_maps_url": maps_url,
        "whatsapp_cliente_url": f"https://wa.me/{tel_clean}?text={wa_msg}" if tel_clean else "",
        "simbolo_moneda": sym,
        "total": float(p.total or 0.0),
        "costo_envio": float(p.costo_envio or 0.0),
        "metodo_pago": p.metodo_pago or "EFECTIVO",
        "estado_pago": p.estado_pago or "PENDIENTE",
        "paga_con_monto": p.paga_con_monto,
        "cambio_a_entregar": p.cambio_a_entregar,
        "requiere_pin": bool(cfg.requiere_pin_entrega) if cfg else True,
        "repartidor_nombre": asig.repartidor_nombre if asig else None,
        "items_resumen": [f"{d.cantidad}x {d.nombre_plato}" for d in (p.detalles or [])],
    }


@router.get("/panel")
def obtener_panel_repartidor(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vista móvil del Repartidor: disponibles para recoger, en camino y entregados hoy."""
    if current_user.rol not in ("REPARTIDOR", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Repartidores.")

    perf = db.query(PerfilRepartidor).filter(PerfilRepartidor.usuario_id == current_user.id).first()
    es_flota = True if (current_user.rol == "SUPER_ADMIN" or (perf and perf.es_flota_saas)) else False

    # Determinar qué tenants puede atender este repartidor
    if es_flota:
        cfgs = db.query(ConfigDeliveryComercio).all()
        cfg_map = {c.tenant_id: c for c in cfgs}
        # Por defecto, si un comercio no tiene config aún y tiene sucursal con domicilio, usa FLOTA_SAAS
        all_tenants = db.query(TenantComercio).filter(TenantComercio.activo.is_(True)).all()
        allowed_tids = []
        for t in all_tenants:
            c = cfg_map.get(t.id)
            if c is None:
                c = get_or_create_delivery_config(db, t.id)
                cfg_map[t.id] = c
            if c.modo_delivery == "FLOTA_SAAS":
                allowed_tids.append(t.id)
        db.commit()
    else:
        allowed_tids = [current_user.tenant_id] if current_user.tenant_id else []
        cfg_map = {
            tid: get_or_create_delivery_config(db, tid) for tid in allowed_tids
        }
        db.commit()

    disponibles: List[Dict[str, Any]] = []
    en_camino: List[Dict[str, Any]] = []
    entregados_hoy: List[Dict[str, Any]] = []

    if allowed_tids:
        com_map = {c.id: c for c in db.query(TenantComercio).filter(TenantComercio.id.in_(allowed_tids)).all()}
        suc_map = {s.id: s for s in db.query(SucursalComercio).filter(SucursalComercio.tenant_id.in_(allowed_tids)).all()}

        # Pedidos a domicilio activos (LISTO o EN_PROCESO)
        activos = (
            db.query(Pedido)
            .filter(
                Pedido.tenant_id.in_(allowed_tids),
                Pedido.tipo_entrega == "DOMICILIO",
                Pedido.estado_pedido.in_(["LISTO", "EN_PROCESO"]),
            )
            .order_by(Pedido.id.desc())
            .all()
        )
        asig_map = {
            a.pedido_id: a
            for a in db.query(AsignacionDelivery)
            .filter(AsignacionDelivery.pedido_id.in_([p.id for p in activos] or [-1]))
            .all()
        }

        for p in activos:
            a = asig_map.get(p.id)
            if a and a.repartidor_usuario_id == current_user.id and a.estado_delivery == "EN_CAMINO":
                en_camino.append(_serialize_delivery_card(p, a, com_map.get(p.tenant_id), suc_map.get(p.sucursal_id), cfg_map.get(p.tenant_id)))
            elif (not a or (a.estado_delivery == "PENDIENTE" and not a.repartidor_usuario_id)) and p.estado_pedido == "LISTO":
                disponibles.append(_serialize_delivery_card(p, a, com_map.get(p.tenant_id), suc_map.get(p.sucursal_id), cfg_map.get(p.tenant_id)))

        # Entregados hoy por este repartidor
        hoy_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        asigs_hoy = (
            db.query(AsignacionDelivery)
            .filter(
                AsignacionDelivery.repartidor_usuario_id == current_user.id,
                AsignacionDelivery.estado_delivery == "ENTREGADO",
                AsignacionDelivery.entregado_en >= hoy_start,
            )
            .order_by(AsignacionDelivery.entregado_en.desc())
            .all()
        )
        if asigs_hoy:
            ped_hoy_map = {
                p.id: p for p in db.query(Pedido).filter(Pedido.id.in_([a.pedido_id for a in asigs_hoy])).all()
            }
            for a in asigs_hoy:
                p = ped_hoy_map.get(a.pedido_id)
                if p:
                    entregados_hoy.append(_serialize_delivery_card(p, a, com_map.get(p.tenant_id), suc_map.get(p.sucursal_id), cfg_map.get(p.tenant_id)))

    total_envios_hoy = round(sum(float(x.get("costo_envio") or 0.0) for x in entregados_hoy), 2)
    return {
        "repartidor": {
            "id": current_user.id,
            "nombre": current_user.nombre_completo,
            "telefono": perf.telefono_whatsapp if perf else "",
            "vehiculo": perf.vehiculo if perf else "Moto",
            "es_flota_saas": es_flota,
            "disponible": perf.disponible if perf else True,
        },
        "disponibles": disponibles,
        "en_camino": en_camino,
        "entregados_hoy": entregados_hoy,
        "ganancia_envios_hoy": total_envios_hoy,
    }


@router.patch("/disponibilidad")
def toggle_disponibilidad_repartidor(
    payload: DisponibilidadRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.rol not in ("REPARTIDOR", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo repartidores.")
    perf = db.query(PerfilRepartidor).filter(PerfilRepartidor.usuario_id == current_user.id).first()
    if perf:
        perf.disponible = payload.disponible
        db.commit()
    return {"status": "ok", "disponible": payload.disponible}


@router.post("/pedidos/{pedido_id}/tomar")
def tomar_pedido_delivery(
    pedido_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """El repartidor toma un pedido LISTO -> pasa a EN_CAMINO y notifica al cliente por WhatsApp."""
    if current_user.rol not in ("REPARTIDOR", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo repartidores pueden tomar entregas.")

    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido or pedido.tipo_entrega != "DOMICILIO":
        raise HTTPException(status_code=404, detail="Pedido a domicilio no encontrado.")

    asig = ensure_delivery_assignment(db, pedido)
    if asig.repartidor_usuario_id and asig.repartidor_usuario_id != current_user.id:
        raise HTTPException(status_code=409, detail="Este pedido ya fue tomado por otro repartidor.")

    perf = db.query(PerfilRepartidor).filter(PerfilRepartidor.usuario_id == current_user.id).first()
    asig.repartidor_usuario_id = current_user.id
    asig.repartidor_nombre = current_user.nombre_completo
    asig.repartidor_telefono = perf.telefono_whatsapp if perf else ""
    asig.estado_delivery = "EN_CAMINO"
    asig.tomado_en = _utcnow()

    cfg = get_or_create_delivery_config(db, pedido.tenant_id)
    comercio = db.query(TenantComercio).filter(TenantComercio.id == pedido.tenant_id).first()
    pin_txt = f"\nRecuerda darle tu código PIN de entrega: *{asig.pin_entrega}*" if cfg.requiere_pin_entrega else ""
    msg_cli = (
        f"¡Tu pedido *#{pedido.folio}* de *{comercio.nombre_comercial if comercio else 'tu comercio'}* "
        f"ya va *EN CAMINO* con nuestro repartidor *{current_user.nombre_completo}*!{pin_txt}"
    )

    if pedido.sesion_chat_id:
        db.add(
            MensajeChatWhatsApp(
                sesion_id=pedido.sesion_chat_id,
                tenant_id=pedido.tenant_id,
                remitente="BOT",
                tipo_contenido="TEXTO",
                contenido=msg_cli,
            )
        )
    db.commit()

    publish_tenant_event(
        pedido.tenant_id,
        "PEDIDO_ACTUALIZADO",
        {"pedido_id": pedido.id, "folio": pedido.folio, "repartidor": current_user.nombre_completo},
    )
    if pedido.telefono_cliente:
        try:
            send_whatsapp_text(pedido.telefono_cliente, msg_cli)
        except Exception:
            pass

    return {"status": "ok", "estado_delivery": "EN_CAMINO", "pedido_id": pedido.id}


@router.post("/pedidos/{pedido_id}/entregar")
def confirmar_entrega_con_pin(
    pedido_id: int,
    payload: EntregarPedidoRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Confirma la entrega validando el PIN de 4 dígitos, marca ENTREGADO y cierra la sesión del chat."""
    if current_user.rol not in ("REPARTIDOR", "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Solo repartidores pueden confirmar entregas.")

    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")

    asig = db.query(AsignacionDelivery).filter(AsignacionDelivery.pedido_id == pedido.id).first()
    if not asig or (asig.repartidor_usuario_id and asig.repartidor_usuario_id != current_user.id and current_user.rol != "SUPER_ADMIN"):
        raise HTTPException(status_code=403, detail="Este pedido no está asignado a tu cuenta.")

    cfg = get_or_create_delivery_config(db, pedido.tenant_id)
    if cfg.requiere_pin_entrega:
        pin_in = (payload.pin or "").strip()
        if pin_in != asig.pin_entrega:
            raise HTTPException(status_code=400, detail="Código PIN de entrega incorrecto. Pídele el PIN de 4 dígitos al cliente.")

    asig.estado_delivery = "ENTREGADO"
    asig.entregado_en = _utcnow()
    pedido.estado_pedido = "ENTREGADO"
    if pedido.estado_pago != "PAGADO":
        pedido.estado_pago = "PAGADO"

    # Cerrar sesión SIGUIENDO_PEDIDO para que el cliente pueda hacer nuevos pedidos después
    if pedido.sesion_chat_id:
        ses = db.query(SesionChatWhatsApp).filter(SesionChatWhatsApp.id == pedido.sesion_chat_id).first()
        if ses and ses.estado_conversacion == "SIGUIENDO_PEDIDO":
            ses.estado_conversacion = "EXPLORANDO_MENU"
        db.add(
            MensajeChatWhatsApp(
                sesion_id=pedido.sesion_chat_id,
                tenant_id=pedido.tenant_id,
                remitente="BOT",
                tipo_contenido="TEXTO",
                contenido=f"¡Tu pedido *#{pedido.folio}* ha sido entregado! Gracias por tu preferencia.",
            )
        )
    db.commit()

    try:
        from app.services.fidelidad_service import otorgar_sello_por_entrega

        if otorgar_sello_por_entrega(db, pedido):
            db.commit()
    except Exception:
        pass

    publish_tenant_event(
        pedido.tenant_id,
        "PEDIDO_ACTUALIZADO",
        {"pedido_id": pedido.id, "folio": pedido.folio, "estado_pedido": "ENTREGADO"},
    )
    if pedido.telefono_cliente:
        try:
            send_whatsapp_text(
                pedido.telefono_cliente,
                f"¡Tu pedido *#{pedido.folio}* ha sido entregado! Gracias por tu compra.",
            )
        except Exception:
            pass

    return {"status": "ok", "estado_pedido": "ENTREGADO", "estado_delivery": "ENTREGADO"}

