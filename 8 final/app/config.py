from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: str = ""
    MONGODB_URI: str
    MONGODB_DB: str = "telegram_shop"
    PUBLIC_BASE_URL: str
    MINI_APP_URL: str
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = ""
    YOOKASSA_RECEIPT_ENABLED: bool = False
    YOOKASSA_TAX_SYSTEM_CODE: int | None = None
    YOOKASSA_VAT_CODE: int | None = None
    YOOKASSA_PAYMENT_MODE: str = "full_payment"
    YOOKASSA_PAYMENT_SUBJECT: str = "commodity"
    ADMIN_SECRET: str
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip().isdigit()}
settings = Settings()
