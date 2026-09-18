from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Banco de dados
    database_url: str

    # JWT
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # gRPC — servico-estoque
    estoque_grpc_host: str = "localhost"
    estoque_grpc_port: int = 50051

    # AWS SQS
    aws_endpoint_url: str | None = None  # None em produção real; URL do LocalStack em dev
    aws_default_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    sqs_queue_pedidos: str = "caixanamao-pedidos-criados"


settings = Settings()
