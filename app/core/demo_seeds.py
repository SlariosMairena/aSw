"""
Plantillas y sembrado idempotente de datos de prueba por giro de negocio:
- RESTAURANTE (Pizzeria & Burger Napoli + plantilla reutilizable)
- CAFETERIA   (Cafe & Reposteria Altura)
- FARMACIA    (Farmacia Salud Total)
- TIENDA      (Urban Style Boutique)
- SUPERMERCADO (Minisuper La Esquina)

Disenado para que cada negocio tenga catalogo amplio, inventario con alertas,
sucursales, usuarios (COMERCIO_ADMIN + SUCURSAL_STAFF) y pedidos historicos/activos
listos para probar Kanban, Inventario, Bot/Tickets y Estadisticas de Ventas.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.saas_models import (
    CategoriaMenu,
    ClienteFrecuente,
    DetallePedido,
    IngredienteOpcionPlato,
    ItemInventario,
    MensajeChatWhatsApp,
    MesaZona,
    PagoTransaccion,
    Pedido,
    PlanSaaS,
    ProductoPlato,
    SesionChatWhatsApp,
    SucursalComercio,
    SuscripcionFacturaSaaS,
    TenantComercio,
    Usuario,
    ZonaEnvioSucursal,
)


def normalize_giro_code(giro_raw: Optional[str]) -> str:
    g = str(giro_raw or "RESTAURANTE").upper().strip()
    if "CAFETER" in g:
        return "CAFETERIA"
    if "FARMAC" in g:
        return "FARMACIA"
    if "TIENDA" in g or "RETAIL" in g:
        return "TIENDA"
    if "SUPER" in g or "MINI" in g:
        return "SUPERMERCADO"
    return "RESTAURANTE"


GIRO_CATALOG_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "RESTAURANTE": {
        "tiene_mesas": True,
        "categorias": [
            "Hamburguesas Artesanales",
            "Pizzas al Horno",
            "Combos Familiares",
            "Bebidas y Postres",
        ],
        "productos": [
            {
                "cat": "Hamburguesas Artesanales",
                "nombre": "Hamburguesa Clásica Doble",
                "desc": "Doble carne 100% res a la parrilla, queso cheddar, pan brioche y salsa especial.",
                "precio": 220.0,
                "removibles": ["Cebolla", "Tomate", "Lechuga", "Pepinillos"],
                "extras": [("Extra Queso Cheddar", "EXTRA", 30.0), ("Tocino Ahumado", "EXTRA", 40.0)],
            },
            {
                "cat": "Hamburguesas Artesanales",
                "nombre": "Burger BBQ Crunch",
                "desc": "Carne res, aros de cebolla crujientes, queso americano y salsa BBQ ahumada.",
                "precio": 245.0,
                "removibles": ["Cebolla", "Salsa BBQ", "Lechuga"],
                "extras": [("Papas Fritas Grandes", "EXTRA", 55.0), ("Carne Extra", "EXTRA", 65.0)],
            },
            {
                "cat": "Hamburguesas Artesanales",
                "nombre": "Chicken Crispy Deluxe",
                "desc": "Pechuga empanizada crujiente con aderezo miel mostaza y ensalada fresca.",
                "precio": 195.0,
                "removibles": ["Tomate", "Lechuga", "Aderezo"],
                "extras": [("Queso Mozzarella", "EXTRA", 25.0)],
            },
            {
                "cat": "Pizzas al Horno",
                "nombre": "Pizza Pepperoni Especial",
                "desc": "Masa artesanal horneada a la piedra, salsa italiana y doble pepperoni.",
                "precio": 340.0,
                "removibles": ["Orégano", "Aceitunas"],
                "extras": [("Borde Relleno de Queso", "EXTRA", 60.0), ("Tamaño Familiar 16p", "VARIANTE", 110.0)],
            },
            {
                "cat": "Pizzas al Horno",
                "nombre": "Pizza Cuatro Quesos",
                "desc": "Mozzarella, parmesano, provolone y queso azul sobre base blanca.",
                "precio": 360.0,
                "removibles": ["Pimienta Negra"],
                "extras": [("Tamaño Familiar 16p", "VARIANTE", 110.0)],
            },
            {
                "cat": "Pizzas al Horno",
                "nombre": "Pizza Suprema de la Casa",
                "desc": "Jamón, pepperoni, champiñones frescos, pimentón y aceitunas negras.",
                "precio": 385.0,
                "removibles": ["Cebolla", "Pimentón", "Aceitunas"],
                "extras": [("Extra Champiñones", "EXTRA", 35.0)],
            },
            {
                "cat": "Combos Familiares",
                "nombre": "Combo Pareja (Pizza + 2 Bebidas)",
                "desc": "Pizza mediana de 2 ingredientes más dos refrescos de lata.",
                "precio": 420.0,
                "removibles": [],
                "extras": [("Aros de Cebolla", "EXTRA", 70.0)],
            },
            {
                "cat": "Combos Familiares",
                "nombre": "Alitas BBQ x12 Piezas",
                "desc": "Doce alitas bañadas en salsa a elección con aderezo ranch y apio.",
                "precio": 290.0,
                "removibles": ["Apio", "Salsa Picante"],
                "extras": [("Papas Gajo", "EXTRA", 65.0)],
            },
            {
                "cat": "Bebidas y Postres",
                "nombre": "Limonada con Hierbabuena",
                "desc": "Limonada natural recién preparada con hielo frappé.",
                "precio": 65.0,
                "removibles": ["Azúcar", "Hielo"],
                "extras": [("Jarra 1 Litro", "VARIANTE", 90.0)],
            },
            {
                "cat": "Bebidas y Postres",
                "nombre": "Brownie con Helado de Vainilla",
                "desc": "Brownie tibio de chocolate artesanal acompañado de bola de helado.",
                "precio": 125.0,
                "disponible": False,
                "removibles": ["Sirope de Chocolate"],
                "extras": [],
            },
        ],
        "inventario": [
            ("Carne Molida Res Premium", "Carnes", "kg", 24.0, 6.0, 210.0, "Carnicería San Martín"),
            ("Queso Mozzarella Rallado", "Lácteos", "kg", 15.0, 4.0, 195.0, "Lácteos La Perfecta"),
            ("Harina Italiana 00", "Secos", "kg", 40.0, 10.0, 42.0, "Distribuidora Central"),
            ("Pepperoni Rebanado", "Embutidos", "kg", 8.5, 3.0, 260.0, "Embutidos Delikatessen"),
            ("Pan Brioche Artesanal", "Panadería", "unidades", 64.0, 20.0, 14.0, "Panadería El Trigo"),
            ("Papas Prefritas Congeladas", "Congelados", "kg", 18.0, 5.0, 75.0, "Frío Alimentos"),
            ("Tomate Saladet Fresco", "Vegetales", "kg", 4.0, 6.0, 38.0, "Mercado Oriental"),
            ("Salsa BBQ Ahumada", "Salsas", "litros", 7.0, 2.0, 115.0, "Salsas Gourmet"),
            ("Refresco Lata 355ml Variado", "Bebidas", "latas", 72.0, 24.0, 20.0, "Embotelladora Nacional"),
            ("Cajas Térmicas de Pizza 14p", "Empaques", "cajas", 0.0, 15.0, 13.0, "Empaques Modernos"),
        ],
    },
    "CAFETERIA": {
        "tiene_mesas": True,
        "categorias": [
            "Cafés Calientes",
            "Bebidas Frías & Frappés",
            "Repostería Artesanal",
            "Desayunos & Brunch",
        ],
        "productos": [
            {
                "cat": "Cafés Calientes",
                "nombre": "Cappuccino Clásico Altura",
                "desc": "Doble espresso de altura de Matagalpa con microespuma de leche sedosa.",
                "precio": 95.0,
                "removibles": ["Canela", "Cacao en Polvo", "Azúcar"],
                "extras": [("Tamaño Grande 16oz", "VARIANTE", 25.0), ("Leche de Almendra", "EXTRA", 25.0)],
            },
            {
                "cat": "Cafés Calientes",
                "nombre": "Latte Vainilla Caramelo",
                "desc": "Espresso balanceado, leche vaporizada y sirope artesanal de vainilla.",
                "precio": 110.0,
                "removibles": ["Sirope Caramelo", "Crema Batida"],
                "extras": [("Shot Extra Espresso", "EXTRA", 20.0), ("Leche Deslactosada", "EXTRA", 15.0)],
            },
            {
                "cat": "Cafés Calientes",
                "nombre": "Americano de Origen",
                "desc": "Grano lavado de Nueva Segovia tostado medio en extracción limpia.",
                "precio": 70.0,
                "removibles": [],
                "extras": [("Tamaño Grande 16oz", "VARIANTE", 20.0)],
            },
            {
                "cat": "Bebidas Frías & Frappés",
                "nombre": "Frappé Mocha Belga",
                "desc": "Café frío licuado con chocolate belga, hielo y crema batida.",
                "precio": 135.0,
                "removibles": ["Crema Batida", "Chispas de Chocolate"],
                "extras": [("Shot Extra Espresso", "EXTRA", 20.0)],
            },
            {
                "cat": "Bebidas Frías & Frappés",
                "nombre": "Cold Brew Naranja Tónica",
                "desc": "Extracción en frío de 18 horas con agua tónica y rodaja de naranja.",
                "precio": 120.0,
                "removibles": ["Rodaja de Naranja", "Hielo"],
                "extras": [],
            },
            {
                "cat": "Bebidas Frías & Frappés",
                "nombre": "Matcha Latte Helado",
                "desc": "Té verde ceremonial japonés batido con leche fría a elección.",
                "precio": 140.0,
                "removibles": ["Endulzante"],
                "extras": [("Leche de Coco", "EXTRA", 25.0)],
            },
            {
                "cat": "Repostería Artesanal",
                "nombre": "Croissant de Almendras",
                "desc": "Hojaldre francés horneado diario relleno de crema frangipane.",
                "precio": 115.0,
                "removibles": ["Azúcar Glass"],
                "extras": [("Calentar en Horno", "EXTRA", 0.0)],
            },
            {
                "cat": "Repostería Artesanal",
                "nombre": "Cheesecake de Maracuyá",
                "desc": "Porción cremosa de queso crema sobre galleta con coulis de maracuyá.",
                "precio": 145.0,
                "removibles": ["Coulis de Fruta"],
                "extras": [],
            },
            {
                "cat": "Desayunos & Brunch",
                "nombre": "Sándwich Ciabatta Pavo y Pesto",
                "desc": "Pan ciabatta tostado, pechuga de pavo, queso mozzarella y pesto fresco.",
                "precio": 185.0,
                "removibles": ["Tomate", "Rúcula", "Pesto"],
                "extras": [("Aguacate Extra", "EXTRA", 30.0)],
            },
            {
                "cat": "Repostería Artesanal",
                "nombre": "Rol de Canela Glaseado",
                "desc": "Masa suave de brioche con canela ceilan y glaseado de queso crema.",
                "precio": 98.0,
                "disponible": False,
                "removibles": ["Glaseado"],
                "extras": [],
            },
        ],
        "inventario": [
            ("Café Arábigo en Grano (Matagalpa)", "Café en Grano", "kg", 14.0, 4.0, 320.0, "Finca La Esperanza"),
            ("Leche Entera Fresca", "Lácteos", "litros", 36.0, 12.0, 34.0, "Lácteos Eskimos"),
            ("Leche de Almendra Barista", "Lácteos Vegetales", "litros", 5.0, 6.0, 95.0, "Importadora Verde"),
            ("Leche Deslactosada 1L", "Lácteos", "litros", 18.0, 6.0, 42.0, "Lácteos La Perfecta"),
            ("Sirope Vainilla Francés", "Jarabes", "botellas", 6.0, 2.0, 240.0, "Monin Centroamérica"),
            ("Chocolate Belga 70% Cacao", "Repostería", "kg", 4.5, 2.0, 380.0, "Chocolatería Selecta"),
            ("Mantequilla Francesa", "Repostería", "kg", 9.0, 3.0, 260.0, "Distribuidora Panadera"),
            ("Té Matcha Ceremonial", "Tés", "bolsas", 0.0, 2.0, 450.0, "Importadora Asia"),
            ("Vasos Biodegradables 16oz", "Empaques", "unidades", 320.0, 100.0, 4.5, "EcoPack Nicaragua"),
            ("Tapas Planas & Domo 16oz", "Empaques", "unidades", 290.0, 100.0, 2.0, "EcoPack Nicaragua"),
        ],
    },
    "FARMACIA": {
        "tiene_mesas": False,
        "categorias": [
            "Analgésicos y Fiebre",
            "Antigripales y Respiratorio",
            "Vitaminas y Suplementos",
            "Botiquín y Cuidado Personal",
        ],
        "productos": [
            {
                "cat": "Analgésicos y Fiebre",
                "nombre": "Acetaminofén 500mg (Caja x20)",
                "desc": "Alivio rápido para dolor de cabeza, malestar general y fiebre. Sin receta.",
                "precio": 85.0,
                "extras": [("Blíster x10 Tabletas", "VARIANTE", 0.0), ("Caja x50 Tabletas", "VARIANTE", 95.0)],
            },
            {
                "cat": "Analgésicos y Fiebre",
                "nombre": "Ibuprofeno 400mg Cápsula Blanda",
                "desc": "Antiinflamatorio y analgésico de rápida absorción. Blíster de 10 cápsulas.",
                "precio": 115.0,
                "extras": [("Caja x30 Cápsulas", "VARIANTE", 190.0)],
            },
            {
                "cat": "Analgésicos y Fiebre",
                "nombre": "Naproxeno Sódico 550mg",
                "desc": "Analgésico muscular y articular de acción prolongada cada 12 horas.",
                "precio": 140.0,
                "extras": [("Caja x20 Tabletas", "VARIANTE", 120.0)],
            },
            {
                "cat": "Antigripales y Respiratorio",
                "nombre": "Antigripal Día y Noche (12 Tabs)",
                "desc": "Descongestionante nasal, antihistamínico y alivio de dolor de garganta.",
                "precio": 130.0,
                "extras": [("Sobre Individual x4", "VARIANTE", 0.0)],
            },
            {
                "cat": "Antigripales y Respiratorio",
                "nombre": "Jarabe Expectorante Ambroxol 120ml",
                "desc": "Mucolítico para tos con flema, sabor fresa, incluye vaso dosificador.",
                "precio": 165.0,
                "extras": [("Presentación Pediátrica", "VARIANTE", 0.0)],
            },
            {
                "cat": "Antigripales y Respiratorio",
                "nombre": "Spray Nasal Agua de Mar 100ml",
                "desc": "Solución isotónica natural para higiene y descongestión nasal diaria.",
                "precio": 240.0,
                "extras": [],
            },
            {
                "cat": "Vitaminas y Suplementos",
                "nombre": "Vitamina C + Zinc Efervescente (Tubo x10)",
                "desc": "Refuerzo inmunológico diario de 1000mg sabor naranja sin azúcar.",
                "precio": 195.0,
                "extras": [("Pack 2 Tubos", "VARIANTE", 160.0)],
            },
            {
                "cat": "Vitaminas y Suplementos",
                "nombre": "Complejo B Neurobión Forte x30",
                "desc": "Vitaminas B1, B6 y B12 para energía y salud neuromuscular.",
                "precio": 310.0,
                "extras": [],
            },
            {
                "cat": "Botiquín y Cuidado Personal",
                "nombre": "Suero Oral Electrolitos Coco 625ml",
                "desc": "Hidratación médica balanceada con sodio, potasio y zinc.",
                "precio": 75.0,
                "extras": [("Pack x3 Botellas", "VARIANTE", 130.0)],
            },
            {
                "cat": "Botiquín y Cuidado Personal",
                "nombre": "Termómetro Digital Punta Flexible",
                "desc": "Lectura clínica en 10 segundos con memoria de última medición.",
                "precio": 260.0,
                "disponible": False,
                "extras": [],
            },
        ],
        "inventario": [
            ("Acetaminofén 500mg Caja x20", "Analgésicos", "cajas", 85.0, 20.0, 48.0, "Laboratorios Ramos"),
            ("Ibuprofeno 400mg Blíster x10", "Analgésicos", "blísters", 62.0, 15.0, 68.0, "Bayer Centroamérica"),
            ("Antigripal Día/Noche Caja x12", "Respiratorio", "cajas", 40.0, 15.0, 79.0, "Droguería Universal"),
            ("Ambroxol Jarabe 120ml", "Respiratorio", "frascos", 28.0, 10.0, 98.0, "Laboratorios Solka"),
            ("Vitamina C 1000mg + Zinc Tubo", "Vitaminas", "tubos", 8.0, 12.0, 118.0, "Medipharm Nica"),
            ("Suero Rehidratación Oral 625ml", "Hidratación", "botellas", 54.0, 18.0, 44.0, "Pisa Farmacéutica"),
            ("Alcohol Clínico 70% 500ml", "Botiquín", "frascos", 38.0, 12.0, 39.0, "Química Industrial"),
            ("Gasas Estériles 10x10cm Caja", "Botiquín", "cajas", 22.0, 8.0, 55.0, "Insumos Médicos del Sur"),
            ("Protector Solar FPS 50+ 120ml", "Dermatología", "unidades", 14.0, 5.0, 290.0, "La Roche Distribuciones"),
            ("Oxímetro Digital de Pulso", "Equipos Médicos", "unidades", 0.0, 3.0, 410.0, "MediEquip Nicaragua"),
        ],
    },
    "TIENDA": {
        "tiene_mesas": False,
        "categorias": [
            "Camisetas & Tops",
            "Pantalones & Jeans",
            "Calzado Urbano",
            "Accesorios & Mochilas",
        ],
        "productos": [
            {
                "cat": "Camisetas & Tops",
                "nombre": "Camiseta Oversize Algodón Peinado",
                "desc": "Corte moderno 100% algodón 220gr, disponible en negro, hueso y oliva.",
                "precio": 490.0,
                "extras": [("Talla S / M / L", "VARIANTE", 0.0), ("Empaque Regalo Premium", "EXTRA", 60.0)],
            },
            {
                "cat": "Camisetas & Tops",
                "nombre": "Polo Piqué Clásica Bordada",
                "desc": "Cuello estructurado respirable ideal para oficina o fin de semana.",
                "precio": 620.0,
                "extras": [("Talla XL", "VARIANTE", 40.0)],
            },
            {
                "cat": "Camisetas & Tops",
                "nombre": "Sudadera Hoodie Urban Fleece",
                "desc": "Felpa gruesa suave por dentro con bolsillo canguro y capucha doble.",
                "precio": 980.0,
                "extras": [("Empaque Regalo Premium", "EXTRA", 60.0)],
            },
            {
                "cat": "Pantalones & Jeans",
                "nombre": "Jean Slim Stretch Azul Índigo",
                "desc": "Denim elástico cómodo de 11 oz, tiro medio y costura reforzada.",
                "precio": 1150.0,
                "extras": [("Ajuste de Ruedo Incluido", "EXTRA", 0.0)],
            },
            {
                "cat": "Pantalones & Jeans",
                "nombre": "Pantalón Cargo Ripstop Arena",
                "desc": "Tela resistente con bolsillos laterales funcionales y ajuste en tobillo.",
                "precio": 1080.0,
                "extras": [],
            },
            {
                "cat": "Calzado Urbano",
                "nombre": "Sneakers Blancos Cuero Vegano",
                "desc": "Suela cosida antiderrapante y plantilla memory foam para uso diario.",
                "precio": 1850.0,
                "extras": [("Protector Impermeabilizante", "EXTRA", 180.0)],
            },
            {
                "cat": "Calzado Urbano",
                "nombre": "Sandalias Anatómicas Minimal",
                "desc": "Eva ultraligera de doble densidad, resistentes al agua.",
                "precio": 590.0,
                "extras": [],
            },
            {
                "cat": "Accesorios & Mochilas",
                "nombre": "Mochila Antirrobo Laptop 15.6p",
                "desc": "Tela impermeable, puerto USB externo y respaldo acolchado.",
                "precio": 1350.0,
                "extras": [("Candado TSA", "EXTRA", 150.0)],
            },
            {
                "cat": "Accesorios & Mochilas",
                "nombre": "Gorra Minimalista Twill Lavado",
                "desc": "Broche metálico trasero ajustable y visera curva.",
                "precio": 340.0,
                "extras": [],
            },
            {
                "cat": "Calzado Urbano",
                "nombre": "Botines Chelsea Gamuza Camel",
                "desc": "Edición de temporada con elástico lateral y suela crepe.",
                "precio": 2250.0,
                "disponible": False,
                "extras": [],
            },
        ],
        "inventario": [
            ("Camiseta Oversize Negra (Stock M/L)", "Camisetas", "piezas", 42.0, 12.0, 230.0, "Textiles Centroamérica"),
            ("Camiseta Oversize Hueso (Stock S/M)", "Camisetas", "piezas", 35.0, 12.0, 230.0, "Textiles Centroamérica"),
            ("Polo Piqué Azul Marino", "Camisetas", "piezas", 24.0, 8.0, 310.0, "Confecciones Atlas"),
            ("Hoodie Urban Fleece Gris", "Abrigos", "piezas", 16.0, 6.0, 520.0, "Importadora Moda CR"),
            ("Jean Slim Stretch Ínidigo T30-34", "Pantalones", "piezas", 28.0, 10.0, 610.0, "Denim Factory"),
            ("Cargo Ripstop Arena T30-34", "Pantalones", "piezas", 5.0, 8.0, 580.0, "Denim Factory"),
            ("Sneakers Blancos Talla 40-43", "Calzado", "pares", 19.0, 6.0, 960.0, "Calzado Urban Hub"),
            ("Mochila Antirrobo 15.6p Negra", "Accesorios", "unidades", 14.0, 5.0, 690.0, "TechBags Imports"),
            ("Gorra Twill Oliva", "Accesorios", "unidades", 30.0, 10.0, 145.0, "Accesorios Del Valle"),
            ("Cajas Kraft Empaque Regalo", "Empaques", "unidades", 0.0, 15.0, 22.0, "Empaques Modernos"),
        ],
    },
    "SUPERMERCADO": {
        "tiene_mesas": False,
        "categorias": [
            "Canasta Básica",
            "Lácteos y Huevos",
            "Bebidas y Jugos",
            "Limpieza del Hogar",
        ],
        "productos": [
            {
                "cat": "Canasta Básica",
                "nombre": "Arroz Grano Largo Premium 5 Lb",
                "desc": "Arroz seleccionado limpio 95/5, bolsa sellada de 5 libras.",
                "precio": 118.0,
                "extras": [("Saco Familiar 25 Lb", "VARIANTE", 420.0)],
            },
            {
                "cat": "Canasta Básica",
                "nombre": "Frijol Rojo Seda Nacional 2 Lb",
                "desc": "Cosecha fresca de rápida cocción, seleccionado sin impurezas.",
                "precio": 78.0,
                "extras": [("Bolsa 5 Lb", "VARIANTE", 110.0)],
            },
            {
                "cat": "Canasta Básica",
                "nombre": "Aceite Vegetal Puro 900ml",
                "desc": "Botella de 900ml libre de colesterol para cocinar y freír.",
                "precio": 82.0,
                "extras": [("Galón 3.78 Litros", "VARIANTE", 240.0)],
            },
            {
                "cat": "Lácteos y Huevos",
                "nombre": "Cajilla de Huevos Rojos x30",
                "desc": "Huevos frescos tamaño mediano-grande de granja local.",
                "precio": 165.0,
                "extras": [("Media Cajilla x15", "VARIANTE", 0.0)],
            },
            {
                "cat": "Lácteos y Huevos",
                "nombre": "Leche UHT Descremada 1 Litro",
                "desc": "Envase Tetra Pak larga duración fortificada con vitaminas A y D.",
                "precio": 46.0,
                "extras": [("Pack x6 Litros", "VARIANTE", 220.0)],
            },
            {
                "cat": "Lácteos y Huevos",
                "nombre": "Queso Seco Chontaleño (1 Libra)",
                "desc": "Queso artesanal prensado ideal para acompañar gallo pinto.",
                "precio": 95.0,
                "extras": [],
            },
            {
                "cat": "Bebidas y Jugos",
                "nombre": "Agua Purificada Galón 3.78L",
                "desc": "Agua baja en sodio con tapa de seguridad.",
                "precio": 48.0,
                "extras": [("Botellón 5 Galones (Solo Líquido)", "VARIANTE", 25.0)],
            },
            {
                "cat": "Bebidas y Jugos",
                "nombre": "Jugo de Naranja 100% Natural 1L",
                "desc": "Sin azúcares añadidos, refrigerado listo para servir.",
                "precio": 72.0,
                "extras": [],
            },
            {
                "cat": "Limpieza del Hogar",
                "nombre": "Detergente Multiusos Limón 2 Kg",
                "desc": "Espuma activa biodegradable para ropa blanca y de color.",
                "precio": 135.0,
                "extras": [],
            },
            {
                "cat": "Limpieza del Hogar",
                "nombre": "Papel Higiénico Doble Hoja (Pack x12)",
                "desc": "Rollos rendidores ultrasuaves de 250 hojas cada uno.",
                "precio": 190.0,
                "disponible": False,
                "extras": [],
            },
        ],
        "inventario": [
            ("Arroz Grano Largo 5 Lb", "Granos Básicos", "bolsas", 68.0, 20.0, 92.0, "Arrocera El Dorado"),
            ("Frijol Rojo Seda 2 Lb", "Granos Básicos", "bolsas", 55.0, 20.0, 58.0, "Acopio Norte"),
            ("Aceite Vegetal 900ml", "Despensa", "botellas", 44.0, 15.0, 63.0, "Aceitera Corona"),
            ("Huevos Frescos Cajilla x30", "Lácteos y Huevos", "cajillas", 9.0, 12.0, 132.0, "Avícola San José"),
            ("Leche UHT 1 Litro", "Lácteos y Huevos", "cajas", 74.0, 24.0, 35.0, "Lácteos La Perfecta"),
            ("Queso Seco Artesanal", "Lácteos y Huevos", "libras", 26.0, 10.0, 72.0, "Cooperativa Chontales"),
            ("Agua Purificada Galón", "Bebidas", "galones", 38.0, 15.0, 29.0, "Fuente Pura S.A."),
            ("Café Instantáneo Frasco 170g", "Despensa", "frascos", 22.0, 8.0, 145.0, "Distribuidora Alimentos"),
            ("Detergente Polvo 2 Kg", "Limpieza", "bolsas", 31.0, 10.0, 98.0, "Química Hogar"),
            ("Cloro Desinfectante Galón", "Limpieza", "galones", 0.0, 8.0, 54.0, "Química Hogar"),
        ],
    },
}


# Negocios completos de demostracion (CAFETERIA, FARMACIA, TIENDA, SUPERMERCADO)
DEMO_BUSINESSES: List[Dict[str, Any]] = [
    {
        "slug": "altura",
        "giro": "CAFETERIA",
        "nombre_comercial": "Café & Repostería Altura",
        "telefono_propietario": "+50588441100",
        "email_dueno": "dueno@altura.com",
        "pass_dueno": "Altura#2026!",
        "nombre_dueno": "Sofía Mendoza (Dueña Altura)",
        "email_staff": "barra.galerias@altura.com",
        "pass_staff": "Barra#2026!",
        "nombre_staff": "Barista Sucursal Galerías",
        "banco_texto": "Banco: Lafise Bancentro | Cuenta Córdobas: 102-448910-3 | Titular: Café Altura S.A.",
        "sucursales": [
            ("Sucursal Galerías", "Altura-Galerias", "Galerías Santo Domingo, Local 14", 45.0, "Colonia Centroamérica", "Villa Fontana"),
            ("Sucursal Los Robles", "Altura-Robles", "De Plaza El Sol 2c al sur, Los Robles", 40.0, "Los Robles", "Altamira"),
        ],
        "mesas": ["Mesa 1 - Ventanal Café", "Mesa 2 - Terraza Jardín"],
        "clientes": [
            ("50588112201", "Valeria Castro", "Villa Fontana, Casa #18"),
            ("50588112202", "Diego Morales", "Los Robles, 3ra Etapa #44"),
            ("50588112203", "Camila Ortiz", "Altamira D'Este #102"),
            ("50588112204", "Andrés Pineda", "Colonia Centroamérica #55"),
        ],
    },
    {
        "slug": "saludtotal",
        "giro": "FARMACIA",
        "nombre_comercial": "Farmacia Salud Total",
        "telefono_propietario": "+50588552200",
        "email_dueno": "dueno@saludtotal.com",
        "pass_dueno": "Farmacia#2026!",
        "nombre_dueno": "Dra. Elena Rivas (Dueña Salud Total)",
        "email_staff": "despacho.metro@saludtotal.com",
        "pass_staff": "Despacho#2026!",
        "nombre_staff": "Regencia Metrocentro",
        "banco_texto": "Banco: BAC Credomatic | Cuenta Córdobas: 368-902311-1 | Titular: Farmacia Salud Total S.A.",
        "sucursales": [
            ("Sucursal Metrocentro", "Salud-Metro", "Rotonda Rubén Darío, 150m Este", 50.0, "Bolonia / Centro", "Metrocentro / Altamira"),
            ("Sucursal Bello Horizonte", "Salud-Bello", "Rotonda Bello Horizonte, Módulo B4", 45.0, "Bello Horizonte", "Ciudad Jardín"),
        ],
        "mesas": [],
        "clientes": [
            ("50588223301", "Roberto Jarquín", "Bolonia, de Canal 2 1c abajo"),
            ("50588223302", "Lucía Espinoza", "Bello Horizonte, Casa F-19"),
            ("50588223303", "Mario Bermúdez", "Altamira, Pista Principal #78"),
            ("50588223304", "Paola Zelaya", "Ciudad Jardín, Casa L-08"),
        ],
    },
    {
        "slug": "urbanstyle",
        "giro": "TIENDA",
        "nombre_comercial": "Urban Style Boutique",
        "telefono_propietario": "+50588663300",
        "email_dueno": "dueno@urbanstyle.com",
        "pass_dueno": "Tienda#2026!",
        "nombre_dueno": "Mateo Solís (Dueño Urban Style)",
        "email_staff": "ventas.tienda@urbanstyle.com",
        "pass_staff": "Ventas#2026!",
        "nombre_staff": "Despacho Multicentro",
        "banco_texto": "Banco: Banpro | Cuenta Córdobas: 10023-771204-8 | Titular: Urban Style Retail Ltda.",
        "sucursales": [
            ("Tienda Multicentro", "Urban-Multi", "Multicentro Las Américas, Planta 2", 60.0, "Managua Urbana", "Carretera Masaya"),
            ("Tienda Plaza España", "Urban-Espana", "Plaza España, Local 8", 50.0, "Plaza España / Montoya", "Las Brisas"),
        ],
        "mesas": [],
        "clientes": [
            ("50588334401", "Fernanda López", "Residencial Las Colinas #12"),
            ("50588334402", "Kevin Sequeira", "Las Brisas, Calle Principal #33"),
            ("50588334403", "Daniela Reyes", "Km 8.5 Carretera a Masaya"),
            ("50588334404", "Jorge Castillo", "Residencial El Dorado #91"),
        ],
    },
    {
        "slug": "laesquina",
        "giro": "SUPERMERCADO",
        "nombre_comercial": "Minisúper La Esquina",
        "telefono_propietario": "+50588774400",
        "email_dueno": "dueno@laesquina.com",
        "pass_dueno": "Super#2026!",
        "nombre_dueno": "Don Ernesto Vega (Dueño La Esquina)",
        "email_staff": "caja.norte@laesquina.com",
        "pass_staff": "Caja#2026!",
        "nombre_staff": "Surtido Carretera Norte",
        "banco_texto": "Banco: Banco Avanz | Cuenta Córdobas: 441-009812-5 | Titular: Comercial La Esquina",
        "sucursales": [
            ("Sucursal Carretera Norte", "Esquina-Norte", "Km 5.5 Carretera Norte, contiguo a Gasolinera", 35.0, "Zona Norte", "Portezuelo / Rubenia"),
            ("Sucursal Las Colinas", "Esquina-Colinas", "Entrada Las Colinas, Módulo 3", 45.0, "Las Colinas", "Santo Domingo"),
        ],
        "mesas": [],
        "clientes": [
            ("50588445501", "Doña Marta Silva", "Colonia 10 de Junio, Casa #204"),
            ("50588445502", "Luis Blandón", "Villa Progreso, Andén 4 #11"),
            ("50588445503", "Karla Gutiérrez", "Las Colinas, Segunda Entrada #29"),
            ("50588445504", "Héctor Moncada", "Residencial Rubenia #67"),
        ],
    },
]


def _build_demo_orders_for_tenant(
    db: Session,
    comercio: TenantComercio,
    sucursales: List[SucursalComercio],
    productos: List[ProductoPlato],
    clientes_spec: List[tuple],
    giro_code: str,
    prefix_folio: str,
    ensure_latest_has_sin: bool = False,
) -> None:
    """Genera 10 pedidos variados en los últimos 7 días para probar Kanban y Estadísticas."""
    if not sucursales or not productos:
        return

    clientes_objs: List[ClienteFrecuente] = []
    for tel, nom, dir_txt in clientes_spec:
        cli = (
            db.query(ClienteFrecuente)
            .filter(
                ClienteFrecuente.tenant_id == comercio.id,
                ClienteFrecuente.telefono_whatsapp == tel,
            )
            .first()
        )
        if not cli:
            cli = ClienteFrecuente(
                tenant_id=comercio.id,
                telefono_whatsapp=tel,
                nombre_cliente=nom,
                ultima_direccion=dir_txt,
                total_pedidos_realizados=2,
            )
            db.add(cli)
            db.flush()
        clientes_objs.append(cli)

    now = datetime.now(timezone.utc)
    usa_mesas = giro_code in ("RESTAURANTE", "CAFETERIA")

    # (days_ago, estado_pedido, metodo_pago, estado_pago, tipo_entrega, item_indices)
    schedule = [
        (6, "ENTREGADO", "EFECTIVO", "PAGADO", "DOMICILIO", [(0, 2), (4, 1)]),
        (5, "ENTREGADO", "TARJETA_ONLINE", "PAGADO", "DOMICILIO", [(1, 1), (6, 2)]),
        (4, "ENTREGADO", "TRANSFERENCIA", "PAGADO", "RECOGER", [(2, 2), (7, 1)]),
        (3, "CANCELADO", "EFECTIVO", "PENDIENTE", "DOMICILIO", [(3, 1)]),
        (2, "ENTREGADO", "TARJETA_ONLINE", "PAGADO", "MESA" if usa_mesas else "RECOGER", [(0, 1), (5, 2), (8, 1)]),
        (1, "ENTREGADO", "EFECTIVO", "PAGADO", "DOMICILIO", [(1, 2), (3, 1)]),
        (1, "LISTO", "TARJETA_ONLINE", "PAGADO", "RECOGER", [(4, 2), (8, 2)]),
        (0, "LISTO", "TRANSFERENCIA", "PAGADO", "DOMICILIO", [(2, 1), (6, 1)]),
        (0, "EN_PROCESO", "TARJETA_ONLINE", "PAGADO", "DOMICILIO", [(0, 1), (7, 2)]),
        (0, "RECIBIDO", "EFECTIVO", "PENDIENTE", "DOMICILIO", [(1, 1), (5, 1)]),
    ]

    for idx, (days_ago, est_ped, met_pago, est_pago, tipo_ent, items_idx) in enumerate(schedule):
        folio = f"{prefix_folio}-{1100 + idx}"
        if db.query(Pedido).filter(Pedido.folio == folio).first():
            continue

        suc = sucursales[idx % len(sucursales)]
        cli = clientes_objs[idx % len(clientes_objs)]
        created_at = now - timedelta(days=days_ago, hours=(idx % 5) + 1)

        line_items = []
        subtotal = 0.0
        resumen_parts = []
        for p_idx, qty in items_idx:
            prod = productos[p_idx % len(productos)]
            unit_price = float(prod.precio_base or 100.0)
            line_sub = round(unit_price * qty, 2)
            subtotal += line_sub
            resumen_parts.append(f"{qty}x {prod.nombre}")

            rem_json = "[]"
            if ensure_latest_has_sin and idx == len(schedule) - 1 and len(line_items) == 0:
                rem_json = json.dumps(["Cebolla", "Tomate"], ensure_ascii=False)

            line_items.append((prod, qty, unit_price, line_sub, rem_json))

        costo_envio = float(suc.costo_envio_base or 40.0) if tipo_ent == "DOMICILIO" else 0.0
        total = round(subtotal + costo_envio, 2)
        paga_con = None
        cambio = None
        if met_pago == "EFECTIVO":
            paga_con = float(((int(total) // 500) + 1) * 500)
            cambio = round(paga_con - total, 2)

        resumen_txt = (
            f"{cli.nombre_cliente} ordenó {', '.join(resumen_parts)} desde {suc.nombre_sucursal}. "
            f"Entrega: {tipo_ent}. Pago: {met_pago} ({est_pago})."
        )
        sesion = SesionChatWhatsApp(
            tenant_id=comercio.id,
            sucursal_id=suc.id,
            telefono_cliente=cli.telefono_whatsapp,
            nombre_cliente=cli.nombre_cliente,
            modo_atencion="BOT_ACTIVO",
            estado_conversacion="COMPLETADO",
            resumen_ia_chat=resumen_txt,
            como_se_realizo_resumen=(
                f"1. Ingreso por link [{suc.codigo_whatsapp}] -> 2. Selección en catálogo ({giro_code}) -> "
                f"3. Entrega {tipo_ent} -> 4. Confirmación {met_pago}."
            ),
            activa=True,
            creado_en=created_at,
            ultimo_mensaje_en=created_at,
        )
        db.add(sesion)
        db.flush()

        db.add_all(
            [
                MensajeChatWhatsApp(
                    sesion_id=sesion.id,
                    tenant_id=comercio.id,
                    remitente="CLIENTE",
                    tipo_contenido="TEXTO",
                    contenido=f"Hola! Quiero pedir {', '.join(resumen_parts)} ({suc.nombre_sucursal}).",
                    creado_en=created_at,
                ),
                MensajeChatWhatsApp(
                    sesion_id=sesion.id,
                    tenant_id=comercio.id,
                    remitente="BOT",
                    tipo_contenido="TEXTO",
                    contenido=f"Pedido #{folio} confirmado por C$ {total:.2f}. Estado: {est_ped}.",
                    creado_en=created_at,
                ),
            ]
        )

        pedido = Pedido(
            folio=folio,
            tenant_id=comercio.id,
            sucursal_id=suc.id,
            sesion_chat_id=sesion.id,
            cliente_frecuente_id=cli.id,
            telefono_cliente=cli.telefono_whatsapp,
            nombre_cliente=cli.nombre_cliente,
            tipo_entrega=tipo_ent,
            direccion_entrega=cli.ultima_direccion if tipo_ent == "DOMICILIO" else "Retiro en local",
            zona_envio_nombre=suc.nombre_sucursal,
            estado_pedido=est_ped,
            subtotal=round(subtotal, 2),
            costo_envio=round(costo_envio, 2),
            total=total,
            resumen_chat_ia=resumen_txt,
            como_se_realizo_pedido=sesion.como_se_realizo_resumen,
            notas_especiales="Pedido de demostración para estadísticas y flujo operativo.",
            metodo_pago=met_pago,
            estado_pago=est_pago,
            paga_con_monto=paga_con,
            cambio_a_entregar=cambio,
            creado_en=created_at,
            actualizado_en=created_at,
        )
        db.add(pedido)
        db.flush()

        for prod, qty, unit_price, line_sub, rem_json in line_items:
            db.add(
                DetallePedido(
                    pedido_id=pedido.id,
                    plato_id=prod.id,
                    nombre_plato=prod.nombre,
                    cantidad=qty,
                    precio_unitario_base=unit_price,
                    sin_ingredientes_json=rem_json,
                    extras_json="[]",
                    precio_extras_unitario=0.0,
                    subtotal_linea=line_sub,
                )
            )

        db.add(
            PagoTransaccion(
                tenant_id=comercio.id,
                pedido_id=pedido.id,
                modo_operacion="MODO_2_PASARELA" if met_pago == "TARJETA_ONLINE" else "MODO_1_DIRECTO",
                proveedor_api="SIMULATED_STRIPE" if met_pago == "TARJETA_ONLINE" else None,
                metodo=met_pago,
                monto=total,
                moneda=comercio.moneda or "NIO",
                estado="APROBADO" if est_pago == "PAGADO" else "PENDIENTE",
                paga_con=paga_con,
                cambio=cambio,
                creado_en=created_at,
            )
        )


def populate_tenant_demo_data(
    db: Session,
    comercio: TenantComercio,
    giro_raw: Optional[str] = None,
    clientes_spec: Optional[List[tuple]] = None,
    ensure_latest_has_sin: bool = False,
) -> Dict[str, int]:
    """
    Puebla un TenantComercio existente (por ejemplo recien creado desde SuperAdmin)
    con categorias, catalogo amplio, inventario y pedidos de muestra segun su giro.
    Evita duplicar categorias, productos o insumos existentes por nombre.
    """
    giro_code = normalize_giro_code(giro_raw or comercio.giro)
    tpl = GIRO_CATALOG_TEMPLATES[giro_code]
    comercio.tiene_mesas_fisicas = bool(tpl["tiene_mesas"])

    sucursales = (
        db.query(SucursalComercio)
        .filter(SucursalComercio.tenant_id == comercio.id)
        .order_by(SucursalComercio.id.asc())
        .all()
    )
    if not sucursales:
        return {"productos_creados": 0, "insumos_creados": 0, "pedidos_creados": 0}

    suc_principal = sucursales[0]

    # 1. Categorias y Productos
    existing_cats = {
        c.nombre: c
        for c in db.query(CategoriaMenu).filter(CategoriaMenu.tenant_id == comercio.id).all()
    }
    cat_map: Dict[str, CategoriaMenu] = dict(existing_cats)
    for idx, cat_name in enumerate(tpl["categorias"], start=1):
        if cat_name not in cat_map:
            cat_obj = CategoriaMenu(tenant_id=comercio.id, nombre=cat_name, orden=idx)
            db.add(cat_obj)
            db.flush()
            cat_map[cat_name] = cat_obj

    existing_prods = {
        p.nombre: p
        for p in db.query(ProductoPlato).filter(ProductoPlato.tenant_id == comercio.id).all()
    }
    productos_creados = 0
    productos_lista: List[ProductoPlato] = list(existing_prods.values())

    for p_def in tpl["productos"]:
        if p_def["nombre"] in existing_prods:
            continue
        cat_obj = cat_map.get(p_def["cat"]) or list(cat_map.values())[0]
        plato = ProductoPlato(
            tenant_id=comercio.id,
            categoria_id=cat_obj.id,
            nombre=p_def["nombre"],
            descripcion=p_def["desc"],
            precio_base=p_def["precio"],
            disponible_global=p_def.get("disponible", True),
        )
        db.add(plato)
        db.flush()

        for ing_name in p_def.get("removibles", []):
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato.id,
                    nombre=ing_name,
                    tipo="INGREDIENTE_BASE",
                    precio_adicional=0.0,
                    es_removible=True,
                    disponible_global=True,
                )
            )
        for ext_name, ext_tipo, ext_precio in p_def.get("extras", []):
            db.add(
                IngredienteOpcionPlato(
                    tenant_id=comercio.id,
                    plato_id=plato.id,
                    nombre=ext_name,
                    tipo=ext_tipo,
                    precio_adicional=ext_precio,
                    es_removible=False,
                    disponible_global=True,
                )
            )
        productos_creados += 1
        productos_lista.append(plato)

    # 2. Inventario
    existing_inv = {
        i.nombre_insumo
        for i in db.query(ItemInventario).filter(ItemInventario.tenant_id == comercio.id).all()
    }
    insumos_creados = 0
    for idx, (nom, cat, und, stk, min_stk, costo, prov) in enumerate(tpl["inventario"]):
        if nom in existing_inv:
            continue
        target_suc = sucursales[idx % len(sucursales)]
        nota = None
        if stk <= 0:
            nota = "Agotado: programar reposición inmediata"
        elif stk <= min_stk:
            nota = "Stock bajo: reorden sugerido"
        db.add(
            ItemInventario(
                tenant_id=comercio.id,
                sucursal_id=target_suc.id,
                nombre_insumo=nom,
                categoria=cat,
                unidad_medida=und,
                stock_actual=stk,
                stock_minimo=min_stk,
                costo_unitario=costo,
                proveedor=prov,
                notas=nota,
            )
        )
        insumos_creados += 1

    # 3. Mesas si aplica y no tiene
    if tpl["tiene_mesas"] and db.query(MesaZona).filter(MesaZona.tenant_id == comercio.id).count() == 0:
        db.add_all(
            [
                MesaZona(
                    tenant_id=comercio.id,
                    sucursal_id=suc_principal.id,
                    nombre_mesa="Mesa 1 - Salón Principal",
                    capacidad_personas=4,
                    monto_anticipo_reserva=150.0,
                ),
                MesaZona(
                    tenant_id=comercio.id,
                    sucursal_id=suc_principal.id,
                    nombre_mesa="Mesa 2 - Terraza",
                    capacidad_personas=6,
                    monto_anticipo_reserva=250.0,
                ),
            ]
        )

    # 4. Pedidos historicos y activos (si tiene menos de 4)
    pedidos_antes = db.query(Pedido).filter(Pedido.tenant_id == comercio.id).count()
    prefix = f"T{comercio.id}"
    if pedidos_antes < 4 and productos_lista:
        clientes_default = clientes_spec or [
            ("50588001001", "Andrea Morales", "Zona Central, Casa #14"),
            ("50588001002", "Gabriel Herrera", "Residencial Las Flores #28"),
            ("50588001003", "Sofía Castillo", "Km 6.5 Carretera Principal"),
            ("50588001004", "Fernando Cruz", "Contiguo a Plaza Central"),
        ]
        _build_demo_orders_for_tenant(
            db=db,
            comercio=comercio,
            sucursales=sucursales,
            productos=productos_lista,
            clientes_spec=clientes_default,
            giro_code=giro_code,
            prefix_folio=prefix,
            ensure_latest_has_sin=ensure_latest_has_sin,
        )
        comercio.pedidos_mes_actual = max(comercio.pedidos_mes_actual or 0, 10)

    db.commit()
    pedidos_despues = db.query(Pedido).filter(Pedido.tenant_id == comercio.id).count()
    return {
        "productos_creados": productos_creados,
        "insumos_creados": insumos_creados,
        "pedidos_creados": max(0, pedidos_despues - pedidos_antes),
    }


def seed_all_demo_giros(db: Session) -> None:
    """
    Siembra de forma idempotente (por slug) los negocios demo de cada giro
    (CAFETERIA, FARMACIA, TIENDA, SUPERMERCADO) y enriquece estadisticas de Napoli.
    """
    plan_pro = (
        db.query(PlanSaaS).filter(PlanSaaS.codigo == "PLAN_PRO").first()
        or db.query(PlanSaaS).first()
    )
    if not plan_pro:
        return

    # 1. Sembrar los 4 giros adicionales si no existen por slug
    for idx, spec in enumerate(DEMO_BUSINESSES, start=1):
        if db.query(TenantComercio).filter(TenantComercio.slug == spec["slug"]).first():
            continue

        giro_code = spec["giro"]
        tpl = GIRO_CATALOG_TEMPLATES[giro_code]

        comercio = TenantComercio(
            nombre_comercial=spec["nombre_comercial"],
            slug=spec["slug"],
            giro=giro_code,
            telefono_propietario=spec["telefono_propietario"],
            email_contacto=spec["email_dueno"],
            plan_id=plan_pro.id,
            estado_suscripcion="ACTIVO",
            fecha_vencimiento_suscripcion=datetime.now(timezone.utc) + timedelta(days=30),
            pedidos_mes_actual=10,
            moneda="NIO",
            simbolo_moneda="C$",
            acepta_efectivo=True,
            acepta_transferencia=True,
            datos_bancarios_texto=spec["banco_texto"],
            modo_pago_api_activo=False,
            tiene_mesas_fisicas=bool(tpl["tiene_mesas"]),
            reservaciones_habilitadas=False,
            monto_anticipo_reserva_default=150.0,
        )
        db.add(comercio)
        db.flush()

        db.add(
            SuscripcionFacturaSaaS(
                tenant_id=comercio.id,
                periodo_mes=datetime.now(timezone.utc).strftime("%Y-%m"),
                pedidos_contabilizados=10,
                monto_mensualidad_fija=float(plan_pro.precio_mensual_usd or 59.0),
                monto_comision_pedidos=0.0,
                total_a_pagar=float(plan_pro.precio_mensual_usd or 59.0),
                estado_pago="PAGADO",
                referencia_pago=f"SUSC-{spec['slug'].upper()}-001",
            )
        )

        suc_objs: List[SucursalComercio] = []
        for s_idx, (s_nom, s_wa, s_dir, s_envio, z1, z2) in enumerate(spec["sucursales"]):
            suc = SucursalComercio(
                tenant_id=comercio.id,
                nombre_sucursal=s_nom,
                codigo_whatsapp=s_wa,
                es_principal=(s_idx == 0),
                direccion_fisica=s_dir,
                horario_apertura="07:30",
                horario_cierre="21:00",
                abierto_ahora=True,
                tiempo_estimado_entrega_min=30,
                costo_envio_base=s_envio,
                pedido_minimo_domicilio=100.0,
            )
            db.add(suc)
            db.flush()
            suc_objs.append(suc)
            db.add_all(
                [
                    ZonaEnvioSucursal(sucursal_id=suc.id, nombre_zona=z1, costo_envio=s_envio),
                    ZonaEnvioSucursal(sucursal_id=suc.id, nombre_zona=z2, costo_envio=s_envio + 20.0),
                ]
            )

        if spec["mesas"] and suc_objs:
            for m_nom in spec["mesas"]:
                db.add(
                    MesaZona(
                        tenant_id=comercio.id,
                        sucursal_id=suc_objs[0].id,
                        nombre_mesa=m_nom,
                        capacidad_personas=4,
                        monto_anticipo_reserva=150.0,
                    )
                )

        # Solo 2 roles dentro del comercio: COMERCIO_ADMIN y SUCURSAL_STAFF
        if not db.query(Usuario).filter(Usuario.email == spec["email_dueno"]).first():
            db.add(
                Usuario(
                    tenant_id=comercio.id,
                    sucursal_id=None,
                    nombre_completo=spec["nombre_dueno"],
                    email=spec["email_dueno"],
                    password_hash=hash_password(spec["pass_dueno"]),
                    rol="COMERCIO_ADMIN",
                    activo=True,
                )
            )
        if not db.query(Usuario).filter(Usuario.email == spec["email_staff"]).first():
            db.add(
                Usuario(
                    tenant_id=comercio.id,
                    sucursal_id=suc_objs[0].id,
                    nombre_completo=spec["nombre_staff"],
                    email=spec["email_staff"],
                    password_hash=hash_password(spec["pass_staff"]),
                    rol="SUCURSAL_STAFF",
                    activo=True,
                )
            )

        db.flush()
        populate_tenant_demo_data(db, comercio, giro_code, clientes_spec=spec.get("clientes"))
        db.commit()

    # 2. Enriquecer Napoli con catalogo amplio e historial para Estadisticas sin romper pruebas existentes
    napoli = db.query(TenantComercio).filter(TenantComercio.slug == "napoli").first()
    if napoli and (
        db.query(ProductoPlato).filter(ProductoPlato.tenant_id == napoli.id).count() < 6
        or db.query(Pedido).filter(Pedido.tenant_id == napoli.id).count() < 5
    ):
        napoli_clientes = [
            ("50588990001", "Mariana Gómez", "Colonia Centroamérica #42"),
            ("50588990002", "Javier Téllez", "Villa Fontana Norte #15"),
            ("50588990003", "Claudia Pastrán", "Los Robles, Plaza El Sol 3c sur"),
            ("50588990004", "Carlos Ruiz", "Calle 10 #45, Col. Centro"),
        ]
        populate_tenant_demo_data(
            db=db,
            comercio=napoli,
            giro_raw="RESTAURANTE",
            clientes_spec=napoli_clientes,
            ensure_latest_has_sin=True,
        )
        db.commit()
