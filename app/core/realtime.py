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


def publish_tenant_event(
    tenant_id: int, event_type: str, data: Dict[str, Any] | None = None
) -> None:
    """
    Publica un evento para todos los navegadores conectados de un tenant_id.
    Es seguro llamarlo desde endpoints síncronos (threadpool) o asíncronos.
    """
    if not tenant_id:
        return
    payload = {"tipo": event_type, **(data or {})}
    with _LOCK:
        subs = list(_SUBSCRIBERS.get(int(tenant_id), []))

    for loop, queue in subs:
        try:
            if not loop.is_closed():
                loop.call_soon_threadsafe(_safe_put_nowait, queue, payload)
        except Exception:
            pass


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
