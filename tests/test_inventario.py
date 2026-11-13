import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_template_modular_includes():
    """Verifica que el template index.html modular incluya todas las secciones y scripts."""
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Navbar y vistas
    assert "loginSection" in html
    assert "superAdminSection" in html
    assert "comercioSection" in html
    assert "tabContent_pedidos" in html
    assert "tabContent_menu" in html
    assert "tabContent_inventario" in html
    assert "tabContent_sucursales" in html

    # Modales
    assert "chatModal" in html
    assert "modalNuevoComercio" in html
    assert "modalConfigComercio" in html
    assert "modalItemInventario" in html
    assert "modalAjusteStock" in html

    # Scripts montados
    assert "/static/js/inventario.js" in html
    assert "/static/js/app.js" in html

def test_inventario_api_crud_flow():
    """Verifica el flujo completo de inventario: login, listar, metricas y ajuste rapido de stock."""
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

    # 2. Consultar inventario
    res_inv = client.get(f"/api/v1/panel/comercio/{tenant_id}/inventario", headers=headers)
    assert res_inv.status_code == 200
    data = res_inv.json()

    assert "items" in data
    assert "metricas" in data
    assert "categorias" in data
    assert len(data["items"]) >= 1
    assert data["metricas"]["total_items"] >= 1
    assert data["metricas"]["valor_total_inventario"] > 0

    first_item = data["items"][0]
    item_id = first_item["id"]
    stock_original = first_item["stock_actual"]

    # 3. Ajuste rapido (+5 compra)
    res_ajuste = client.patch(
        f"/api/v1/panel/comercio/inventario/{item_id}/ajustar",
        json={"tipo": "SUMAR", "cantidad": 5.0, "motivo": "Compra de prueba unitaria"},
        headers=headers
    )
    assert res_ajuste.status_code == 200
    ajuste_data = res_ajuste.json()
    assert ajuste_data["stock_nuevo"] == round(stock_original + 5.0, 2)

    # 4. Restaurar stock (-5)
    res_restaurar = client.patch(
        f"/api/v1/panel/comercio/inventario/{item_id}/ajustar",
        json={"tipo": "RESTAR", "cantidad": 5.0, "motivo": "Restaurar stock de prueba"},
        headers=headers
    )
    assert res_restaurar.status_code == 200
    assert res_restaurar.json()["stock_nuevo"] == stock_original
