import os
from dotenv import load_dotenv
load_dotenv()

from clients.mercadolibre import MercadoLibreClient

ml = MercadoLibreClient(
    os.environ["ML_CLIENT_ID"],
    os.environ["ML_CLIENT_SECRET"],
    os.environ["ML_ACCESS_TOKEN"],
    os.environ["ML_REFRESH_TOKEN"],
)

user_id = ml.get_user_id()
print(f"Usuario ML ID: {user_id}")

ids = ml.get_active_item_ids()
print(f"Publicaciones activas: {len(ids)}\n")

for item_id in ids[:5]:
    item = ml.get_item_details(item_id)
    titulo = item["title"][:55]
    precio = item["price"]
    stock  = item["available_quantity"]
    print(f"  [{item_id}]  {titulo}")
    print(f"              Precio: ${precio:,.0f}  |  Stock: {stock}")

if len(ids) > 5:
    print(f"\n  ... y {len(ids) - 5} publicaciones más.")
