FROM python:3.12-slim

WORKDIR /app

# Instala dependências do sistema necessárias para psycopg2 e compilação de stubs gRPC
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências Python antes do código (camada cacheável)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do serviço
COPY . .

# Porta gRPC principal e HTTP health check
EXPOSE 50051
EXPOSE 8002

CMD ["python", "main.py"]
