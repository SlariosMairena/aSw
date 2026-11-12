# =====================================================================
# Dockerfile Endurecido (ISO 27001 / OWASP Container Security)
# Compatible con: Render.com, Railway, Koyeb, Fly.io y VPS propio
# =====================================================================
FROM python:3.11-slim AS base

# Evitar archivos .pyc y habilitar logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Instalar dependencias mínimas del sistema para PostgreSQL (Neon.tech) y salud
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario sin privilegios de root (Estándar de Seguridad OWASP / ISO)
RUN groupadd -r saasgroup && useradd -r -g saasgroup -d /app -s /sbin/nologin saasuser

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copiar código fuente de la aplicación
COPY . .

# Crear directorio para archivos subidos (comprobantes de pago Modo 1) y ajustar permisos
RUN mkdir -p /app/uploads/comprobantes && chown -R saasuser:saasgroup /app

# Cambiar a usuario no-root
USER saasuser

# Puerto dinámico (Render asigna $PORT automáticamente, por defecto 8000)
EXPOSE 8000

# Verificación de salud ligera (/health no despierta innecesariamente a Neon.tech)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Comando de arranque en producción
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]

