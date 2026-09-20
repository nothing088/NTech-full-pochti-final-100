import hashlib,hmac,json,time
from urllib.parse import parse_qsl
from fastapi import HTTPException,Request
from .config import settings
def validate_telegram_init_data(init_data:str)->dict:
    if not init_data: raise HTTPException(401,"Telegram initData is required")
    data=dict(parse_qsl(init_data,keep_blank_values=True)); received=data.pop("hash",None)
    if not received: raise HTTPException(401,"Invalid Telegram initData")
    try: auth_date=int(data.get("auth_date","0"))
    except ValueError: raise HTTPException(401,"Invalid auth_date")
    if abs(time.time()-auth_date)>86400: raise HTTPException(401,"Telegram initData expired")
    check="\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret=hmac.new(b"WebAppData",settings.BOT_TOKEN.encode(),hashlib.sha256).digest()
    calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc,received): raise HTTPException(401,"Invalid Telegram signature")
    return data
async def current_user(request:Request):
    data=validate_telegram_init_data(request.headers.get("X-Telegram-Init-Data",""))
    try:return json.loads(data["user"])
    except Exception:raise HTTPException(401,"Telegram user data missing")
async def admin_user(request:Request):
    secret=request.headers.get("X-Admin-Secret","")
    if secret and hmac.compare_digest(secret,settings.ADMIN_SECRET): return {"admin":True}
    user=await current_user(request)
    if int(user["id"]) not in settings.admin_ids: raise HTTPException(403,"Admin access required")
    return user
