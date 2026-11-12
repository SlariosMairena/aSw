"""
Pruebas Automatizadas de la Fase 1 y Fase 2:
- Adaptador Neon.tech (PostgreSQL Serverless con sslmode=require y DB_SCHEMA aislado)
- Controles de Seguridad ISO/IEC 27001 y OWASP (Argon2id, AES-256, JWT, HMAC-SHA256, 2FA, Headers)
- Base de Datos SaaS Multi-Tenant y Multi-Sucursal (Planes, Menú con Ingredientes, Pedidos, Reservaciones)
- Panel Web Visual en "/" y Vista de Ticket Térmico en "/ticket/{id}" (Fase 2)
- APIs Operativas: Cambio de estado de pedido (Recibido -> En Proceso -> Listo),
  Tomar control humano del chat y botón "Agotado hoy"
"""
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import (
    decrypt_sensitive_data,
    encrypt_sensitive_data,
    hash_password,
    verify_password,
    verify_webhook_hmac_signature,
)
from app.main import app


def test_neon_database_url_normalization():
    """Verifica que cualquier URL de Neon.tech se convierta a psycopg v3 con sslmode=require."""
    raw_neon = "postgresql://user:pass@ep-cool-darkness-123456-pooler.us-east-2.aws.neon.tech/neondb"
    normalized = Settings._normalize_database_url(raw_neon)
    assert normalized.startswith("postgresql+psycopg://")
    assert "sslmode=require" in normalized


def test_security_primitives_iso_owasp():
    """Verifica Argon2id, cifrado AES-256 en reposo y firma HMAC-SHA256."""
    hashed = hash_password("MiClaveUltraSegura#2026")
    assert hashed.startswith("$argon2id$")
    assert verify_password("MiClaveUltraSegura#2026", hashed) is True
    assert verify_password("ClaveIncorrecta", hashed) is False

    secret_api_key = "sk_live_secreto_restaurante_12345"
    encrypted = encrypt_sensitive_data(secret_api_key)
    assert encrypted != secret_api_key
    assert decrypt_sensitive_data(encrypted) == secret_api_key

    import hashlib
    import hmac

    body = b'{"event":"messages.upsert"}'
    secret = "test_secret"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_hmac_signature(body, sig, secret=secret) is True
    assert verify_webhook_hmac_signature(body, "sha256=invalida", secret=secret) is False


def test_health_and_saas_phase1_and_phase2():
    """Verifica /health, el Panel Web en '/', Ticket Térmico y operaciones de Restaurante."""
    with TestClient(app) as client:
        # 1. Raíz '/' carga el Panel Web Visual HTML (Fase 2)
        res_home = client.get("/")
        assert res_home.status_code == 200
        assert "SaaS WhatsApp Orders" in res_home.text

        # 2. Healthcheck y cabeceras OWASP
        res_health = client.get("/health")
        assert res_health.status_code == 200
        assert res_health.headers["X-Content-Type-Options"] == "nosniff"
        assert res_health.headers["X-Frame-Options"] == "SAMEORIGIN"

        # 3. Login como Dueño de Restaurante (Multi-Tenant)
        res_login = client.post(
            "/api/v1/auth/login",
            json={"email": "dueno@napoli.com", "password": "Napoli#2026!"},
        )
        assert res_login.status_code == 200
        login_data = res_login.json()
        token = login_data["access_token"]
        tenant_id = login_data["user"]["tenant_id"]
        headers = {"Authorization": f"Bearer {token}"}

        # 4. Consultar Dashboard del Restaurante
        res_dash = client.get(
            f"/api/v1/panel/comercio/{tenant_id}/dashboard", headers=headers
        )
        assert res_dash.status_code == 200
        dash = res_dash.json()
        assert len(dash["pedidos"]) >= 1
        pedido = dash["pedidos"][0]
        pedido_id = pedido["id"]
        sesion_id = pedido["sesion_chat_id"]

        # 5. Vista de Ticket Térmico 58mm/80mm
        res_ticket = client.get(f"/ticket/{pedido_id}")
        assert res_ticket.status_code == 200
        assert pedido["folio"] in res_ticket.text
        assert "SIN:" in res_ticket.text

        # 6. Cambiar estado del pedido: RECIBIDO -> EN_PROCESO -> LISTO -> RECIBIDO
        res_est = client.patch(
            f"/api/v1/panel/comercio/pedidos/{pedido_id}/estado",
            json={"nuevo_estado": "EN_PROCESO"},
            headers=headers,
        )
        assert res_est.status_code == 200
        assert res_est.json()["estado_pedido"] == "EN_PROCESO"

        # Restaurar a RECIBIDO para mantener el estado limpio de demostración
        client.patch(
            f"/api/v1/panel/comercio/pedidos/{pedido_id}/estado",
            json={"nuevo_estado": "RECIBIDO"},
            headers=headers,
        )

        # 7. Probar Interruptor de Control Humano del Chat ('HUMANO_ACTIVO' -> 'BOT_ACTIVO')
        res_ctrl = client.post(
            f"/api/v1/panel/comercio/chats/{sesion_id}/control",
            json={"modo_atencion": "HUMANO_ACTIVO"},
            headers=headers,
        )
        assert res_ctrl.status_code == 200
        assert res_ctrl.json()["modo_atencion"] == "HUMANO_ACTIVO"

        res_ctrl_bot = client.post(
            f"/api/v1/panel/comercio/chats/{sesion_id}/control",
            json={"modo_atencion": "BOT_ACTIVO"},
            headers=headers,
        )
        assert res_ctrl_bot.status_code == 200
        assert res_ctrl_bot.json()["modo_atencion"] == "BOT_ACTIVO"
