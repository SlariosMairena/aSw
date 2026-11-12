"""
Middlewares de Seguridad Web (OWASP Top 10 / ISO 27001):
- Cabeceras de Seguridad HTTP (CSP, HSTS, X-Frame-Options, X-Content-Type-Options)
- Rate Limiting por IP / Ruta para mitigar ataques de fuerza bruta y abuso de IA
"""
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Agrega cabeceras de defensa en profundidad recomendadas por OWASP ASVS."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Limitador de velocidad en ventana deslizante para proteger:
    - Login (anti fuerza bruta)
    - Webhooks y endpoints de IA (anti spam / agotamiento de cuota)
    """

    def __init__(
        self,
        app,
        max_requests_per_minute: int = 120,
        auth_max_requests_per_minute: int = 15,
    ) -> None:
        super().__init__(app)
        self.max_requests = max_requests_per_minute
        self.auth_max_requests = auth_max_requests_per_minute
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path == "/health" or path.startswith("/static"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        is_auth_route = "/auth/login" in path
        bucket_key = f"{client_ip}:{'auth' if is_auth_route else 'general'}"
        limit = self.auth_max_requests if is_auth_route else self.max_requests

        now = time.time()
        window_start = now - 60.0
        timestamps = self._buckets[bucket_key]

        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()

        if len(timestamps) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Demasiadas solicitudes. Por seguridad (ISO/OWASP), intenta de nuevo en un minuto."
                },
            )

        timestamps.append(now)
        return await call_next(request)

