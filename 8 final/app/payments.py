import uuid,httpx
from .config import settings
API="https://api.yookassa.ru/v3"
def receipt(order):
    if not settings.YOOKASSA_RECEIPT_ENABLED:return None
    if settings.YOOKASSA_TAX_SYSTEM_CODE is None or settings.YOOKASSA_VAT_CODE is None: raise RuntimeError("YooKassa tax/VAT settings are missing")
    items=[]
    for x in order["items"]:
        items.append({"description":str(x["name"])[:128],"quantity":f'{int(x["quantity"]):.2f}',"amount":{"value":f'{float(x["price"]):.2f}',"currency":"RUB"},"vat_code":settings.YOOKASSA_VAT_CODE,"payment_mode":settings.YOOKASSA_PAYMENT_MODE,"payment_subject":settings.YOOKASSA_PAYMENT_SUBJECT})
    return {"customer":{"email":order["customer_email"]},"items":items,"tax_system_code":settings.YOOKASSA_TAX_SYSTEM_CODE}
async def create_payment(order):
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY: raise RuntimeError("YooKassa credentials are not configured")
    payload={"amount":{"value":f'{float(order["total"]):.2f}',"currency":"RUB"},"capture":True,"description":f'Заказ #{order["order_id"]}',"metadata":{"order_id":order["order_id"]},"confirmation":{"type":"redirect","return_url":settings.YOOKASSA_RETURN_URL or settings.MINI_APP_URL}}
    if settings.YOOKASSA_RECEIPT_ENABLED: payload["receipt"]=receipt(order)
    async with httpx.AsyncClient(timeout=20) as http:
        r=await http.post(f"{API}/payments",auth=(settings.YOOKASSA_SHOP_ID,settings.YOOKASSA_SECRET_KEY),headers={"Idempotence-Key":str(uuid.uuid4()),"Content-Type":"application/json"},json=payload); r.raise_for_status(); return r.json()
async def get_payment(payment_id):
    async with httpx.AsyncClient(timeout=20) as http:
        r=await http.get(f"{API}/payments/{payment_id}",auth=(settings.YOOKASSA_SHOP_ID,settings.YOOKASSA_SECRET_KEY)); r.raise_for_status(); return r.json()
