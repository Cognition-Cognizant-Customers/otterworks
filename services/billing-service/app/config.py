from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "billing-service"
    schema_name: str = "billing_svc"
    database_url: str
    mongo_uri: str = "mongodb://billing-service-mongo:27017"
    mongo_db: str = "ow_tp_demo"
    cors_origins: list[str] = ["http://localhost:3000"]
    allow_internal_reset: bool = False

    model_config = {"env_prefix": "BILLING_SVC_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
