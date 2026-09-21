"""Configuração do servico-pedidos (12-factor: tudo por variável de ambiente).

Os defaults abaixo existem para que a suíte de testes e o serviço subam em
desenvolvimento **sem** nenhum `.env` (o `.env.example` na raiz documenta todas
as variáveis). Em produção, nada de sensível fica no default.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variáveis de ambiente do serviço."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Banco de dados: pedidos_db (ADR-003) ---
    database_url: str = "postgresql+asyncpg://pedidos_user:pedidos_pass@localhost:5432/pedidos_db"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- JWT: validação local HS256 (ADR-001) ---
    jwt_secret: str = "dev-secret-troque-em-producao"
    jwt_algorithm: str = "HS256"

    # --- gRPC — servico-estoque ---
    estoque_grpc_host: str = "localhost"
    estoque_grpc_port: int = 50051

    # --- Mensageria assíncrona (ADR-005) ---
    aws_endpoint_url: str | None = None  # None em produção real; URL do LocalStack em dev
    aws_default_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    sqs_queue_pedidos: str = "caixanamao-pedidos-criados"

    # --- Observabilidade ---
    log_level: str = "INFO"


settings = Settings()
