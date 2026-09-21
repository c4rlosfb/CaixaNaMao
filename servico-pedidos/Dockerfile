# =============================================================================
# servico-pedidos — REST (8000)
#
# Contexto de build = RAIZ do repositório (ver docker-compose.yml), porque a
# geração dos stubs precisa de shared-protos/estoque.proto.
#
# Estágios:
#   runtime  → imagem base com as dependências de runtime
#   builder  → gera os stubs do contrato (grpcio-tools NÃO vai para a imagem final)
#   test     → runtime + requirements-dev.txt (não é publicada; usada para a suíte)
#   final    → imagem publicada: runtime + stubs + código
# =============================================================================

# --- Dependências de runtime ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copia e instala dependências Python antes do código (camada cacheável)
COPY servico-pedidos/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- Geração dos stubs gRPC do contrato compartilhado ---
FROM runtime AS builder

RUN pip install --no-cache-dir grpcio-tools==1.66.1

COPY shared-protos/ /app/shared-protos/
# Gerados em app/clients/ porque o cliente importa `from app.clients import estoque_pb2_grpc`.
# O diretório de destino precisa existir no estágio builder (o código do serviço só é
# copiado nos estágios finais).
RUN mkdir -p /app/app/clients \
    && python -m grpc_tools.protoc \
        -I/app/shared-protos \
        --python_out=/app/app/clients \
        --grpc_python_out=/app/app/clients \
        /app/shared-protos/estoque.proto \
    # O *_pb2_grpc.py gerado pelo protoc usa import absoluto (`import estoque_pb2`),
    # que NÃO resolve quando o módulo é importado como `app.clients.estoque_pb2_grpc`.
    # Sem este ajuste o cliente gRPC fica indisponível em runtime (bug pego pela
    # verificação ponta a ponta e pela guarda de build abaixo).
    && sed -i 's|^import estoque_pb2 as estoque__pb2$|from app.clients import estoque_pb2 as estoque__pb2|' \
        /app/app/clients/estoque_pb2_grpc.py

# --- Estágio de testes (não é a imagem publicada) ---
# Declarado antes de `final`: o alvo padrão do `docker build` é o último estágio.
FROM runtime AS test

COPY --from=builder /app/app/clients/estoque_pb2.py /app/app/clients/
COPY --from=builder /app/app/clients/estoque_pb2_grpc.py /app/app/clients/
COPY servico-pedidos/ /app/
COPY servico-pedidos/requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt

# Guarda: falha o build se os stubs não importarem (ex.: falta de dependência de runtime)
RUN python -c "from app.clients import estoque_pb2, estoque_pb2_grpc"

# --- Imagem final (publicada) ---
FROM runtime AS final

# Stubs gerados no estágio builder (nunca versionados — ver .gitignore)
COPY --from=builder /app/app/clients/estoque_pb2.py /app/app/clients/estoque_pb2_grpc.py /app/app/clients/

# Código do serviço
COPY servico-pedidos/ /app/

# Guarda de build: dependência de runtime dos stubs (protobuf) e layout do pacote.
RUN python -c "from app.main import app; from app.clients import estoque_pb2_grpc"

EXPOSE 8000

# Em desenvolvimento o container aplica as migrations antes de subir.
# Em produção (múltiplas réplicas), defina RUN_MIGRATIONS=false e rode
# `alembic upgrade head` como etapa separada, evitando corrida entre réplicas.
ENV RUN_MIGRATIONS=true
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-true}\" != \"false\" ]; then alembic upgrade head; fi; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
