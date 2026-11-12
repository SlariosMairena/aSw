# SaaS Bot de Pedidos por WhatsApp + Panel Web Multi-Comercio / Multi-Sucursal

Plataforma SaaS en **Python (FastAPI)** para automatizar la toma de pedidos de restaurantes y comercios **100% desde un único número de WhatsApp** (Opción B por Links/QR de sucursal), con **Panel Web en Tiempo Real**, **Resumen de Chat con IA (`MultiLLMRouter`)**, **Intervención Humana por Chat**, **Impresión de Ticket Térmico (58mm/80mm)** y controles de seguridad alineados con **ISO/IEC 27001, ISO 27701 y OWASP ASVS**.

---

## Arquitectura Principal

- **Backend:** Python 3.11+ con FastAPI + WebSockets + Pydantic v2
- **Base de Datos:** **Neon.tech (PostgreSQL Serverless)** con aislamiento por esquema (`saas_whatsapp`) y `pool_pre_ping=True` + SQLAlchemy 2.0
- **Seguridad (ISO 27001 / OWASP):**
  - Contraseñas con **Argon2id**
  - Sesiones **JWT (`HttpOnly`, `Secure`, `SameSite`)** + **2FA (TOTP con Google Authenticator)**
  - Cifrado en reposo **AES-256 (Fernet)** para tokens y claves de comercios
  - Validación criptográfica **HMAC-SHA256** en webhooks
  - Aislamiento estricto **Multi-Tenant (`tenant_id`)** y **Multi-Sucursal (`sucursal_id`)**
  - Tabla inmutable de auditoría (`audit_logs`)
- **Infraestructura:** `Dockerfile` (usuario no-root), `docker-compose.yml` (con **Evolution API** para WhatsApp QR Nivel 1) y `render.yaml`

---

## Ejecución Local

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Pruebas Automatizadas

```bash
python -m pytest -v
```

