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
_LAST_HTTP_STATUS: int = 0
_CACHED_PROVIDER_MODELS: Dict[str, List[str]] = {}
_PREFERRED_OPENAI_TARGET: Dict[str, tuple] = {}


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
    global _LAST_AI_ERROR, _LAST_HTTP_STATUS
    _LAST_HTTP_STATUS = 0
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
            _LAST_HTTP_STATUS = resp.status_code
            if 200 <= resp.status_code < 300:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            # Si el proveedor rechaza response_format, reintentar sin response_format
            if resp.status_code == 400 and "response_format" in resp.text.lower():
                payload.pop("response_format", None)
                resp2 = client.post(url, headers=headers, json=payload)
                _LAST_HTTP_STATUS = resp2.status_code
                if 200 <= resp2.status_code < 300:
                    return resp2.json()["choices"][0]["message"]["content"]
                resp = resp2
            msg = ""
            try:
                msg = (resp.json().get("error") or {}).get("message") or ""
            except Exception:
                msg = resp.text[:80]
            _LAST_AI_ERROR = f"{provider_label}({model}):{resp.status_code} {msg[:45]}"
            logger.warning("IA %s falló: %s", provider_label, _LAST_AI_ERROR)
    except Exception as exc:
        _LAST_HTTP_STATUS = 0
        _LAST_AI_ERROR = f"{provider_label}:net {str(exc)[:45]}"
        logger.warning("IA %s error red: %s", provider_label, exc)
    return None


def _discover_openai_models(
    models_url: str, clean_api: str, cache_key: str
) -> List[str]:
    """Consulta /models en proveedores OpenAI-compatibles (ej. Groq) para descubrir modelos chat activos."""
    if _CACHED_PROVIDER_MODELS.get(cache_key):
        return _CACHED_PROVIDER_MODELS[cache_key]
    try:
        headers = {"Authorization": f"Bearer {clean_api}"}
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(models_url, headers=headers)
            if 200 <= resp.status_code < 300:
                items = resp.json().get("data") or []
                excluded = (
                    "whisper", "tts", "guard", "embed", "rerank", "moderation",
                    "audio", "speech", "transcribe", "playai", "orpheus", "vision",
                    "safeguard", "allam",
                )
                candidates: List[str] = []
                for m in items:
                    if m.get("active") is False:
                        continue
                    mid = str(m.get("id") or "").strip()
                    low = mid.lower()
                    if not mid or any(bad in low for bad in excluded):
                        continue
                    candidates.append(mid)

                def _score(n: str) -> int:
                    nl = n.lower()
                    if "llama-3.3" in nl or "llama-4" in nl:
                        return 0
                    if "gpt-oss" in nl or "qwen" in nl or "kimi" in nl:
                        return 1
                    if "llama-3" in nl or "llama" in nl:
                        return 2
                    if "mistral" in nl or "gemma" in nl or "deepseek" in nl:
                        return 3
                    return 5

                candidates.sort(key=_score)
                _CACHED_PROVIDER_MODELS[cache_key] = candidates[:4]
                return _CACHED_PROVIDER_MODELS[cache_key]
    except Exception as exc:
        logger.warning("No se pudo listar modelos en %s: %s", cache_key, exc)
    return []


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
            raw_text = _dispatch_provider_call(prov, system_prompt, user_prompt, max_tokens=450)
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
        "groq": (
            "https://api.groq.com/openai/v1/chat/completions",
            "https://api.groq.com/openai/v1/models",
            settings.GROQ_API_KEY,
            settings.GROQ_MODEL,
        ),
        "cerebras": (
            "https://api.cerebras.ai/v1/chat/completions",
            "https://api.cerebras.ai/v1/models",
            settings.CEREBRAS_API_KEY,
            settings.CEREBRAS_MODEL,
        ),
        "sambanova": (
            "https://api.sambanova.ai/v1/chat/completions",
            "https://api.sambanova.ai/v1/models",
            settings.SAMBANOVA_API_KEY,
            settings.SAMBANOVA_MODEL,
        ),
        "mistral": (
            "https://api.mistral.ai/v1/chat/completions",
            "https://api.mistral.ai/v1/models",
            settings.MISTRAL_API_KEY,
            settings.MISTRAL_MODEL,
        ),
        "github": (
            "https://models.github.ai/inference/chat/completions",
            "",
            settings.GITHUB_MODELS_TOKEN,
            settings.GITHUB_MODELS_MODEL,
        ),
        "openrouter": (
            "https://openrouter.ai/api/v1/chat/completions",
            "",
            settings.OPENROUTER_API_KEY,
            settings.OPENROUTER_MODEL,
        ),
        "openai": (
            "https://api.openai.com/v1/chat/completions",
            "",
            settings.OPENAI_API_KEY,
            settings.OPENAI_MODEL,
        ),
    }
    cfg = openai_endpoints.get(prov)
    if not cfg:
        return None
    url, models_url, raw_key, model = cfg
    clean_api = _clean_key(raw_key)
    if not clean_api:
        return None

    targets: List[tuple] = []
    if prov in _PREFERRED_OPENAI_TARGET:
        targets.append(_PREFERRED_OPENAI_TARGET[prov])

    if prov == "github":
        gh_model = model if "/" in model else f"openai/{model}"
        for cand in [
            ("https://models.github.ai/inference/chat/completions", gh_model),
            ("https://models.github.ai/inference/chat/completions", "openai/gpt-4.1-mini"),
            ("https://models.inference.ai.azure.com/chat/completions", model.split("/")[-1]),
        ]:
            if cand not in targets:
                targets.append(cand)
    else:
        if (url, model) not in targets:
            targets.append((url, model))

    discovered_checked = False
    idx = 0
    while idx < len(targets):
        t_url, t_mdl = targets[idx]
        idx += 1
        out = _call_openai_compatible(
            t_url,
            clean_api,
            t_mdl,
            system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            provider_label=prov,
        )
        if out:
            _LAST_AI_ERROR = ""
            _PREFERRED_OPENAI_TARGET[prov] = (t_url, t_mdl)
            return out
        # Si es 401/403 (API key invalida o sin permisos), saltar al siguiente proveedor inmediatamente
        if _LAST_HTTP_STATUS in (401, 403):
            break
        # Si es 404/400/429 y el proveedor soporta /models, descubrir modelos reales disponibles
        if models_url and _LAST_HTTP_STATUS in (400, 404, 429) and not discovered_checked:
            discovered_checked = True
            for dm in _discover_openai_models(models_url, clean_api, prov):
                if (url, dm) not in targets:
                    targets.append((url, dm))
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


def _build_giro_bot_rules(context_payload: Dict[str, Any]) -> str:
    """Construye instrucciones coherentes y limitantes estrictas según el giro y permisos reales del negocio."""
    com = context_payload.get("comercio") or {}
    suc = context_payload.get("sucursal") or {}
    giro = str(com.get("giro") or "RESTAURANTE").upper().strip()
    perm_dom = bool(suc.get("permite_domicilio"))
    perm_loc = bool(suc.get("permite_recoger_local"))
    perm_res = bool(suc.get("reservaciones_habilitadas")) and bool(suc.get("mesas_disponibles"))
    abierto = bool(suc.get("abierto_ahora", True))

    # Reglas operativas dinámicas según configuración real de la sucursal
    limites_dinamicos = []
    if not perm_dom:
        limites_dinamicos.append("- PROHIBIDO ofrecer envío a domicilio (permite_domicilio=false). Solo atención/retiro en el local.")
    if not perm_loc and perm_dom:
        limites_dinamicos.append("- Solo se atiende envío a domicilio (permite_recoger_local=false).")
    if not abierto:
        limites_dinamicos.append(f"- El negocio está CERRADO ahora (horario: {suc.get('horario', '')}). Infórmalo amablemente; puedes mostrar el catálogo/precios.")
    if not com.get("acepta_efectivo"):
        limites_dinamicos.append("- NO se acepta efectivo.")
    if not com.get("acepta_transferencia"):
        limites_dinamicos.append("- NO se aceptan transferencias bancarias.")

    if giro == "SERVICIOS_CITAS":
        perfil = (
            "GIRO DEL NEGOCIO: CLÍNICA / DENTISTA / SERVICIOS CON CITA (SERVICIOS_CITAS).\n"
            "- Actúa como recepcionista profesional, empático y ordenado.\n"
            "- Vocabulario obligatorio: *servicios/tratamientos*, *pacientes/clientes*, *consultorios/especialistas* y *citas*.\n"
            "- PROHIBIDO usar palabras de restaurante o tienda: NUNCA digas 'mesa', 'cocina', 'platillo', 'ingredientes' ni 'envío a domicilio'.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) ver el catálogo de servicios y precios en menu_web_url, "
            "(B) consultar tratamientos o precios aquí por chat, y (C) agendar una cita" + ("." if perm_res else " (indica que un asesor coordinará horario).") + "\n"
            "- FLUJO DE CITA: pregunta qué servicio/tratamiento necesita -> ofrece consultorio/especialista disponible (indicando el anticipo si > 0) "
            "-> pide fecha y hora clara con AM/PM dentro del horario -> pide nombre completo del paciente -> muestra resumen y pide confirmación explícita "
            "(usa siempre cantidad_personas=1 automáticamente, NO preguntes cuántas personas).\n"
            "- LIMITANTE MÉDICA ESTRICTA: NUNCA des diagnósticos médicos/odontológicos ni recetes medicamentos; explica con amabilidad que el especialista lo evaluará en consulta.\n"
        )
    elif giro == "FARMACIA":
        perfil = (
            "GIRO DEL NEGOCIO: FARMACIA / SALUD (FARMACIA).\n"
            "- Actúa como asistente de mostrador de farmacia, claro, respetuoso y preciso.\n"
            "- Vocabulario: *productos/medicamentos*, *secciones*, *presentaciones/dosis*, *despacho*.\n"
            "- PROHIBIDO mencionar mesas, reservaciones, cocina o ingredientes removibles.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) ver el catálogo en menu_web_url y (B) hacer su pedido por chat.\n"
            "- LIMITANTE DE SALUD ESTRICTA: NUNCA diagnostiques enfermedades ni recetes medicamentos que requieran receta médica; "
            "si piden recomendación clínica o dosis personalizada, sugiere consultar a un médico o pon transferir_humano=true para que un farmacéutico atienda.\n"
        )
    elif giro == "TIENDA":
        perfil = (
            "GIRO DEL NEGOCIO: TIENDA / BOUTIQUE / RETAIL (TIENDA).\n"
            "- Actúa como asesor de ventas de tienda, amable y ágil.\n"
            "- Vocabulario: *artículos/prendas*, *categorías*, *tallas/colores/variantes*, *empaque y envío*.\n"
            "- PROHIBIDO mencionar mesas, reservaciones, cocina o ingredientes removibles.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) ver el catálogo con fotos en menu_web_url y (B) comprar aquí por chat.\n"
            "- Antes de confirmar un artículo que tenga variantes (tallas/colores en extras), confirma cuál desea; NUNCA prometas tallas o colores fuera del catálogo.\n"
        )
    elif giro == "SUPERMERCADO":
        perfil = (
            "GIRO DEL NEGOCIO: MINISÚPER / CONVENIENCIA (SUPERMERCADO).\n"
            "- Actúa como asistente ágil de despensa/minisúper.\n"
            "- Vocabulario: *productos*, *pasillos/secciones*, *alistamiento*.\n"
            "- PROHIBIDO mencionar mesas, reservaciones, cocina o ingredientes removibles.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) ver el catálogo en menu_web_url y (B) enviar su lista de compras por chat.\n"
        )
    elif giro == "CAFETERIA":
        perfil = (
            "GIRO DEL NEGOCIO: CAFETERÍA / REPOSTERÍA (CAFETERIA).\n"
            "- Actúa como anfitrión/barista cálido y conciso.\n"
            "- Vocabulario: *menú de bebidas y repostería*, *barra*, *tamaños/leches/extras*.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) Menú Web (menu_web_url), (B) pedir por chat"
            + (", y (C) reservar mesa." if perm_res else ".") + "\n"
        )
    else:
        perfil = (
            "GIRO DEL NEGOCIO: RESTAURANTE / COMIDA RÁPIDA (RESTAURANTE).\n"
            "- Actúa como anfitrión/mesero cálido, rápido y ordenado.\n"
            "- Vocabulario: *menú*, *platillos*, *cocina*, *ingredientes/extras*.\n"
            "- PRIMER SALUDO (solo si HISTORIAL está vacío): ofrece (A) Menú Web con fotos (menu_web_url), (B) pedir por chat"
            + (", y (C) reservar mesa." if perm_res else ".") + "\n"
        )

    if limites_dinamicos:
        perfil += "LIMITANTES ACTIVAS DE ESTA SUCURSAL:\n" + "\n".join(limites_dinamicos) + "\n"

    # Personalización del dueño del negocio (tabla config_bot_comercio)
    bcfg = context_payload.get("bot_config") or {}
    custom_rules = []
    if bcfg.get("nombre_asistente"):
        custom_rules.append(f"- Tu nombre como asistente es *{bcfg['nombre_asistente']}*.")
    tono = str(bcfg.get("tono_bot") or "CERCANO").upper()
    if tono == "PROFESIONAL":
        custom_rules.append("- TONO: Profesional, formal y respetuoso (trata al cliente de 'usted').")
    elif tono == "DIRECTO":
        custom_rules.append("- TONO: Directo, muy breve y al grano (máximo 3 a 4 líneas por respuesta).")
    else:
        custom_rules.append("- TONO: Cercano, cálido y amigable.")
    if bcfg.get("promo_del_dia"):
        custom_rules.append(f"- PROMOCIÓN/AVISO DEL DÍA: {bcfg['promo_del_dia']} (menciónala brevemente si encaja con la consulta).")
    if bcfg.get("ubicacion_texto") or bcfg.get("ubicacion_maps_url"):
        ubi = " | ".join(x for x in [bcfg.get("ubicacion_texto"), bcfg.get("ubicacion_maps_url")] if x)
        custom_rules.append(f"- UBICACIÓN OFICIAL DEL LOCAL: {ubi}.")
    if bcfg.get("politicas_faq"):
        custom_rules.append(f"- POLÍTICAS / PREGUNTAS FRECUENTES DEL NEGOCIO: {bcfg['politicas_faq']}.")
    if float(bcfg.get("pedido_minimo") or 0) > 0 and giro != "SERVICIOS_CITAS":
        sym_c = com.get("simbolo_moneda") or "C$"
        custom_rules.append(
            f"- PEDIDO MÍNIMO PARA ENVÍO A DOMICILIO: {sym_c}{float(bcfg['pedido_minimo']):.2f}. "
            "Si el cliente elige DOMICILIO y su subtotal es menor, infórmale amablemente cuánto le falta o sugiérele retiro en local."
        )
    if not abierto and not bcfg.get("permitir_fuera_horario", True):
        msg_c = bcfg.get("mensaje_cerrado") or f"Estamos cerrados (horario {suc.get('horario', '')})."
        custom_rules.append(f"- FUERA DE HORARIO ESTRICTO: NO confirmes pedidos ni citas ahora; informa: {msg_c}")
    if bcfg.get("reglas_extra"):
        custom_rules.append(f"- INSTRUCCIONES ESPECIALES DEL DUEÑO (OBLIGATORIAS): {bcfg['reglas_extra']}")

    if custom_rules:
        perfil += "PERSONALIZACIÓN DEL DUEÑO DEL NEGOCIO:\n" + "\n".join(custom_rules) + "\n"
    return perfil


def run_full_ai_order_turn(
    context_payload: Dict[str, Any],
    history_messages: List[Dict[str, str]],
    user_message: str,
) -> Optional[Dict[str, Any]]:
    """
    Maneja un turno completo de conversación de pedidos/citas 100% con IA (modo SOLO_IA),
    adaptado dinámicamente al giro del negocio y respetando estrictamente sus limitantes.
    """
    giro_rules = _build_giro_bot_rules(context_payload)
    system_prompt = (
        "Eres el asistente oficial de WhatsApp del negocio descrito en CONTEXTO.\n"
        "ESTILO VISUAL WHATSAPP OBLIGATORIO EN TODA LA CONVERSACIÓN:\n"
        "- Mensajes limpios, compactos y fáciles de leer en celular (3 a 6 líneas, SIN saltos de línea dobles innecesarios).\n"
        "- Usa 1 emoji claro al inicio de cada opción o dato clave (ej. 📋 catálogo/resumen, 🛒 productos, 🚚 domicilio, 🏪 retiro, 🪑 mesa, 📅 fecha/hora, 💵 efectivo, 💳 transferencia, 📍 dirección, ✅ confirmación, ⚠️ aviso).\n"
        "- En resúmenes de pedido o reserva, pon CADA dato en su propia línea con emoji (👤, 🪑/🩺, 📅, 💵/💳) y nunca todo pegado en una sola oración.\n"
        "- Usa *negritas* de WhatsApp solo en nombres clave, precios y totales, y guion corto (–) para aclaraciones breves.\n"
        "- Cierra siempre con una pregunta corta y directa (ej. '¿Qué prefieres?', '¿Agregas algo más?', '¿Confirmamos?').\n"
        "- REGLA ANTI-REPETICIÓN: NUNCA vuelvas a pedir un dato que el cliente YA escribió en NUEVO MENSAJE DEL CLIENTE (ej. si acaba de escribir su nombre completo, fecha, hora o dirección, guárdalo en acciones y avanza de inmediato al siguiente paso o resumen).\n\n"
        f"{giro_rules}\n"
        "REGLAS GENERALES ESTRICTAS:\n"
        "1. NUNCA inventes productos, servicios, precios, tallas, ingredientes ni zonas que no estén en el CONTEXTO. "
        "Si el cliente pregunta temas ajenos al negocio (política, tareas, programación, etc.), declina amablemente y redirige a los productos/servicios del negocio.\n"
        "2. PROHIBIDO repetir el saludo inicial ni las opciones A/B/C si en HISTORIAL RECIENTE ya saludaste.\n"
        "   - Si responde 'B', 'la b', 'por chat' o pide ver opciones: muéstrale de inmediato las categorías o destacados con precio.\n"
        "   - Si responde 'C', 'reservar' o 'agendar cita' (cuando esté habilitado): inicia directo el flujo de cita/reserva.\n"
        "   - Si el cliente usa lenguaje informal, abreviaciones o modismos (ej. 'bro', 'ey', 'mandame', 'q vale', 'sin sebolla'), "
        "entiéndelo con naturalidad pero mantén SIEMPRE un tono amable, profesional y el orden estricto de los pasos sin saltarte ninguno.\n"
        "3. FLUJO ORDENADO DE COMPRA/PEDIDO (UNA SOLA PREGUNTA POR MENSAJE):\n"
        "   - PROHIBIDO hacer 2 o más preguntas en el mismo mensaje (ej. NO preguntes ingredientes + domicilio/local + algo más juntos).\n"
        "   - Si el cliente da varios datos en un solo mensaje (ej. '2 pizzas sin cebolla para recoger y pago en transferencia'), "
        "extrae y guarda TODOS esos datos en `acciones` al mismo tiempo y haz SOLO la siguiente pregunta que falte.\n"
        "   - Obedece SIEMPRE `carrito_actual.siguiente_paso_obligatorio` y revisa `carrito_actual` antes de preguntar: "
        "si `tipo_entrega` ya tiene valor, PROHIBIDO volver a preguntar domicilio/local; "
        "si `opciones_y_extras_confirmados=true`, PROHIBIDO volver a preguntar ingredientes o extras; "
        "si `metodo_pago` ya tiene valor, PROHIBIDO volver a preguntar método de pago.\n"
        "   - Al tener todos los datos, muestra el resumen ordenado (mencionando beneficios VIP si aplica) y pide CONFIRMACIÓN EXPLÍCITA antes de poner confirmar_pedido=true.\n"
        "4. CITAS / RESERVACIONES: SOLO si reservaciones_habilitadas=true y hay mesas_disponibles. "
        "Valida fecha y hora con AM/PM dentro del horario, nombre completo y espacio/consultorio. "
        "Si el anticipo > 0, NUNCA digas que ya está confirmada sin pagar: deja claro que queda *PENDIENTE DE PAGO* hasta abonar el anticipo.\n"
        "5. RESTRICCIONES POR PRODUCTO (si un ítem del catálogo incluye estas marcas): "
        "si tiene requiere_validacion=true, NO lo confirmes ni cobres automáticamente (pide receta/foto o pon transferir_humano=true para validación humana); "
        "si tiene solo_retiro_local=true, PROHIBIDO ofrecer envío a domicilio para ese producto (solo retiro en local).\n"
        "6. Si hay queja, reclamo, problema grave o pide hablar con una persona, pon transferir_humano=true.\n"
        "7. Tolera errores de ortografía con naturalidad.\n"
        "8. Responde EXCLUSIVAMENTE con un JSON válido (sin markdown ni texto fuera del JSON) con esta estructura exacta:\n"
        '{"respuesta":"texto corto para el cliente","acciones":{'
        '"agregar_items":[{"plato_id":0,"cantidad":1,"sin_ingredientes":[],"extras_ids":[],"notas":""}],'
        '"vaciar_carrito":false,'
        '"tipo_entrega":null,'
        '"zona_envio_id":null,'
        '"direccion_entrega":null,'
        '"metodo_pago":null,'
        '"paga_con":null,'
        '"confirmar_pedido":false,'
        '"reservacion":{"mesa_id":null,"cantidad_personas":null,"fecha_hora":null,"nombre_cliente":null,"notas":null,"confirmar_reservacion":false},'
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

