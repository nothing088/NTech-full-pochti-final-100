import io, uuid, json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from bson import ObjectId
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import (
    init_db, close_db, client, users, categories,
    products, orders, promos, delivery, support, faq, fs_bucket
)
from .auth import current_user, admin_user
from .bot import bot, dp, configure_webhook
from .payments import create_payment, get_payment
from .notifications import notify_status, notify_admins

app = FastAPI(title="NOTHING TECH — Telegram Shop")
app.mount("/static", StaticFiles(directory="frontend"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

def now():
    return datetime.now(timezone.utc)

def money(v):
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def oid(v):
    try:
        return ObjectId(v)
    except Exception:
        raise HTTPException(400, "Invalid id")

def config(p):
    variants = []
    for v in p.get("variants", []) or []:
        name = str(v.get("name", "")).strip()
        opts = []
        for o in v.get("options", []) or []:
            val = str(o.get("value", "")).strip()
            if val:
                opts.append({
                    "value": val,
                    "price": money(o.get("price", 0)),
                    "stock": int(o.get("stock", p.get("stock", 0)))
                })
        if name and opts:
            variants.append({"name": name, "options": opts})
    tabs = []
    for t in p.get("tabs", []) or []:
        title = str(t.get("title", "")).strip()
        content = str(t.get("content", "")).strip()
        if title and content:
            tabs.append({"title": title[:100], "content": content[:5000]})
    return variants, tabs

def resolve(p, selections):
    variants, _ = config(p)
    price = money(p.get("price", 0))
    stock = int(p.get("stock", 0))
    chosen = []
    for v in variants:
        val = str((selections or {}).get(v["name"], "")).strip()
        opt = next((o for o in v["options"] if o["value"] == val), None)
        if not opt:
            raise HTTPException(400, f'Выберите: {v["name"]}')
        price = money(price + opt["price"])
        stock = min(stock, opt["stock"])
        chosen.append({"name": v["name"], "value": val})
    return price, stock, chosen

@app.on_event("startup")
async def startup():
    await init_db()
    await configure_webhook()
    if await products.count_documents({}) == 0:
        await products.insert_one({
            "name": "Пример товара",
            "description": "Добавьте товары через админку.",
            "price": 990,
            "stock": 100,
            "active": True,
            "variants": [],
            "tabs": [],
            "image_file_ids": [],
            "created_at": now()
        })
    if await delivery.count_documents({}) == 0:
        await delivery.insert_many([
            {"name": "Самовывоз", "price": 0, "active": True},
            {"name": "Курьер", "price": 300, "active": True}
        ])

@app.on_event("shutdown")
async def shutdown():
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.session.close()
    await close_db()

@app.get("/")
async def index():
    return FileResponse("frontend/index.html")

@app.get("/admin")
async def admin_page():
    return FileResponse("frontend/admin.html")

@app.get("/admin/")
async def admin_page_slash():
    return FileResponse("frontend/admin.html")

@app.get("/health")
async def health():
    await client.admin.command("ping")
    return {"ok": True}

@app.post("/telegram/webhook")
async def webhook(request: Request):
    from aiogram.types import Update
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/api/me")
async def me(user=Depends(current_user)):
    await users.update_one(
        {"tg_id": int(user["id"])},
        {
            "$set": {
                "tg_id": int(user["id"]),
                "username": user.get("username", ""),
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
                "updated_at": now()
            }
        },
        upsert=True
    )
    return user

@app.get("/api/profile")
async def profile(user=Depends(current_user)):
    tg_id = int(user["id"])
    total = await orders.count_documents({"tg_id": tg_id})
    active = await orders.count_documents({
        "tg_id": tg_id,
        "status": {"$in": ["new", "waiting_payment", "paid", "processing", "shipped"]}
    })
    spent = 0.0
    async for o in orders.find(
        {"tg_id": tg_id, "status": {"$in": ["paid", "processing", "shipped", "delivered"]}},
        {"total": 1}
    ):
        spent += float(o.get("total", 0))
    last = await orders.find_one({"tg_id": tg_id}, sort=[("created_at", -1)])
    u = await users.find_one({"tg_id": tg_id})
    favorites = (u or {}).get("favorites", [])
    
    return {
        "user": user,
        "stats": {
            "orders": total,
            "active_orders": active,
            "spent": money(spent),
            "favorites_count": len(favorites)
        },
        "last_order": ({
            "order_id": last["order_id"],
            "status": last["status"],
            "total": last["total"],
            "created_at": last["created_at"].isoformat()
        } if last else None)
    }

@app.get("/api/favorites")
async def get_favorites(user=Depends(current_user)):
    tg_id = int(user["id"])
    u = await users.find_one({"tg_id": tg_id})
    return {"favorites": (u or {}).get("favorites", [])}

@app.post("/api/favorites/{product_id}")
async def toggle_favorite(product_id: str, user=Depends(current_user)):
    tg_id = int(user["id"])
    u = await users.find_one({"tg_id": tg_id})
    current_favs = (u or {}).get("favorites", []) if u else []
    
    if product_id in current_favs:
        await users.update_one({"tg_id": tg_id}, {"$pull": {"favorites": product_id}})
        is_active = False
    else:
        await users.update_one(
            {"tg_id": tg_id},
            {
                "$addToSet": {"favorites": product_id},
                "$setOnInsert": {
                    "username": user.get("username", ""),
                    "first_name": user.get("first_name", ""),
                    "last_name": user.get("last_name", ""),
                    "created_at": now()
                },
                "$set": {"updated_at": now()}
            },
            upsert=True
        )
        is_active = True
        
    return {"ok": True, "active": is_active}

@app.get("/api/cart")
async def get_cart(user=Depends(current_user)):
    tg_id = int(user["id"])
    u = await users.find_one({"tg_id": tg_id})
    return {"cart": (u or {}).get("cart", [])}

@app.post("/api/cart")
async def save_cart(payload: dict, user=Depends(current_user)):
    tg_id = int(user["id"])
    cart_items = payload.get("cart", [])
    await users.update_one(
        {"tg_id": tg_id},
        {
            "$set": {
                "cart": cart_items,
                "updated_at": now()
            },
            "$setOnInsert": {
                "username": user.get("username", ""),
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
                "created_at": now()
            }
        },
        upsert=True
    )
    return {"ok": True}

@app.get("/api/catalog")
async def catalog():
    ps = []
    async for p in products.find({"active": True}).sort("created_at", -1):
        image_ids = p.get("image_file_ids") or ([] if not p.get("image_file_id") else [p.get("image_file_id")])
        image_urls = [f'/api/images/{fid}' for fid in image_ids]
        ps.append({
            "id": str(p["_id"]),
            "name": p["name"],
            "description": p.get("description", ""),
            "price": p["price"],
            "stock": p.get("stock", 0),
            "category_id": p.get("category_id", ""),
            "image_url": image_urls[0] if image_urls else "",
            "image_urls": image_urls,
            "has_variants": bool(p.get("variants"))
        })
    cs = [{"id": str(c["_id"]), "name": c["name"]} async for c in categories.find({}).sort("name", 1)]
    ds = [{"id": str(d["_id"]), "name": d["name"], "price": d["price"]} async for d in delivery.find({"active": True}).sort("price", 1)]
    return {"products": ps, "categories": cs, "delivery": ds}

@app.get("/api/products/{product_id}")
async def product_detail(product_id: str):
    p = await products.find_one({"_id": oid(product_id), "active": True})
    if not p:
        raise HTTPException(404, "Product not found")
    variants, tabs = config(p)
    image_ids = p.get("image_file_ids") or ([] if not p.get("image_file_id") else [p.get("image_file_id")])
    image_urls = [f'/api/images/{fid}' for fid in image_ids]
    return {
        "id": str(p["_id"]),
        "name": p["name"],
        "description": p.get("description", ""),
        "price": p["price"],
        "stock": p.get("stock", 0),
        "category_id": p.get("category_id", ""),
        "image_url": image_urls[0] if image_urls else "",
        "image_urls": image_urls,
        "variants": variants,
        "tabs": tabs
    }

@app.get("/api/faq")
async def public_faq():
    d = await faq.find_one({"key": "support"})
    return {"items": (d or {}).get("items", [])}

@app.get("/api/images/{file_id}")
async def image(file_id: str):
    if fs_bucket is None:
        raise HTTPException(503, "Storage not ready")
    try:
        s = await fs_bucket.open_download_stream(oid(file_id))
    except Exception:
        raise HTTPException(404, "Image not found")
    return Response(
        content=await s.read(),
        media_type=(getattr(s, "metadata", {}) or {}).get("content_type", "image/jpeg")
    )

@app.get("/api/orders")
async def my_orders(user=Depends(current_user)):
    return [
        {
            "order_id": o["order_id"],
            "status": o["status"],
            "total": o["total"],
            "subtotal": o.get("subtotal", o["total"]),
            "delivery_price": o.get("delivery_price", 0),
            "discount": o.get("discount", 0),
            "comment": o.get("comment", ""),
            "created_at": o["created_at"].isoformat(),
            "items": o.get("items", []),
            "delivery_name": o.get("delivery_name")
        }
        async for o in orders.find({"tg_id": int(user["id"])}).sort("created_at", -1)
    ]

@app.get("/api/orders/{order_id}")
async def my_order(order_id: str, user=Depends(current_user)):
    o = await orders.find_one({"order_id": order_id, "tg_id": int(user["id"])})
    if not o:
        raise HTTPException(404, "Order not found")
    return {
        "order_id": o["order_id"],
        "status": o["status"],
        "total": o["total"],
        "subtotal": o["subtotal"],
        "delivery_price": o["delivery_price"],
        "discount": o["discount"],
        "items": o["items"],
        "delivery_name": o.get("delivery_name"),
        "comment": o.get("comment", ""),
        "created_at": o["created_at"].isoformat()
    }

@app.post("/api/orders")
async def create_order(payload: dict, user=Depends(current_user)):
    tg_id = int(user["id"])
    cart_items = payload.get("items") or []
    if not cart_items:
        raise HTTPException(400, "Корзина пуста")
    ids = [oid(x["product_id"]) for x in cart_items]
    found = {str(p["_id"]): p async for p in products.find({"_id": {"$in": ids}, "active": True})}
    items = []
    subtotal = 0
    for line in cart_items:
        pid = str(line["product_id"])
        p = found.get(pid)
        if not p:
            raise HTTPException(400, "Товар больше недоступен")
        qty = int(line.get("quantity", 1))
        price, stock, chosen = resolve(p, line.get("selections") or {})
        if qty < 1 or qty > stock:
            raise HTTPException(400, f'Недостаточный остаток: {p["name"]}')
        total = money(price * qty)
        subtotal += total
        items.append({
            "product_id": pid,
            "name": p["name"],
            "price": price,
            "quantity": qty,
            "total": total,
            "variants": chosen
        })
    d = None
    if payload.get("delivery_id"):
        d = await delivery.find_one({"_id": oid(payload["delivery_id"]), "active": True})
    dp_price = money(d["price"] if d else 0)
    promo_code = (payload.get("promo_code") or "").strip().upper()
    discount = 0
    if promo_code:
        already_used = await orders.find_one({
            "tg_id": tg_id,
            "promo_code": promo_code,
            "status": {"$in": ["paid", "processing", "shipped", "delivered", "waiting_payment"]}
        })
        if already_used:
            raise HTTPException(400, "Вы уже использовали этот промокод")

        p_promo = await promos.find_one({"code": promo_code, "active": True})
        if (
            p_promo
            and (not p_promo.get("expires_at") or p_promo["expires_at"] > now())
            and (not p_promo.get("max_uses") or p_promo.get("uses", 0) < p_promo["max_uses"])
            and subtotal >= float(p_promo.get("min_order", 0))
        ):
            discount = money(subtotal * float(p_promo.get("percent", 0)) / 100)
        else:
            raise HTTPException(400, "Промокод недействителен или истек")
            
    total = max(0, money(subtotal + dp_price - discount))
    email = (payload.get("email") or "").strip().lower()
    if settings.YOOKASSA_RECEIPT_ENABLED and not email:
        raise HTTPException(400, "Укажите email для чека")
    order_id = uuid.uuid4().hex[:12].upper()
    order = {
        "order_id": order_id,
        "tg_id": tg_id,
        "username": user.get("username", ""),
        "status": "waiting_payment",
        "payment_status": "pending",
        "items": items,
        "subtotal": subtotal,
        "delivery_price": dp_price,
        "discount": discount,
        "total": total,
        "promo_code": promo_code or None,
        "delivery_name": d["name"] if d else None,
        "customer_email": email,
        "comment": payload.get("comment", ""),
        "created_at": now(),
        "updated_at": now()
    }
    await orders.insert_one(order)
    try:
        payment = await create_payment(order)
    except Exception as e:
        await orders.delete_one({"order_id": order_id})
        raise HTTPException(502, f"Ошибка ЮKassa: {e}")
    await orders.update_one(
        {"order_id": order_id},
        {"$set": {"payment_id": payment["id"], "payment_status": payment.get("status", "pending")}}
    )
    if promo_code:
        await promos.update_one({"code": promo_code}, {"$inc": {"uses": 1}})
    await users.update_one({"tg_id": tg_id}, {"$set": {"cart": []}})
    await notify_admins(f"Новый заказ #{order_id}\nСумма: {total:.2f} ₽")
    return {"order_id": order_id, "confirmation_url": payment["confirmation"]["confirmation_url"]}

@app.post("/api/payments/yookassa")
async def yookassa(request: Request):
    event = await request.json()
    obj = event.get("object") or {}
    pid = obj.get("id")
    if event.get("event") != "payment.succeeded" or not pid:
        return {"ok": True}
    o = await orders.find_one({"payment_id": pid})
    if not o:
        return {"ok": True}
    r = await orders.update_one(
        {"_id": o["_id"], "status": {"$in": ["waiting_payment", "new"]}},
        {"$set": {"status": "paid", "payment_status": "succeeded", "paid_at": now(), "updated_at": now()}}
    )
    if not r.modified_count:
        return {"ok": True}
    for x in o["items"]:
        await products.update_one(
            {"_id": oid(x["product_id"]), "stock": {"$gte": int(x["quantity"])}},
            {"$inc": {"stock": -int(x["quantity"])}}
        )
    await notify_status(o["tg_id"], o["order_id"], "paid")
    return {"ok": True}

@app.post("/api/support")
async def user_support(payload: dict, user=Depends(current_user)):
    msg = (payload.get("message") or "").strip()
    if not msg:
        raise HTTPException(400, "Сообщение пустое")
    await support.insert_one({
        "tg_id": int(user["id"]),
        "username": user.get("username", ""),
        "message": msg[:4000],
        "status": "open",
        "created_at": now()
    })
    await notify_admins(f"Новое обращение\n@{user.get('username','')}\n{msg[:1000]}")
    return {"ok": True}

@app.get("/api/admin/stats")
async def admin_stats(_: dict = Depends(admin_user)):
    revenue = 0.0
    async for o in orders.find(
        {"status": {"$in": ["paid", "processing", "shipped", "delivered"]}},
        {"total": 1}
    ):
        revenue += float(o.get("total", 0))
    return {
        "products": await products.count_documents({"active": True}),
        "orders": await orders.count_documents({}),
        "revenue": money(revenue),
        "support": await support.count_documents({"status": "open"})
    }

@app.get("/api/admin/products")
async def admin_products(_: dict = Depends(admin_user)):
    res = []
    async for p in products.find({}).sort("created_at", -1):
        image_ids = p.get("image_file_ids") or ([] if not p.get("image_file_id") else [p.get("image_file_id")])
        res.append({
            "id": str(p["_id"]),
            "name": p["name"],
            "description": p.get("description", ""),
            "price": p["price"],
            "stock": p.get("stock", 0),
            "active": p.get("active", True),
            "category_id": p.get("category_id", ""),
            "image_file_ids": image_ids,
            "variants": p.get("variants", []),
            "tabs": p.get("tabs", [])
        })
    return res

@app.post("/api/admin/products")
async def add_product(payload: dict, _: dict = Depends(admin_user)):
    image_ids = payload.get("image_file_ids") or []
    if not image_ids and payload.get("image_file_id"):
        image_ids = [payload["image_file_id"]]
    r = await products.insert_one({
        "name": payload.get("name", "").strip(),
        "description": payload.get("description", ""),
        "price": money(payload.get("price", 0)),
        "stock": int(payload.get("stock", 0)),
        "active": bool(payload.get("active", True)),
        "category_id": payload.get("category_id", ""),
        "image_file_id": image_ids[0] if image_ids else None,
        "image_file_ids": image_ids,
        "variants": payload.get("variants") or [],
        "tabs": payload.get("tabs") or [],
        "created_at": now()
    })
    return {"id": str(r.inserted_id)}

@app.put("/api/admin/products/{product_id}")
async def edit_product(product_id: str, payload: dict, _: dict = Depends(admin_user)):
    u = {k: payload[k] for k in ["name", "description", "active", "category_id", "variants", "tabs"] if k in payload}
    if "price" in payload:
        u["price"] = money(payload["price"])
    if "stock" in payload:
        u["stock"] = int(payload["stock"])
    if "image_file_ids" in payload:
        ids = payload["image_file_ids"]
        u["image_file_ids"] = ids
        u["image_file_id"] = ids[0] if ids else None
    await products.update_one({"_id": oid(product_id)}, {"$set": u})
    return {"ok": True}

@app.delete("/api/admin/products/{product_id}")
async def delete_product(product_id: str, _: dict = Depends(admin_user)):
    r = await products.delete_one({"_id": oid(product_id)})
    return {"ok": bool(r.deleted_count)}

@app.post("/api/admin/upload")
async def upload(file: UploadFile = File(...), _: dict = Depends(admin_user)):
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(400, "Разрешены JPG, PNG, WEBP")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "Максимум 8 MB")
    fid = await fs_bucket.upload_from_stream(
        uuid.uuid4().hex,
        io.BytesIO(data),
        metadata={"content_type": file.content_type}
    )
    return {"file_id": str(fid)}

@app.get("/api/admin/categories")
async def admin_categories(_: dict = Depends(admin_user)):
    return [{"id": str(c["_id"]), "name": c["name"]} async for c in categories.find({}).sort("name", 1)]

@app.post("/api/admin/categories")
async def add_category(payload: dict, _: dict = Depends(admin_user)):
    r = await categories.insert_one({"name": payload["name"].strip(), "created_at": now()})
    return {"id": str(r.inserted_id)}

@app.delete("/api/admin/categories/{category_id}")
async def delete_category(category_id: str, _: dict = Depends(admin_user)):
    r = await categories.delete_one({"_id": oid(category_id)})
    return {"ok": bool(r.deleted_count)}

@app.get("/api/admin/delivery")
async def admin_delivery(_: dict = Depends(admin_user)):
    return [
        {
            "id": str(d["_id"]),
            "name": d["name"],
            "price": d["price"],
            "active": d.get("active", True)
        }
        async for d in delivery.find({}).sort("price", 1)
    ]

@app.post("/api/admin/delivery")
async def add_delivery(payload: dict, _: dict = Depends(admin_user)):
    r = await delivery.insert_one({
        "name": payload["name"].strip(),
        "price": money(payload.get("price", 0)),
        "active": True
    })
    return {"id": str(r.inserted_id)}

@app.delete("/api/admin/delivery/{delivery_id}")
async def delete_delivery(delivery_id: str, _: dict = Depends(admin_user)):
    r = await delivery.delete_one({"_id": oid(delivery_id)})
    return {"ok": bool(r.deleted_count)}

@app.put("/api/admin/delivery/{delivery_id}")
async def edit_delivery(delivery_id: str, payload: dict, _: dict = Depends(admin_user)):
    u = {}
    if "name" in payload:
        u["name"] = payload["name"].strip()
    if "price" in payload:
        u["price"] = money(payload["price"])
    if "active" in payload:
        u["active"] = bool(payload["active"])
    await delivery.update_one({"_id": oid(delivery_id)}, {"$set": u})
    return {"ok": True}

@app.get("/api/admin/orders")
async def admin_orders(_: dict = Depends(admin_user)):
    return [
        {
            "order_id": o["order_id"],
            "status": o["status"],
            "total": o["total"],
            "username": o.get("username", "")
        }
        async for o in orders.find({}).sort("created_at", -1).limit(500)
    ]

@app.put("/api/admin/orders/{order_id}/status")
async def order_status(order_id: str, payload: dict, _: dict = Depends(admin_user)):
    status = payload.get("status")
    allowed = {"new", "waiting_payment", "paid", "processing", "shipped", "delivered", "cancelled"}
    if status not in allowed:
        raise HTTPException(400, "Invalid status")
    o = await orders.find_one({"order_id": order_id})
    if not o:
        raise HTTPException(404, "Order not found")
    await orders.update_one({"_id": o["_id"]}, {"$set": {"status": status, "updated_at": now()}})
    await notify_status(o["tg_id"], order_id, status)
    return {"ok": True}

@app.get("/api/admin/faq")
async def admin_faq(_: dict = Depends(admin_user)):
    d = await faq.find_one({"key": "support"})
    return {"items": (d or {}).get("items", [])}

@app.put("/api/admin/faq")
async def update_faq(payload: dict, _: dict = Depends(admin_user)):
    items = [
        {
            "question": str(x.get("question", "")).strip()[:300],
            "answer": str(x.get("answer", "")).strip()[:5000]
        }
        for x in payload.get("items", [])
        if str(x.get("question", "")).strip() and str(x.get("answer", "")).strip()
    ]
    await faq.update_one({"key": "support"}, {"$set": {"items": items, "updated_at": now()}}, upsert=True)
    return {"ok": True}