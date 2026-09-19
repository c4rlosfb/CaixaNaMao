FROM python:3.12-slim

WORKDIR /app

# Contexto de build = raiz do repositório (ver docker-compose.yml), porque a
# geração dos stubs gRPC precisa de shared-protos/estoque.proto.
COPY servico-estoque/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Contrato gRPC compartilhado + código do serviço
COPY shared-protos/ /app/shared-protos/
COPY servico-estoque/ /app/

# Gera os stubs no build (nunca versionados — ver .gitignore).
# Gerados no diretório do serviço para que `import estoque_pb2` resolva direto.
RUN python -m grpc_tools.protoc \
    -I/app/shared-protos \
    --python_out=/app \
    --grpc_python_out=/app \
    /app/shared-protos/estoque.proto

# Porta gRPC principal e HTTP health check
EXPOSE 50051
EXPOSE 8002

# Em desenvolvimento o próprio container aplica as migrations antes de subir.
# Em produção, rodar as migrations como etapa separada (job/init container).
CMD ["sh", "-c", "alembic upgrade head && exec python main.py"]
