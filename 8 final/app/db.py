from pymongo import AsyncMongoClient, ASCENDING, DESCENDING
from .config import settings
client=AsyncMongoClient(settings.MONGODB_URI,serverSelectionTimeoutMS=10000,connectTimeoutMS=10000)
db=client[settings.MONGODB_DB]
users=db.users; categories=db.categories; products=db.products; orders=db.orders; promos=db.promos; delivery=db.delivery; support=db.support; faq=db.faq
fs_bucket=None
async def init_db():
    global fs_bucket
    await client.admin.command("ping")
    from gridfs import AsyncGridFSBucket
    fs_bucket=AsyncGridFSBucket(db)
    await users.create_index("tg_id",unique=True)
    await products.create_index([("active",ASCENDING),("created_at",DESCENDING)])
    await orders.create_index([("tg_id",ASCENDING),("created_at",DESCENDING)])
    await orders.create_index("order_id",unique=True)
    await promos.create_index("code",unique=True)
    await delivery.create_index("name",unique=True)
    await faq.create_index("key",unique=True)
    if await faq.count_documents({})==0:
        await faq.insert_one({"key":"support","items":[
            {"question":"Как оформить заказ?","answer":"Выберите товар, настройте характеристики, добавьте его в корзину и перейдите к оформлению."},
            {"question":"Есть ли гарантия?","answer":"Условия гарантии указаны в карточке конкретного товара."},
            {"question":"Как связаться с поддержкой?","answer":"Откройте вкладку «Поддержка» и отправьте сообщение."}]})
async def close_db(): client.close()
