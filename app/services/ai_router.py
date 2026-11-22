"""
Enrutador Multi-IA ligero para modo Híbrido del Bot de WhatsApp.
Soporta Gemini, Groq, OpenRouter y OpenAI según AI_PROVIDER_PRIORITY.
Si no hay ninguna API Key configurada, retorna None de forma inmediata para que
el motor use el flujo determinista (Menú Guiado costo cero).
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_available_ai_providers() -> List[str]:
    """Devuelve la lista ordenada de proveedores IA que tienen API Key configurada."""
    key_map = {
        "gemini": settings.GEMINI_API_KEY,
        "groq": settings.GROQ_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
        "openai": settings.OPENAI_API_KEY,
    }
    return [
        prov
        for prov in settings.AI_PROVIDER_PRIORITY
        if (key_map.get(prov) or "").strip()
    ]


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    """Extrae el primer objeto JSON válido de una respuesta de LLM."""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
    return None


def _call_openai_compatible(
    url: str, api_key: str, model: str, system_prompt: str, user_prompt: str
) -> Optional[str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 350,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        if 200 <= resp.status_code < 300:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    return None


def _call_gemini(api_key: str, model: str, system_prompt: str, user_prompt: str) -> Optional[str]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 350},
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(url, json=payload)
        if 200 <= resp.status_code < 300:
            data = resp.json()
            candidates = data.get("candidates") or []
            if candidates:
                parts = candidates[0].get("content", {}).get("parts") or []
                if parts:
                    return parts[0].get("text", "")
    return None


def interpret_natural_order_message(
    user_message: str,
    comercio_nombre: str,
    giro: str,
    catalogo_resumido: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Interpreta un mensaje libre del cliente usando la primera IA disponible.
    Retorna un diccionario estructurado seguro (nunca calcula precios finales):
    {
      "accion": "AGREGAR" | "CONSULTA" | "CONFIRMAR" | "HUMANO" | "DESCONOCIDO",
      "items": [{"plato_id": 12, "cantidad": 2, "sin_ingredientes": ["cebolla"], "notas": ""}],
      "respuesta_breve": "Texto corto si accion es CONSULTA"
    }
    """
    providers = get_available_ai_providers()
    if not providers:
        return None

    system_prompt = (
        f"Eres el asistente de pedidos por WhatsApp de '{comercio_nombre}' (giro: {giro}). "
        "Tu unica tarea es analizar el mensaje del cliente y devolver EXCLUSIVAMENTE un objeto JSON valido, "
        "sin texto adicional ni markdown. "
        "Solo puedes usar IDs exactos (plato_id) que existan en el catalogo entregado. "
        "Formato JSON obligatorio: "
        '{"accion":"AGREGAR|CONSULTA|CONFIRMAR|HUMANO|DESCONOCIDO",'
        '"items":[{"plato_id":0,"cantidad":1,"sin_ingredientes":[],"notas":""}],'
        '"respuesta_breve":""}'
    )

    cat_json = json.dumps(catalogo_resumido[:40], ensure_ascii=False)
    user_prompt = f"Catalogo disponible:\n{cat_json}\n\nMensaje del cliente:\n{user_message}"

    for prov in providers:
        try:
            raw_text = None
            if prov == "gemini":
                raw_text = _call_gemini(
                    settings.GEMINI_API_KEY.strip(),
                    settings.GEMINI_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "groq":
                raw_text = _call_openai_compatible(
                    "https://api.groq.com/openai/v1/chat/completions",
                    settings.GROQ_API_KEY.strip(),
                    settings.GROQ_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "openrouter":
                raw_text = _call_openai_compatible(
                    "https://openrouter.ai/api/v1/chat/completions",
                    settings.OPENROUTER_API_KEY.strip(),
                    settings.OPENROUTER_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "openai":
                raw_text = _call_openai_compatible(
                    "https://api.openai.com/v1/chat/completions",
                    settings.OPENAI_API_KEY.strip(),
                    settings.OPENAI_MODEL,
                    system_prompt,
                    user_prompt,
                )

            parsed = _extract_json_block(raw_text or "")
            if parsed and "accion" in parsed:
                parsed["_provider"] = prov
                return parsed
        except Exception as exc:
            logger.warning("Proveedor IA %s falló, probando siguiente: %s", prov, exc)
            continue

    return None


def _run_ai_json_prompt(system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
    """Ejecuta un prompt estructurado en cascada por los proveedores IA configurados."""
    providers = get_available_ai_providers()
    if not providers:
        return None
    for prov in providers:
        try:
            raw_text = None
            if prov == "gemini":
                raw_text = _call_gemini(
                    settings.GEMINI_API_KEY.strip(),
                    settings.GEMINI_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "groq":
                raw_text = _call_openai_compatible(
                    "https://api.groq.com/openai/v1/chat/completions",
                    settings.GROQ_API_KEY.strip(),
                    settings.GROQ_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "openrouter":
                raw_text = _call_openai_compatible(
                    "https://openrouter.ai/api/v1/chat/completions",
                    settings.OPENROUTER_API_KEY.strip(),
                    settings.OPENROUTER_MODEL,
                    system_prompt,
                    user_prompt,
                )
            elif prov == "openai":
                raw_text = _call_openai_compatible(
                    "https://api.openai.com/v1/chat/completions",
                    settings.OPENAI_API_KEY.strip(),
                    settings.OPENAI_MODEL,
                    system_prompt,
                    user_prompt,
                )
            parsed = _extract_json_block(raw_text or "")
            if parsed:
                parsed["_provider"] = prov
                return parsed
        except Exception as exc:
            logger.warning("Proveedor IA %s falló en prompt estructurado: %s", prov, exc)
            continue
    return None


def validate_and_normalize_address_ai(
    raw_address: str,
    zonas_disponibles: List[Dict[str, Any]],
    sucursal_nombre: str,
) -> Optional[Dict[str, Any]]:
    """
    Valida si una dirección de entrega escrita en WhatsApp es suficientemente clara
    para un repartidor, la limpia y detecta si corresponde a alguna zona de envío configurada.
    Retorna:
      {"valida": bool, "direccion_limpia": str, "zona_id": Optional[int], "pregunta_aclaracion": str}
    """
    system_prompt = (
        f"Eres un validador de direcciones de entrega a domicilio en Latinoamerica para la sucursal '{sucursal_nombre}'. "
        "Responde EXCLUSIVAMENTE con un JSON valido sin markdown. "
        "Reglas:\n"
        "- En Latinoamerica las direcciones por referencias (ej: 'de la rotonda 2 cuadras al sur casa porton verde', 'barrio X casa 14') SON VALIDAS.\n"
        "- Marca valida=false SOLO si es demasiado vaga o incompleta para un repartidor (ej: 'en mi casa', 'por el parque', 'aqui', 'donde siempre', 'no se').\n"
        "- Si coincide con alguna zona de la lista, pon su zona_id exacto; si no, pon null.\n"
        "- Si valida=false, escribe una pregunta_aclaracion corta, amable y natural pidiendo calle/barrio/referencia.\n"
        'Formato JSON exacto: {"valida":true,"direccion_limpia":"","zona_id":null,"pregunta_aclaracion":""}'
    )
    user_prompt = (
        f"Zonas configuradas: {json.dumps(zonas_disponibles, ensure_ascii=False)}\n"
        f"Direccion escrita por el cliente: {raw_address}"
    )
    parsed = _run_ai_json_prompt(system_prompt, user_prompt)
    if parsed and "valida" in parsed:
        return parsed
    return None


def detect_escalation_or_complaint_ai(
    user_message: str, comercio_nombre: str
) -> Optional[Dict[str, Any]]:
    """
    Detecta si un mensaje corresponde a un reclamo, queja, problema con un pedido anterior,
    solicitud fiscal/especial o frustración que deba transferirse a un humano (HUMANO_ACTIVO).
    Retorna:
      {"requiere_humano": bool, "motivo": str, "respuesta_empatica": str}
    """
    system_prompt = (
        f"Eres un filtro de atencion al cliente para '{comercio_nombre}'. "
        "Analiza el mensaje del cliente y decide si requiere intervencion de una persona real (reclamo, queja, "
        "pedido equivocado/frio/incompleto, cobro incorrecto, factura fiscal/RUC, evento grande, devolucion o molestia). "
        "NO marques requiere_humano=true si solo esta haciendo un pedido normal, eligiendo direccion o preguntando algo simple. "
        "Responde EXCLUSIVAMENTE con JSON valido sin markdown: "
        '{"requiere_humano":false,"motivo":"","respuesta_empatica":""}'
    )
    user_prompt = f"Mensaje del cliente: {user_message}"
    parsed = _run_ai_json_prompt(system_prompt, user_prompt)
    if parsed and "requiere_humano" in parsed:
        return parsed
    return None

