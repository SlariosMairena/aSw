"""
Adaptador unificado de envío y estado para WhatsApp:
- Nivel 1: Evolution API (escaneo de QR con número normal, ideal para testeo y operación inicial)
- Nivel 2: Meta WhatsApp Cloud API oficial
- Modo Simulado: si no hay credenciales externas configuradas, registra el mensaje sin romper el flujo local.
"""
import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def clean_whatsapp_phone(raw_phone: str) -> str:
    """Limpia JID de Evolution ('5215512345678@s.whatsapp.net') o formatos con '+' y espacios."""
    if not raw_phone:
        return ""
    base = raw_phone.split("@", 1)[0]
    return "".join(ch for ch in base if ch.isdigit())


def verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Valida X-Hub-Signature-256 de Meta Cloud API si WHATSAPP_META_APP_SECRET está configurado."""
    secret = (settings.WHATSAPP_META_APP_SECRET or "").strip()
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, received)


def send_whatsapp_text(telefono: str, texto: str) -> Dict[str, Any]:
    """
    Envía un mensaje de texto saliente al cliente por el proveedor configurado.
    Nunca lanza excepciones que bloqueen el panel o el webhook si el proveedor externo está apagado.
    """
    phone = clean_whatsapp_phone(telefono)
    if not phone or not texto or not texto.strip():
        return {"sent": False, "provider": "none", "detail": "Teléfono o mensaje vacío."}

    provider = (settings.WHATSAPP_PROVIDER or "evolution_qr").strip().lower()

    # 1. Nivel 1: Evolution API (QR)
    if provider == "evolution_qr":
        api_key = (settings.WHATSAPP_EVOLUTION_API_KEY or "").strip()
        base_url = (settings.WHATSAPP_EVOLUTION_BASE_URL or "").strip().rstrip("/")
        instance = (settings.WHATSAPP_EVOLUTION_INSTANCE or "").strip()
        if not api_key or not base_url or not instance:
            return {
                "sent": False,
                "provider": "evolution_qr",
                "mode": "simulado",
                "detail": "WHATSAPP_EVOLUTION_API_KEY no configurada (modo simulación local activo).",
            }

        url = f"{base_url}/message/sendText/{instance}"
        headers = {"apikey": api_key, "Content-Type": "application/json"}
        payload = {"number": phone, "text": texto}
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if 200 <= resp.status_code < 300:
                    return {"sent": True, "provider": "evolution_qr", "status_code": resp.status_code}
                logger.warning("Evolution API sendText devolvió %s: %s", resp.status_code, resp.text[:300])
                return {
                    "sent": False,
                    "provider": "evolution_qr",
                    "status_code": resp.status_code,
                    "detail": resp.text[:250],
                }
        except Exception as exc:
            logger.warning("Error enviando por Evolution API: %s", exc)
            return {"sent": False, "provider": "evolution_qr", "detail": str(exc)}

    # 2. Nivel 2: Meta Cloud API Oficial
    if provider == "meta_cloud":
        token = (settings.WHATSAPP_META_ACCESS_TOKEN or "").strip()
        phone_id = (settings.WHATSAPP_META_PHONE_NUMBER_ID or "").strip()
        if not token or not phone_id:
            return {
                "sent": False,
                "provider": "meta_cloud",
                "mode": "simulado",
                "detail": "WHATSAPP_META_ACCESS_TOKEN o PHONE_NUMBER_ID no configurados.",
            }

        url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": texto},
        }
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.post(url, headers=headers, json=payload)
                if 200 <= resp.status_code < 300:
                    return {"sent": True, "provider": "meta_cloud", "status_code": resp.status_code}
                logger.warning("Meta Cloud API devolvió %s: %s", resp.status_code, resp.text[:300])
                return {
                    "sent": False,
                    "provider": "meta_cloud",
                    "status_code": resp.status_code,
                    "detail": resp.text[:250],
                }
        except Exception as exc:
            logger.warning("Error enviando por Meta Cloud API: %s", exc)
            return {"sent": False, "provider": "meta_cloud", "detail": str(exc)}

    return {"sent": False, "provider": provider, "mode": "simulado", "detail": "Proveedor en modo simulado."}


def get_evolution_connection_status() -> Dict[str, Any]:
    """Consulta el estado de conexión de la instancia en Evolution API (si está configurada)."""
    api_key = (settings.WHATSAPP_EVOLUTION_API_KEY or "").strip()
    base_url = (settings.WHATSAPP_EVOLUTION_BASE_URL or "").strip().rstrip("/")
    instance = (settings.WHATSAPP_EVOLUTION_INSTANCE or "").strip()
    if not api_key or not base_url or not instance:
        return {
            "configured": False,
            "connected": False,
            "state": "NO_CONFIGURADO",
            "detail": "Configura WHATSAPP_EVOLUTION_BASE_URL, WHATSAPP_EVOLUTION_API_KEY y WHATSAPP_EVOLUTION_INSTANCE en tu .env",
        }

    url = f"{base_url}/instance/connectionState/{instance}"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url, headers={"apikey": api_key})
            if 200 <= resp.status_code < 300:
                data = resp.json()
                state = (
                    data.get("instance", {}).get("state")
                    or data.get("state")
                    or "UNKNOWN"
                )
                return {
                    "configured": True,
                    "connected": str(state).lower() == "open",
                    "state": str(state).upper(),
                    "raw": data,
                }
            return {
                "configured": True,
                "connected": False,
                "state": f"HTTP_{resp.status_code}",
                "detail": resp.text[:200],
            }
    except Exception as exc:
        return {
            "configured": True,
            "connected": False,
            "state": "SIN_CONEXION",
            "detail": f"No se pudo conectar a {base_url}: {exc}",
        }


def create_or_connect_evolution_qr(webhook_url: str) -> Dict[str, Any]:
    """
    Crea la instancia en Evolution API v2 (si aún no existe), configura el webhook
    MESSAGES_UPSERT y solicita el código QR real (base64) para escanear con WhatsApp.
    """
    api_key = (settings.WHATSAPP_EVOLUTION_API_KEY or "").strip()
    base_url = (settings.WHATSAPP_EVOLUTION_BASE_URL or "").strip().rstrip("/")
    instance = (settings.WHATSAPP_EVOLUTION_INSTANCE or "saas_bot_principal").strip()
    if not api_key or not base_url or not instance:
        return {
            "ok": False,
            "detail": "Faltan WHATSAPP_EVOLUTION_BASE_URL, WHATSAPP_EVOLUTION_API_KEY o WHATSAPP_EVOLUTION_INSTANCE en .env",
        }

    headers = {"apikey": api_key, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=15.0) as client:
            # 1. Revisar si ya está conectada
            st_resp = client.get(f"{base_url}/instance/connectionState/{instance}", headers=headers)
            if st_resp.status_code == 200:
                st_data = st_resp.json()
                state = str(st_data.get("instance", {}).get("state") or st_data.get("state") or "").lower()
                if state == "open":
                    return {
                        "ok": True,
                        "connected": True,
                        "state": "OPEN",
                        "instance": instance,
                        "detail": "WhatsApp ya está conectado y activo en esta instancia.",
                    }

            # 2. Intentar crear la instancia con QR + Webhook (si ya existe devolverá 403/409 y seguimos a /connect)
            create_payload = {
                "instanceName": instance,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS",
                "webhook": {
                    "url": webhook_url,
                    "byEvents": False,
                    "base64": False,
                    "events": ["MESSAGES_UPSERT"],
                },
            }
            c_resp = client.post(f"{base_url}/instance/create", headers=headers, json=create_payload)
            if 200 <= c_resp.status_code < 300:
                c_data = c_resp.json()
                qr_b64 = (
                    (c_data.get("qrcode") or {}).get("base64")
                    or c_data.get("base64")
                )
                pairing_code = (c_data.get("qrcode") or {}).get("pairingCode") or c_data.get("pairingCode")
                if qr_b64:
                    if not qr_b64.startswith("data:image"):
                        qr_b64 = f"data:image/png;base64,{qr_b64}"
                    return {
                        "ok": True,
                        "connected": False,
                        "state": "QRCODE",
                        "instance": instance,
                        "qr_base64": qr_b64,
                        "pairing_code": pairing_code,
                        "detail": "Escanea este QR desde WhatsApp -> Dispositivos vinculados.",
                    }

            # 3. Asegurar webhook configurado en instancia existente
            client.post(
                f"{base_url}/webhook/set/{instance}",
                headers=headers,
                json={
                    "webhook": {
                        "enabled": True,
                        "url": webhook_url,
                        "webhookByEvents": False,
                        "webhookBase64": False,
                        "events": ["MESSAGES_UPSERT"],
                    }
                },
            )

            # 4. Solicitar QR fresco en /instance/connect/{instance}
            conn_resp = client.get(f"{base_url}/instance/connect/{instance}", headers=headers)
            if 200 <= conn_resp.status_code < 300:
                conn_data = conn_resp.json()
                qr_b64 = conn_data.get("base64") or (conn_data.get("qrcode") or {}).get("base64")
                pairing_code = conn_data.get("pairingCode") or (conn_data.get("qrcode") or {}).get("pairingCode")
                if qr_b64:
                    if not qr_b64.startswith("data:image"):
                        qr_b64 = f"data:image/png;base64,{qr_b64}"
                    return {
                        "ok": True,
                        "connected": False,
                        "state": "QRCODE",
                        "instance": instance,
                        "qr_base64": qr_b64,
                        "pairing_code": pairing_code,
                        "detail": "Escanea este QR desde WhatsApp -> Dispositivos vinculados.",
                    }
                if str((conn_data.get("instance") or {}).get("state") or "").lower() == "open":
                    return {
                        "ok": True,
                        "connected": True,
                        "state": "OPEN",
                        "instance": instance,
                        "detail": "WhatsApp conectado correctamente.",
                    }
                return {
                    "ok": False,
                    "detail": f"Evolution respondió sin QR aún. Intenta de nuevo en 3 segundos. ({str(conn_data)[:180]})",
                }

            return {
                "ok": False,
                "detail": f"Evolution API devolvió HTTP {conn_resp.status_code}: {conn_resp.text[:200]}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "detail": f"No se pudo conectar a Evolution API en {base_url}. Verifica que Docker Desktop y Evolution API estén encendidos. ({exc})",
        }


def logout_evolution_instance() -> Dict[str, Any]:
    """Desvincula la sesión actual de WhatsApp en Evolution API."""
    api_key = (settings.WHATSAPP_EVOLUTION_API_KEY or "").strip()
    base_url = (settings.WHATSAPP_EVOLUTION_BASE_URL or "").strip().rstrip("/")
    instance = (settings.WHATSAPP_EVOLUTION_INSTANCE or "saas_bot_principal").strip()
    if not api_key or not base_url:
        return {"ok": False, "detail": "Evolution API no configurada."}
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.delete(
                f"{base_url}/instance/logout/{instance}",
                headers={"apikey": api_key},
            )
            return {"ok": 200 <= resp.status_code < 300, "status_code": resp.status_code}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}

