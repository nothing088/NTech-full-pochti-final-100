from aiogram import Bot,Dispatcher,Router
from aiogram.filters import CommandStart
from aiogram.types import Message,InlineKeyboardButton,InlineKeyboardMarkup,WebAppInfo,FSInputFile
from .config import settings
bot=Bot(settings.BOT_TOKEN); dp=Dispatcher(); router=Router()
LOGO="assets/nothing-tech.png"
async def logo_message(chat_id:int,text:str,reply_markup=None):
    try:
        await bot.send_photo(chat_id,FSInputFile(LOGO),caption=text[:1024],reply_markup=reply_markup)
        if len(text)>1024: await bot.send_message(chat_id,text[1024:])
    except Exception:
        await bot.send_message(chat_id,"◼ NOTHING TECH\n\n"+text,reply_markup=reply_markup)
@router.message(CommandStart())
async def start(message:Message):
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть NOTHING TECH",web_app=WebAppInfo(url=settings.MINI_APP_URL))]])
    await logo_message(message.chat.id,"Добро пожаловать в NOTHING TECH. Здесь вы надете премиальную технику, с чесностью и открытосью к каждому клиенту.",kb)
@router.message()
async def fallback(message:Message): await logo_message(message.chat.id,"Используйте /start, чтобы открыть NOTHING TECH.")
dp.include_router(router)
async def configure_webhook():
    await bot.set_webhook(url=settings.PUBLIC_BASE_URL.rstrip("/")+"/telegram/webhook",allowed_updates=dp.resolve_used_update_types(),drop_pending_updates=False)
