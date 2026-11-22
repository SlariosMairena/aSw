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
_LAST_AI_ERROR: str = ""


def get_last_ai_error() -> str:
    return _LAST_AI_ERROR


def _clean_key(raw: str) -> str:
    return (raw or "").strip().strip('"').strip("'")


def get_available_ai_providers() -> List[str]:
    """Devuelve la lista ordenada de proveedores IA que tienen API Key configurada."""
    key_map = {
        "groq": _clean_key(settings.GROQ_API_KEY),
        "cerebras": _clean_key(settings.CEREBRAS_API_KEY),
        "gemini": _clean_key(settings.GEMINI_API_KEY),
        "sambanova": _clean_key(settings.SAMBANOVA_API_KEY),
        "mistral": _clean_key(settings.MISTRAL_API_KEY),
        "github": _clean_key(settings.GITHUB_MODELS_TOKEN),
        "openrouter": _clean_key(settings.OPENROUTER_API_KEY),
        "openai": _clean_key(settings.OPENAI_API_KEY),
    }
    return [
        prov
        for prov in settings.AI_PROVIDER_PRIORITY
        if key_map.get(prov)
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
    url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 750,
    provider_label: str = "openai",
) -> Optional[str]:
    global _LAST_AI_ERROR
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        with httpx.Client(timeout=18.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if 200 <= resp.status_code < 300:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # Si el proveedor rechaza response_format, reintentar sin response_format
            if resp.status_code == 400 and "response_format" in resp.text.lower():
                payload.pop("response_format", None)
                resp2 = client.post(url, headers=headers, json=payload)
                if 200 <= resp2.status_code < 300:
                    return resp2.json()["choices"][0]["message"]["content"]
                resp = resp2
            msg = ""
            try:
                msg = (resp.json().get("error") or {}).get("message") or ""
            except Exception:
                msg = resp.text[:90]
            _LAST_AI_ERROR = f"{provider_label}({model}):{resp.status_code} {msg[:60]}"
            logger.warning("IA %s falló: %s", provider_label, _LAST_AI_ERROR)
    except Exception as exc:
        _LAST_AI_ERROR = f"{provider_label}:net {str(exc)[:60]}"
        logger.warning("IA %s error red: %s", provider_label, exc)
    return None


_CACHED_GEMINI_MODELS: List[str] = []


def _discover_gemini_models(clean_api: str) -> List[str]:
    """Consulta /v1beta/models para obtener los modelos Gemini flash realmente disponibles para esta API Key."""
    global _CACHED_GEMINI_MODELS
    if _CACHED_GEMINI_MODELS:
        return _CACHED_GEMINI_MODELS
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_api}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            if 200 <= resp.status_code < 300:
                found: List[str] = []
                for m in resp.json().get("models") or []:
                    methods = m.get("supportedGenerationMethods") or []
                    name = str(m.get("name") or "").replace("models/", "")
                    if "generateContent" in methods and "gemini" in name.lower() and "embedding" not in name.lower():
                        found.append(name)
                # Priorizar modelos flash recientes y estables
                def _score(n: str) -> int:
                    nl = n.lower()
                    if "2.5-flash-lite" in nl:
                        return 1
                    if "2.5-flash" in nl and "preview" not in nl and "tts" not in nl and "image" not in nl:
                        return 0
                    if "flash-latest" in nl:
                        return 2
                    if "flash" in nl and "tts" not in nl and "image" not in nl and "audio" not in nl:
                        return 3
                    return 9
                found.sort(key=_score)
                _CACHED_GEMINI_MODELS = [n for n in found if _score(n) <= 3][:4]
                return _CACHED_GEMINI_MODELS
    except Exception as exc:
        logger.warning("No se pudo listar modelos Gemini: %s", exc)
    return []


def _call_gemini(
    api_key: str, model: str, system_prompt: str, user_prompt: str, max_tokens: int = 1024
) -> Optional[str]:
    global _LAST_AI_ERROR
    clean_api = _clean_key(api_key)
    models_to_try: List[str] = []
    for candidate in [model or "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-flash-latest"]:
        if candidate and candidate not in models_to_try:
            models_to_try.append(candidate)

    # Sumar modelos descubiertos dinámicamente si los anteriores fallan
    discovered_checked = False
    errors: List[str] = []

    idx = 0
    while idx < len(models_to_try):
        mdl = models_to_try[idx]
        idx += 1
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{mdl}:generateContent?key={clean_api}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max(1024, max_tokens),
                "responseMimeType": "application/json",
            },
        }
        try:
            with httpx.Client(timeout=22.0) as client:
                resp = client.post(url, json=payload)
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts") or []
                        if parts:
                            _LAST_AI_ERROR = ""
                            return parts[0].get("text", "")
                    errors.append(f"{mdl}:sin_texto")
                else:
                    msg = ""
                    try:
                        msg = (resp.json().get("error") or {}).get("message") or ""
                    except Exception:
                        msg = resp.text[:100]
                    errors.append(f"{mdl}:{resp.status_code} {msg[:70]}")
                    if resp.status_code in (404, 429) and not discovered_checked:
                        discovered_checked = True
                        for dm in _discover_gemini_models(clean_api):
                            if dm not in models_to_try:
                                models_to_try.append(dm)
        except Exception as exc:
            errors.append(f"{mdl}:net {str(exc)[:60]}")

    _LAST_AI_ERROR = " | ".join(errors[-2:]) if errors else "Gemini sin respuesta"
    logger.warning("Gemini falló en todos los modelos: %s", _LAST_AI_ERROR)
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


def _dispatch_provider_call(
    prov: str, system_prompt: str, user_prompt: str, max_tokens: int
) -> Optional[str]:
    global _LAST_AI_ERROR
    if prov == "gemini":
        return _call_gemini(
            settings.GEMINI_API_KEY,
            settings.GEMINI_MODEL,
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
        )

    openai_endpoints = {
        "groq": ("https://api.groq.com/openai/v1/chat/completions", settings.GROQ_API_KEY, settings.GROQ_MODEL),
        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", settings.CEREBRAS_API_KEY, settings.CEREBRAS_MODEL),
        "sambanova": ("https://api.sambanova.ai/v1/chat/completions", settings.SAMBANOVA_API_KEY, settings.SAMBANOVA_MODEL),
        "mistral": ("https://api.mistral.ai/v1/chat/completions", settings.MISTRAL_API_KEY, settings.MISTRAL_MODEL),
        "github": ("https://models.inference.ai.azure.com/chat/completions", settings.GITHUB_MODELS_TOKEN, settings.GITHUB_MODELS_MODEL),
        "openrouter": ("https://openrouter.ai/api/v1/chat/completions", settings.OPENROUTER_API_KEY, settings.OPENROUTER_MODEL),
        "openai": ("https://api.openai.com/v1/chat/completions", settings.OPENAI_API_KEY, settings.OPENAI_MODEL),
    }
    cfg = openai_endpoints.get(prov)
    if not cfg:
        return None
    url, raw_key, model = cfg
    clean_api = _clean_key(raw_key)
    if not clean_api:
        return None
    models_to_try = [model]
    if prov == "groq" and "llama-3.1-8b-instant" not in models_to_try:
        models_to_try.append("llama-3.1-8b-instant")
    for mdl in models_to_try:
        out = _call_openai_compatible(
            url, clean_api, mdl, system_prompt, user_prompt, max_tokens=max_tokens, provider_label=prov
        )
        if out:
            _LAST_AI_ERROR = ""
            return out
    return None


def _run_ai_json_prompt(
    system_prompt: str, user_prompt: str, max_tokens: int = 350
) -> Optional[Dict[str, Any]]:
    """Ejecuta un prompt estructurado en cascada por los proveedores IA configurados."""
    global _LAST_AI_ERROR
    providers = get_available_ai_providers()
    if not providers:
        return None
    chain_errors: List[str] = []
    for prov in providers:
        try:
            raw_text = _dispatch_provider_call(prov, system_prompt, user_prompt, max_tokens=max_tokens)
            parsed = _extract_json_block(raw_text or "")
            if parsed:
                _LAST_AI_ERROR = ""
                parsed["_provider"] = prov
                return parsed
            chain_errors.append(_LAST_AI_ERROR or f"{prov}:json_invalido")
        except Exception as exc:
            chain_errors.append(f"{prov}:{str(exc)[:50]}")
            logger.warning("Proveedor IA %s falló en prompt estructurado: %s", prov, exc)
            continue
    if chain_errors:
        _LAST_AI_ERROR = " | ".join(chain_errors[:3])
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


def run_full_ai_order_turn(
    context_payload: Dict[str, Any],
    history_messages: List[Dict[str, str]],
    user_message: str,
) -> Optional[Dict[str, Any]]:
    """
    Maneja un turno completo de conversación de pedidos 100% con IA (modo SOLO_IA),
    manteniendo estrictamente el catálogo real, reglas de negocio y confirmación obligatoria.
    """
    system_prompt = (
        "Eres el asistente de WhatsApp amable, natural y cercano del comercio descrito en CONTEXTO. "
        "Hablas en español cálido y conciso (mensajes cortos, 3 a 7 líneas máximo, formato WhatsApp con *negritas*). "
        "RESTRICCIONES ESTRICTAS:\n"
        "1. NUNCA inventes productos, precios, ingredientes ni zonas que no estén en el CONTEXTO.\n"
        "2. Sigue siempre el flujo ordenado: en el primer saludo distingue claramente las 2 opciones para el cliente: "
        "(A) tocar el enlace del Menú Web Interactivo (menu_web_url) para ver el catálogo, seleccionar y enviar su pedido directo, o "
        "(B) hacer su pedido aquí mismo por chat escribiéndote lo que desea. Luego: elegir categoría/producto "
        "(si es comida y el producto trae ingredientes, menciónalos brevemente) -> cantidad y personalización (sin ingredientes/extras) "
        "-> preguntar si desea agregar algo más -> modalidad de entrega permitida -> si es DOMICILIO pedir zona y dirección clara "
        "(si es vaga como 'mi casa', pide referencia antes de avanzar) -> método de pago (si es EFECTIVO preguntar con cuánto paga para vuelto) "
        "-> mostrar resumen claro y pedir CONFIRMACIÓN EXPLÍCITA del cliente.\n"
        "3. SOLO pon confirmar_pedido=true cuando el cliente ya vio el resumen y confirmó explícitamente ('sí', 'confirmo', 'dale') "
        "Y el carrito tiene productos, modalidad, dirección (si es DOMICILIO) y método de pago.\n"
        "4. RESERVACIONES DE MESA: Si el cliente quiere reservar una mesa y en el CONTEXTO hay mesas_disponibles y reservaciones_habilitadas=true, "
        "pide de forma natural: cantidad de personas, fecha y hora, y ofrece elegir mesa (indicando el anticipo si es > 0), muestra el resumen "
        "y pide confirmación explícita antes de poner reservacion.confirmar_reservacion=true. Si reservaciones_habilitadas=false, indícale amablemente que un asesor le ayudará.\n"
        "5. Si el cliente tiene un reclamo, queja, problema grave o pide hablar con una persona, pon transferir_humano=true.\n"
        "6. Si el cliente escribe con errores de ortografía (ej: 'no quiero pepinillas', 'efektivo'), entiéndelo con naturalidad.\n"
        "7. Responde EXCLUSIVAMENTE con un JSON válido (sin markdown ni texto fuera del JSON) con esta estructura exacta:\n"
        '{"respuesta":"texto corto para el cliente","acciones":{'
        '"agregar_items":[{"plato_id":0,"cantidad":1,"sin_ingredientes":[],"extras_ids":[],"notas":""}],'
        '"vaciar_carrito":false,'
        '"tipo_entrega":null,'
        '"zona_envio_id":null,'
        '"direccion_entrega":null,'
        '"metodo_pago":null,'
        '"paga_con":null,'
        '"confirmar_pedido":false,'
        '"reservacion":{"mesa_id":null,"cantidad_personas":null,"fecha_hora":null,"notas":null,"confirmar_reservacion":false},'
        '"transferir_humano":false}}'
    )
    user_prompt = (
        f"CONTEXTO:\n{json.dumps(context_payload, ensure_ascii=False)}\n\n"
        f"HISTORIAL RECIENTE:\n{json.dumps(history_messages[-8:], ensure_ascii=False)}\n\n"
        f"NUEVO MENSAJE DEL CLIENTE:\n{user_message}"
    )
    parsed = _run_ai_json_prompt(system_prompt, user_prompt, max_tokens=750)
    if parsed and isinstance(parsed.get("respuesta"), str):
        return parsed
    return None

