"""Configuração centralizada do servico-estoque (12-factor: tudo por variável de ambiente)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variáveis de ambiente do serviço.

    Os defaults apontam para o ambiente local do `docker-compose.yml` para que o
    serviço suba sem configuração extra em desenvolvimento; em qualquer outro
    ambiente os valores chegam por variável de ambiente (ADR-003: cada serviço
    recebe somente as credenciais do seu próprio database).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Banco de dados: estoque_db (ADR-003) ---
    database_url: str = (
        "postgresql+asyncpg://estoque_user:estoque_pass@localhost:5432/estoque_db"
    )

    # --- Redis: distributed lock da reserva (ADR-004) ---
    redis_url: str = "redis://localhost:6379/0"
    lock_ttl_seconds: int = 5

    # --- Servidor gRPC ---
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    grpc_max_workers: int = 10

    # --- Health check HTTP ---
    health_host: str = "0.0.0.0"
    health_port: int = 8002

    # --- Mensageria assíncrona (ADR-005) ---
    aws_endpoint_url: str | None = None  # None = AWS real; URL do LocalStack em dev
    aws_default_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    sqs_queue_estoque: str = "caixanamao-estoque-atualizado"

    # --- Observabilidade ---
    log_level: str = "INFO"


settings = Settings()
