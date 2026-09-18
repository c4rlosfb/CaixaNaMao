FROM python:3.12-slim

WORKDIR /app

# Instala dependências do sistema necessárias para psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala dependências Python antes do código (camada cacheável)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código do serviço
COPY . .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
