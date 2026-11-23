"""
Cliente centralizado de Redis con fallback automático y seguro a memoria local.
- Si `REDIS_URL` está configurado (ej. Upstash, Render Key Value o Redis local), usa Redis real.
- Si `REDIS_URL` está vacío o Redis no responde temporalmente, degrada limpiamente a memoria local
  sin interrumpir nunca las peticiones del SaaS.
"""
import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings

try:
    import redis as _redis_lib
except Exception:
    _redis_lib = None

_CLIENT_LOCK = threading.Lock()
_REDIS_CLIENT: Any = None
_REDIS_INIT_DONE = False

# Fallback ligero en memoria con TTL (usado solo cuando REDIS_URL no está configurado)
_MEM_LOCK = threading.Lock()
_MEM_CACHE: Dict[str, Tuple[float, str]] = {}


def _prefixed(key: str) -> str:
    pfx = settings.REDIS_KEY_PREFIX or "ordenapro:"
    return key if key.startswith(pfx) else f"{pfx}{key}"


def get_redis():
    """Devuelve el cliente Redis singleton si REDIS_URL está configurado, o None si opera en memoria."""
    global _REDIS_CLIENT, _REDIS_INIT_DONE
    if not settings.REDIS_URL or _redis_lib is None:
        return None
    if _REDIS_INIT_DONE:
        return _REDIS_CLIENT
    with _CLIENT_LOCK:
        if _REDIS_INIT_DONE:
            return _REDIS_CLIENT
        try:
            client = _redis_lib.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            _REDIS_CLIENT = client
        except Exception as exc:
            print(f"[Redis] No se pudo inicializar cliente Redis (usando fallback memoria): {exc}")
            _REDIS_CLIENT = None
        _REDIS_INIT_DONE = True
        return _REDIS_CLIENT


def cache_get_json(key: str) -> Optional[Any]:
    """Lee un valor JSON de Redis (o del caché local en memoria si no hay Redis)."""
    full_key = _prefixed(key)
    client = get_redis()
    if client is not None:
        try:
            raw = client.get(full_key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            pass

    now = time.monotonic()
    with _MEM_LOCK:
        entry = _MEM_CACHE.get(full_key)
        if not entry:
            return None
        exp, raw = entry
        if exp and now > exp:
            _MEM_CACHE.pop(full_key, None)
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None


def cache_set_json(key: str, value: Any, ttl_seconds: int = 60) -> bool:
    """Guarda un valor JSON en Redis con TTL (o en memoria local como fallback)."""
    full_key = _prefixed(key)
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return False

    ttl = max(1, int(ttl_seconds))
    client = get_redis()
    if client is not None:
        try:
            client.setex(full_key, ttl, raw)
            return True
        except Exception:
            pass

    with _MEM_LOCK:
        if len(_MEM_CACHE) > 1000:
            _MEM_CACHE.clear()
        _MEM_CACHE[full_key] = (time.monotonic() + ttl, raw)
    return True


def cache_delete(key: str) -> None:
    """Elimina una clave específica de Redis y de memoria local."""
    full_key = _prefixed(key)
    client = get_redis()
    if client is not None:
        try:
            client.delete(full_key)
        except Exception:
            pass
    with _MEM_LOCK:
        _MEM_CACHE.pop(full_key, None)


def cache_delete_prefix(prefix: str) -> None:
    """Elimina todas las claves que inicien con un prefijo dado."""
    full_prefix = _prefixed(prefix)
    client = get_redis()
    if client is not None:
        try:
            keys = list(client.scan_iter(match=f"{full_prefix}*", count=100))
            if keys:
                client.delete(*keys)
        except Exception:
            pass
    with _MEM_LOCK:
        for k in [k for k in _MEM_CACHE if k.startswith(full_prefix)]:
            _MEM_CACHE.pop(k, None)


def rate_limit_hit(key: str, window_seconds: int, max_requests: int) -> Tuple[bool, int]:
    """
    Contador distribuido de rate-limiting en Redis (INCR + EXPIRE).
    Devuelve (permitido, conteo_actual). Si no hay Redis, devuelve (True, 0) para delegar al fallback local.
    """
    client = get_redis()
    if client is None:
        return True, 0
    full_key = _prefixed(f"rl:{key}")
    try:
        pipe = client.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, max(1, int(window_seconds)), nx=True)
        count, _ = pipe.execute()
        return int(count) <= int(max_requests), int(count)
    except Exception:
        return True, 0


def get_redis_health() -> Dict[str, Any]:
    """Estado de salud de Redis para /health y SuperAdmin."""
    if not settings.REDIS_URL:
        return {
            "configured": False,
            "connected": False,
            "backend": "memory_fallback",
            "detail": "REDIS_URL no configurado; operando en memoria local.",
        }
    client = get_redis()
    if client is None:
        return {
            "configured": True,
            "connected": False,
            "backend": "memory_fallback",
            "detail": "Cliente Redis no disponible.",
        }
    t0 = time.perf_counter()
    try:
        ok = bool(client.ping())
        ms = round((time.perf_counter() - t0) * 1000, 2)
        return {
            "configured": True,
            "connected": ok,
            "backend": "redis" if ok else "memory_fallback",
            "latency_ms": ms,
        }
    except Exception as exc:
        return {
            "configured": True,
            "connected": False,
            "backend": "memory_fallback",
            "detail": str(exc)[:120],
        }
