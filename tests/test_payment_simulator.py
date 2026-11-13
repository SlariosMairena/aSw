import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_simulated_payment_gateway_flow():
    """Prueba el flujo completo de simulación de pago online (Modo 2)."""
    client = TestClient(app)

    # 1. Login dueño de Napoli
    res_login = client.post(
        "/api/v1/auth/login",
        json={"email": "dueno@napoli.com", "password": "Napoli#2026!"}
    )
    assert res_login.status_code == 200
    token = res_login.json()["access_token"]
    tenant_id = res_login.json()["user"]["tenant_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Consultar pedidos
    res_dash = client.get(f"/api/v1/panel/comercio/{tenant_id}/dashboard", headers=headers)
    assert res_dash.status_code == 200
    pedidos = res_dash.json()["pedidos"]
    assert len(pedidos) >= 1
    pedido = pedidos[0]
    pedido_id = pedido["id"]

    # 3. Generar link de pago simulado
    res_link = client.post(f"/api/v1/pagos/checkout-link/{pedido_id}")
    assert res_link.status_code == 200
    link_data = res_link.json()
    assert "checkout_url" in link_data
    assert "session_id" in link_data
    session_id = link_data["session_id"]

    # 4. Renderizar página de checkout simulada
    res_checkout_page = client.get(f"/pagos/checkout/{session_id}?pedido_id={pedido_id}")
    assert res_checkout_page.status_code == 200
    assert "Checkout Seguro" in res_checkout_page.text
    assert "Simular Pago Exitoso" in res_checkout_page.text

    # 5. Simular Webhook de pago aprobado
    res_webhook = client.post(
        "/api/v1/pagos/webhook-simulado",
        json={
            "session_id": session_id,
            "pedido_id": pedido_id,
            "resultado": "EXITOSO",
            "demo_mode": True
        }
    )
    assert res_webhook.status_code == 200
    webhook_data = res_webhook.json()
    assert webhook_data["success"] is True
    assert webhook_data["estado_pago"] == "PAGADO"
    assert "tx_id" in webhook_data

