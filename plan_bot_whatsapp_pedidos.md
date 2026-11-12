# Plan Maestro del Proyecto (v8 - Definitivo con Neon.tech + Docker en Render)

> **Estado actual:** Plan 100% ajustado con **Neon.tech** y **Render/Docker**. Sigo **sin crear archivos de código** hasta que me des tu confirmación final de *"Adelante con la Fase 1"*.

---

## 1. Base de Datos en la Nube: Neon.tech (PostgreSQL Serverless)

¡Usar **Neon.tech** es una decisión excelente! De hecho, resuelve el mayor problema que tenía el plan gratuito de Render (donde Render te borraba la base de datos a los 90 días).

### ¿Cómo dejaremos configurado el proyecto para Neon.tech?
1. **PostgreSQL Permanente y Gratis en Pruebas:** Conectaremos **FastAPI + SQLAlchemy 2.0** directamente a tu cadena de conexión de **Neon.tech** (`postgresql+psycopg://...@ep-xxx-pooler.aws.neon.tech/neondb?sslmode=require`).
2. **Optimización Especial para Neon (`Scale-to-Zero`):**
   - Como Neon.tech "pausa" la base de datos tras unos minutos de inactividad para no gastar tu cuota gratis, configuraremos SQLAlchemy con **`pool_pre_ping=True`** y **`pool_recycle=300`**.
   - **¿Qué logra esto?** Cuando llegue un mensaje de WhatsApp después de varias horas sin actividad, Python despertará a Neon.tech automáticamente en milisegundos sin dar nunca error de *"conexión cerrada"*.
3. **Seguridad SSL/TLS Obligatoria:** Toda comunicación entre tu servidor (en tu PC o en Render) y Neon.tech viajará cifrada con `sslmode=require`.

---

## 2. Estrategia de Despliegue por Etapas (Pruebas Gratis ➔ Producción)

Tal como lo planteaste, dividiremos la infraestructura en **2 etapas inteligentes** usando el mismo **`Dockerfile`**:

### Etapa Inicial (Pruebas y Validación Gratis — $0 USD)
* **Base de Datos:** **Neon.tech** (Plan Gratis Serverless PostgreSQL).
* **Servidor del Bot y Panel Web (Python + Docker):**
  * **Opción Principal:** **Render.com** (dejaremos listo el archivo `Dockerfile` y `render.yaml` para desplegar automáticamente desde tu repositorio).
  * **Opción Alternativa Gratis:** **Koyeb** o **Hugging Face Spaces (Docker)** (también corren nuestro mismo `Dockerfile` gratis y tienen muy buena estabilidad).
  * **Truco Técnico Incluido (`/health`):** Crearemos una ruta ultraligera `/health` en FastAPI para mantener despierto el contenedor en Render durante tus pruebas de WhatsApp sin consumir la cuota de Neon.tech.

### Etapa de Producción Comercial (Cuando tengas Restaurantes Pagando)
* **Migración en 5 minutos a VPS (Hetzner / Hostinger / DigitalOcean con Coolify/Docker):**
  * Como todo está en **Docker** y los datos ya viven en **Neon.tech** (o en el PostgreSQL del VPS), mudarte cuando el negocio crezca será transparente sin tocar el código.

---

## 3. Resumen Completo del Stack Tecnológico Cerrado

| Capa del Sistema | Tecnología Elegida |
| :--- | :--- |
| **1. Lenguaje y Backend** | **Python 3.11+ con FastAPI** + WebSockets en tiempo real + Pydantic v2. |
| **2. Base de Datos** | **Neon.tech (PostgreSQL Serverless)** + **SQLAlchemy 2.0** + **Alembic** (con opción de SQLite local de respaldo). |
| **3. Panel Web Unificado** | **Opción 1:** FastAPI + **Jinja2 + TailwindCSS + Alpine.js / HTMX** (Panel SuperAdmin + Panel Restaurante + Vista Previa de Ticket Térmico 58mm/80mm). |
| **4. Inteligencia Artificial** | **Enrutador Multi-IA (`MultiLLMRouter`):** Soporta varias IAs gratis a la vez (**Google Gemini**, **Groq Cloud**, **OpenRouter**) con respaldo automático, y listo para cambiar a IA de pago empresarial (**OpenAI / Vertex / Anthropic**). |
| **5. Conexión WhatsApp** | **Solo Nivel 1 y Nivel 2:** Iniciamos con **Nivel 1 (Código QR con Evolution API)** y listo para cambiar a **Nivel 2 (WhatsApp Cloud API Oficial de Meta)**. |
| **6. Pagos y Reservaciones** | **Modo 1 activo:** Efectivo (calculando cambio) y Transferencia (foto de comprobante en WhatsApp).<br>**Modo 2 listo en estructura:** API de Pagos + **Módulo de Reservación de Mesas con pago obligatorio**. |
| **7. Contenedores y Nube** | **`Dockerfile` + `docker-compose.yml` + `render.yaml`** listos para **Render + Neon.tech** hoy y **VPS** después. |
| **8. Seguridad (ISO 27001 / OWASP)** | Aislamiento Multi-Tenant/Multi-Sucursal, **Argon2id**, **JWT HttpOnly**, **2FA**, **AES-256**, **HMAC-SHA256**, **Rate Limiting**, precios blindados anti-*Prompt Injection* y tabla **`audit_logs`**. |

---

## 4. Plan de Ejecución Paso a Paso (Las 5 Fases)

1. **Fase 1 — Estructura Base, Docker, Conexión a Neon.tech, Modelos SaaS y Seguridad ISO/OWASP:**
   - Crear la estructura del proyecto en `c:\IA-sistem`, `Dockerfile`, `docker-compose.yml`, `render.yaml` y archivo `.env.example` preparado para **Neon.tech**.
   - Programar el motor de seguridad (`Argon2id`, `JWT`, `AES-256`, `2FA`, `audit_logs`) y todas las tablas de la base de datos Multi-Tenant y Multi-Sucursal (Planes SaaS, Suscripciones, Comercios, Sucursales, Usuarios, Platos con Ingredientes/Extras, Mesas/Reservaciones, Chats con interruptor Bot/Humano, Pedidos y Pagos).
2. **Fase 2 — Panel Web Completo (`SUPER_ADMIN` + Panel de Restaurantes/Cocina):**
   - Pantallas para ti (crear comercios, sucursales, planes del SaaS, configurar llaves del Multi-IA y generar Links/QR de WhatsApp).
   - Pantallas para los restaurantes (menú con ingredientes y *"Agotado hoy"*, tablero en vivo `Recibido` / `En proceso` / `Listo` con sonido, vista del **Resumen del Chat**, botón **`[Tomar control del chat]`** y vista previa del **Ticket Térmico**).
3. **Fase 3 — El Cerebro del Bot en Python + Enrutador Multi-IA:**
   - Lógica que atiende por sucursal, valida ingredientes (*"sin cebolla"*), recuerda clientes frecuentes, recibe ubicación GPS, calcula totales/cambio y genera el resumen del chat con IA.
4. **Fase 4 — Conector WhatsApp Real (Nivel 1 por QR y Nivel 2 API Oficial).**
5. **Fase 5 — Conectores de API de Pagos (Modo 2) + Activación de Reservación de Mesas.**

---

### ¿Listo para arrancar?
Si todo en esta **versión 8** está exactamente como lo quieres, dime **"Confirmo, empecemos con la Fase 1"** (o indícame si deseas algún último ajuste).
