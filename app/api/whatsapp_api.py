"""
API de WhatsApp:
- Webhook Evolution API (Nivel 1 QR — ideal para testeo y operación inicial)
- Webhook Meta Cloud API Oficial (Nivel 2)
- Simulador Interactivo autenticado para probar el Bot en vivo desde el Panel Web
  (alternando entre Menú Guiado Sin IA y Modo Híbrido con IA)
- Diagnóstico de estado de conexión y URLs de Webhook
"""
from collections import OrderedDict
import threading
import time
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_comercio_admin, verify_tenant_isolation
from app.models.saas_models import (
    SesionChatWhatsApp,
    SucursalComercio,
    Usuario,
)
from app.services.ai_router import get_available_ai_providers, get_last_ai_error
from app.services.whatsapp_bot_service import process_incoming_whatsapp_message
from app.services.whatsapp_provider import (
    clean_whatsapp_phone,
    create_or_connect_evolution_qr,
    get_evolution_connection_status,
    logout_evolution_instance,
    send_whatsapp_media,
    send_whatsapp_text,
    verify_meta_signature,
)

router = APIRouter(prefix="/api/v1/whatsapp", tags=["Bot WhatsApp y Webhooks"])

# Deduplicación en memoria de IDs de mensajes entrantes (evita respuestas dobles
# cuando Evolution API dispara webhook global + webhook de instancia o reintenta).
_PROCESSED_MSG_IDS: "OrderedDict[str, float]" = OrderedDict()
_PROCESSED_LOCK = threading.Lock()
_DEDUP_TTL_SECONDS = 300.0
_DEDUP_MAX_SIZE = 1000


def _is_duplicate_message(msg_id: Optional[str]) -> bool:
    if not msg_id:
        return False
    now = time.monotonic()
    with _PROCESSED_LOCK:
        # Limpiar expirados
        while _PROCESSED_MSG_IDS:
            oldest_k, oldest_t = next(iter(_PROCESSED_MSG_IDS.items()))
            if now - oldest_t > _DEDUP_TTL_SECONDS or len(_PROCESSED_MSG_IDS) > _DEDUP_MAX_SIZE:
                _PROCESSED_MSG_IDS.popitem(last=False)
            else:
                break
        if msg_id in _PROCESSED_MSG_IDS:
            return True
        _PROCESSED_MSG_IDS[msg_id] = now
        return False


class SimuladorMensajeRequest(BaseModel):
    sucursal_id: int
    mensaje: str
    telefono_simulado: str = "50588001234"
    nombre_cliente: str = "Cliente Prueba Panel"
    modo_bot: str = "SOLO_IA"  # SOLO_IA | MENU_GUIADO | HIBRIDO_IA


class SimuladorReiniciarRequest(BaseModel):
    sucursal_id: int
    telefono_simulado: str = "50588001234"


def _default_webhook_mode() -> str:
    """Si hay alguna API Key de IA configurada usa SOLO_IA (prueba activa); si no, MENU_GUIADO."""
    return "SOLO_IA" if get_available_ai_providers() else "MENU_GUIADO"


# =====================================================================
# 1. WEBHOOK EVOLUTION API (NIVEL 1 — QR)
# =====================================================================
@router.post("/webhook/evolution")
async def webhook_evolution_api(request: Request, db: Session = Depends(get_db)):
    """
    Recibe eventos de Evolution API (v1/v2), especialmente 'messages.upsert'.
    Extrae remitente y texto, ejecuta el motor del bot y responde por Evolution API.
    """
    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido en webhook Evolution.")

    event = str(payload.get("event") or "").lower()
    if event and "messages.upsert" not in event and event != "messages_upsert":
        return {"status": "ignored", "event": event}

    data = payload.get("data") or payload
    key = data.get("key") or {}
    if key.get("fromMe"):
        return {"status": "ignored", "reason": "fromMe"}

    msg_id = str(key.get("id") or data.get("id") or "").strip()
    if msg_id and _is_duplicate_message(f"evo:{msg_id}"):
        return {"status": "ignored", "reason": "duplicate_message_id"}

    remote_jid = str(key.get("remoteJid") or data.get("from") or "")
    if "@g.us" in remote_jid:
        return {"status": "ignored", "reason": "group_message"}

    telefono = clean_whatsapp_phone(remote_jid)
    if not telefono:
        return {"status": "ignored", "reason": "no_phone"}

    msg_obj = data.get("message") or {}
    texto = (
        msg_obj.get("conversation")
        or (msg_obj.get("extendedTextMessage") or {}).get("text")
        or (msg_obj.get("buttonsResponseMessage") or {}).get("selectedDisplayText")
        or (msg_obj.get("listResponseMessage") or {}).get("title")
        or ""
    ).strip()

    if not texto:
        return {"status": "ignored", "reason": "empty_or_non_text"}

    push_name = str(data.get("pushName") or "Cliente WhatsApp").strip()
    result = process_incoming_whatsapp_message(
        db=db,
        telefono_raw=telefono,
        texto_raw=texto,
        nombre_cliente=push_name,
        modo_bot=_default_webhook_mode(),
    )

    delivery = None
    if result.get("reply_image_url"):
        delivery = send_whatsapp_media(telefono, result["reply_image_url"], result.get("reply_text") or "")
    elif result.get("reply_text"):
        delivery = send_whatsapp_text(telefono, result["reply_text"])

    return {
        "status": "ok",
        "sesion_id": result.get("sesion_id"),
        "modo_atencion": result.get("modo_atencion"),
        "pedido_creado": result.get("pedido_creado"),
        "delivery": delivery,
    }


# =====================================================================
# 2. WEBHOOK META CLOUD API OFICIAL (NIVEL 2)
# =====================================================================
@router.get("/webhook/meta")
def verify_meta_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
):
    """Endpoint de verificación inicial exigido por Meta Developers."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_META_VERIFY_TOKEN:
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Token de verificación de Meta inválido.")


@router.post("/webhook/meta")
async def webhook_meta_cloud(request: Request, db: Session = Depends(get_db)):
    """Recibe mensajes entrantes de Meta WhatsApp Cloud API y responde automáticamente."""
    raw_body = await request.body()
    sig_header = request.headers.get("X-Hub-Signature-256")
    if not verify_meta_signature(raw_body, sig_header):
        raise HTTPException(status_code=403, detail="Firma HMAC de Meta inválida.")

    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido en webhook Meta.")

    processed = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = value.get("contacts") or []
            contact_name = (
                (contacts[0].get("profile") or {}).get("name") if contacts else "Cliente WhatsApp"
            )
            for msg in value.get("messages") or []:
                meta_msg_id = str(msg.get("id") or "").strip()
                if meta_msg_id and _is_duplicate_message(f"meta:{meta_msg_id}"):
                    continue
                telefono = clean_whatsapp_phone(str(msg.get("from") or ""))
                msg_type = msg.get("type")
                texto = ""
                if msg_type == "text":
                    texto = (msg.get("text") or {}).get("body") or ""
                elif msg_type == "interactive":
                    inter = msg.get("interactive") or {}
                    texto = (
                        (inter.get("button_reply") or {}).get("title")
                        or (inter.get("list_reply") or {}).get("title")
                        or ""
                    )
                if not telefono or not texto.strip():
                    continue

                result = process_incoming_whatsapp_message(
                    db=db,
                    telefono_raw=telefono,
                    texto_raw=texto.strip(),
                    nombre_cliente=contact_name or "Cliente WhatsApp",
                    modo_bot=_default_webhook_mode(),
                )
                if result.get("reply_text"):
                    send_whatsapp_text(telefono, result["reply_text"])
                processed += 1

    return {"status": "ok", "processed": processed}


# =====================================================================
# 3. SIMULADOR INTERACTIVO EN EL PANEL WEB + ESTADO DE CONEXIÓN
# =====================================================================
@router.post("/simulador/mensaje")
def simulador_enviar_mensaje(
    payload: SimuladorMensajeRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Permite probar el Bot de WhatsApp en vivo desde el panel web sin depender de Evolution/Meta.
    Guarda la conversación y pedidos reales en la BD para verlos al instante en el Kanban.
    """
    suc = (
        db.query(SucursalComercio)
        .filter(SucursalComercio.id == payload.sucursal_id)
        .first()
    )
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    verify_tenant_isolation(current_user, suc.tenant_id)

    if (
        current_user.rol == "SUCURSAL_STAFF"
        and current_user.sucursal_id
        and current_user.sucursal_id != suc.id
    ):
        raise HTTPException(
            status_code=403,
            detail="Solo puedes probar el bot en tu sucursal asignada.",
        )

    result = process_incoming_whatsapp_message(
        db=db,
        telefono_raw=payload.telefono_simulado,
        texto_raw=payload.mensaje,
        nombre_cliente=payload.nombre_cliente,
        forced_sucursal_id=suc.id,
        modo_bot=payload.modo_bot,
    )
    return {"status": "ok", "ai_last_error": get_last_ai_error(), **result}


@router.post("/simulador/reiniciar")
def simulador_reiniciar_chat(
    payload: SimuladorReiniciarRequest,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cierra la sesión simulada actual para iniciar una conversación desde cero."""
    suc = (
        db.query(SucursalComercio)
        .filter(SucursalComercio.id == payload.sucursal_id)
        .first()
    )
    if not suc:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    verify_tenant_isolation(current_user, suc.tenant_id)

    tel = clean_whatsapp_phone(payload.telefono_simulado) or "50588001234"
    sesiones = (
        db.query(SesionChatWhatsApp)
        .filter(
            SesionChatWhatsApp.sucursal_id == suc.id,
            SesionChatWhatsApp.telefono_cliente == tel,
            SesionChatWhatsApp.activa.is_(True),
        )
        .all()
    )
    for s in sesiones:
        s.activa = False
    db.commit()
    return {"status": "ok", "cerradas": len(sesiones)}


@router.get("/estado-conexion")
def estado_conexion_whatsapp(
    check_evolution: bool = False,
    current_user: Usuario = Depends(get_current_user),
):
    """Devuelve configuración activa del proveedor WhatsApp, URLs de webhook e IAs disponibles."""
    base = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
    evolution_status = (
        get_evolution_connection_status()
        if check_evolution
        else {
            "configured": bool((settings.WHATSAPP_EVOLUTION_API_KEY or "").strip()),
            "base_url": settings.WHATSAPP_EVOLUTION_BASE_URL,
            "instance": settings.WHATSAPP_EVOLUTION_INSTANCE,
        }
    )
    return {
        "provider": settings.WHATSAPP_PROVIDER,
        "bot_public_number": settings.WHATSAPP_BOT_PUBLIC_NUMBER,
        "ai_providers_available": get_available_ai_providers(),
        "webhook_evolution_url": f"{base}/api/v1/whatsapp/webhook/evolution",
        "webhook_meta_url": f"{base}/api/v1/whatsapp/webhook/meta",
        "meta_configured": bool(
            (settings.WHATSAPP_META_ACCESS_TOKEN or "").strip()
            and (settings.WHATSAPP_META_PHONE_NUMBER_ID or "").strip()
        ),
        "evolution": evolution_status,
    }


@router.post("/evolution/conectar-qr")
def conectar_evolution_qr(
    current_user: Usuario = Depends(require_comercio_admin),
):
    """
    Crea/conecta la instancia en Evolution API y devuelve el QR real de WhatsApp
    para escanearlo directamente desde el panel web.
    """
    base = (settings.BASE_URL or "http://localhost:8000").rstrip("/")
    # Si FastAPI corre en localhost y Evolution en Docker, host.docker.internal permite llegar al host
    webhook_url = (
        base.replace("http://localhost:", "http://host.docker.internal:")
        .replace("http://127.0.0.1:", "http://host.docker.internal:")
        + "/api/v1/whatsapp/webhook/evolution"
    )
    res = create_or_connect_evolution_qr(webhook_url)
    if not res.get("ok"):
        raise HTTPException(status_code=502, detail=res.get("detail") or "Error conectando con Evolution API.")
    return res


@router.post("/evolution/desconectar")
def desconectar_evolution(
    current_user: Usuario = Depends(require_comercio_admin),
):
    """Cierra la sesión de WhatsApp vinculada en Evolution API."""
    return logout_evolution_instance()

