# =============================================================================
# servico-estoque — servidor gRPC (50051) + health check HTTP (8002)
#
# Contexto de build = RAIZ do repositório (ver docker-compose.yml), porque a
# geração dos stubs precisa de shared-protos/estoque.proto.
#
# Estágios:
#   runtime  → imagem base com as dependências de runtime
#   builder  → gera os stubs do contrato (grpcio-tools NÃO vai para a imagem final)
#   final    → imagem publicada: runtime + stubs + código
#   test     → final + requirements-dev.txt (não é publicada; usada para rodar a suíte)
# =============================================================================

# --- Dependências de runtime ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copia e instala dependências Python antes do código (camada cacheável)
COPY servico-estoque/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- Geração dos stubs gRPC (etapa de build) ---
FROM runtime AS builder

# grpcio-tools só é necessário para gerar os stubs no build
RUN pip install --no-cache-dir grpcio-tools==1.66.1

COPY shared-protos/ /app/shared-protos/
RUN python -m grpc_tools.protoc \
    -I/app/shared-protos \
    --python_out=/app \
    --grpc_python_out=/app \
    /app/shared-protos/estoque.proto

# --- Estágio de testes (não é a imagem publicada) ---
# Declarado ANTES de `final` de propósito: o alvo padrão do `docker build` é o
# ÚLTIMO estágio do arquivo, e o padrão precisa ser a imagem de runtime.
#   docker build --target test -f servico-estoque/Dockerfile -t cnm-servico-estoque:test .
FROM runtime AS test

COPY --from=builder /app/estoque_pb2.py /app/estoque_pb2_grpc.py /app/
COPY servico-estoque/ /app/
COPY servico-estoque/requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt

# Guarda: falha o build se os stubs não importarem (ex.: falta de dependência de runtime)
RUN python -c "import estoque_pb2, estoque_pb2_grpc"

# --- Imagem final (publicada) ---
FROM runtime AS final

# Stubs gerados no estágio builder (nunca versionados — ver .gitignore).
# Ficam no diretório do serviço para que `import estoque_pb2` resolva direto.
COPY --from=builder /app/estoque_pb2.py /app/estoque_pb2_grpc.py /app/

# Código do serviço
COPY servico-estoque/ /app/

# Guarda de build: falha aqui (em vez de em runtime no container) se as
# dependências de runtime dos stubs estiverem faltando — foi assim que se
# detectou a ausência do `protobuf` ao separar runtime de dev/test.
RUN python -c "import estoque_pb2, estoque_pb2_grpc"

# Porta gRPC principal e HTTP health check
EXPOSE 50051
EXPOSE 8002

# Em desenvolvimento o container aplica as migrations antes de subir.
# Em produção (múltiplas réplicas), defina RUN_MIGRATIONS=false e rode
# `alembic upgrade head` como etapa separada (job/init container), evitando
# corrida de migrations entre réplicas.
ENV RUN_MIGRATIONS=true
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-true}\" != \"false\" ]; then alembic upgrade head; fi; exec python main.py"]
