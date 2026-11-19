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
