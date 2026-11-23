"""
Bus de eventos en tiempo real por comercio (Server-Sent Events / SSE).
Permite notificar al instante al tablero Kanban y al modal de chat cuando ocurre
un movimiento real (nuevo pedido, cambio de estado o nuevo mensaje de WhatsApp),
sin necesidad de polling constante.
"""
import asyncio
import json
import threading
from typing import Any, AsyncGenerator, Dict, List, Tuple

_LOCK = threading.Lock()
_SUBSCRIBERS: Dict[int, List[Tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_REDIS_LISTENER_STARTED = False
_REDIS_LISTENER_LOCK = threading.Lock()
_SSE_CHANNEL = "ordenapro:sse:events"


def _dispatch_local(tenant_id: int, payload: Dict[str, Any]) -> None:
    with _LOCK:
        subs = list(_SUBSCRIBERS.get(int(tenant_id), []))
    for loop, queue in subs:
        try:
            if not loop.is_closed():
                loop.call_soon_threadsafe(_safe_put_nowait, queue, payload)
        except Exception:
            pass


def _ensure_redis_listener() -> None:
    global _REDIS_LISTENER_STARTED
    if _REDIS_LISTENER_STARTED:
        return
    from app.core.redis_client import get_redis

    client = get_redis()
    if client is None:
        return
    with _REDIS_LISTENER_LOCK:
        if _REDIS_LISTENER_STARTED:
            return
        _REDIS_LISTENER_STARTED = True

        def _worker() -> None:
            try:
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                pubsub.subscribe(_SSE_CHANNEL)
                for msg in pubsub.listen():
                    if msg.get("type") != "message":
                        continue
                    try:
                        envelope = json.loads(msg["data"])
                        tid = int(envelope.get("tenant_id") or 0)
                        payload = envelope.get("payload") or {}
                        if tid:
                            _dispatch_local(tid, payload)
                    except Exception:
                        pass
            except Exception:
                pass

        t = threading.Thread(target=_worker, daemon=True, name="redis-sse-listener")
        t.start()


def publish_tenant_event(
    tenant_id: int, event_type: str, data: Dict[str, Any] | None = None
) -> None:
    """
    Publica un evento para todos los navegadores conectados de un tenant_id.
    Usa Redis Pub/Sub si REDIS_URL está configurado (multi-worker), o entrega local en memoria.
    """
    if not tenant_id:
        return
    payload = {"tipo": event_type, **(data or {})}

    from app.core.redis_client import get_redis

    client = get_redis()
    if client is not None:
        try:
            _ensure_redis_listener()
            client.publish(
                _SSE_CHANNEL,
                json.dumps({"tenant_id": int(tenant_id), "payload": payload}, ensure_ascii=False),
            )
            return
        except Exception:
            pass

    _dispatch_local(int(tenant_id), payload)


def _safe_put_nowait(queue: asyncio.Queue, item: Dict[str, Any]) -> None:
    try:
        if queue.full():
            try:
                queue.get_nowait()
            except Exception:
                pass
        queue.put_nowait(item)
    except Exception:
        pass


async def subscribe_tenant_events(tenant_id: int) -> AsyncGenerator[str, None]:
    """Generador SSE que emite eventos JSON en tiempo real y keep-alive cada 25s."""
    _ensure_redis_listener()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    tid = int(tenant_id)
    entry = (loop, queue)

    with _LOCK:
        _SUBSCRIBERS.setdefault(tid, []).append(entry)

    try:
        # Confirmación inicial de conexión SSE
        yield f"data: {json.dumps({'tipo': 'CONECTADO'}, ensure_ascii=False)}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=25.0)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                # Heartbeat ligero para mantener viva la conexión en proxies/Render
                yield ": ping\n\n"
    finally:
        with _LOCK:
            subs = _SUBSCRIBERS.get(tid, [])
            if entry in subs:
                subs.remove(entry)
            if not subs and tid in _SUBSCRIBERS:
                del _SUBSCRIBERS[tid]
