# Guia de Deploy e Homologação na AWS (EC2)

> **Projeto:** CaixaNaMao  
> **Sprint:** 2  
> **Issue:** #8 — [Issue 7] Configurar ambiente AWS e realizar deploy inicial de container  
> **Autor:** LuanCasDias (Luan Dias)  
> **Revisão técnica:** c4rlosfb  
> **Status:** Em homologação  
> **Última atualização:** 2026-09-21  

---

## 1. Objetivo

Este documento cumpre a **Issue #8** da **Sprint 2**, definindo e registrando o procedimento operacional para o provisionamento do ambiente na AWS e o deploy inicial da stack de microsserviços do **CaixaNaMão** via Docker Compose.

O foco desta entrega é:
1. **Validar o pipeline de entrega e empacotamento em nuvem** dos containers (`servico-pedidos`, `servico-estoque`, `postgres`, `redis` e opcionalmente `localstack`);
2. **Homologar a comunicação externa** pela porta pública exposta do Serviço de Pedidos (REST);
3. **Garantir a postura de segurança mínima em nuvem**, isolando bancos de dados e canais internos de comunicação (gRPC) no loopback da instância.

---

## 2. Arquitetura e Postura de Segurança na Homologação

Para ambiente de nuvem, foi estabelecido um arquivo de composição especializado: `docker-compose.homolog.yml`.

### 2.1 Por que um override de homologação?
No arquivo de desenvolvimento local (`docker-compose.yml`), portas como PostgreSQL (`5432`), Redis (`6379`) e gRPC (`50051`) ficam expostas para facilitar testes rápidos pelo desenvolvedor. Em ambiente de nuvem, expor essas portas diretamente à internet configuraria uma grave falha de segurança.

O `docker-compose.homolog.yml` faz uso da diretiva `!override` (disponível no Docker Compose $\ge$ 2.24) para substituir as regras de porta:

| Serviço | Porta Interna | Ligação em Nuvem (`homolog`) | Acesso Externo (Internet) |
| :--- | :---: | :---: | :---: |
| **servico-pedidos** | `8000` | `0.0.0.0:8000` | **Liberado** (API REST pública) |
| **servico-estoque** | `50051` (gRPC), `8002` (Health) | `127.0.0.1:50051`, `127.0.0.1:8002` | **Bloqueado** (loopback apenas) |
| **postgres** | `5432` | `127.0.0.1:5432` | **Bloqueado** (loopback apenas) |
| **redis** | `6379` | `127.0.0.1:6379` | **Bloqueado** (loopback apenas) |
| **localstack** | `4566` | `127.0.0.1:4566` | **Bloqueado** (loopback apenas) |

> [!NOTE]
> Os serviços que escutam em `127.0.0.1` continuam perfeitamente acessíveis para a suíte de testes de integração e scripts executados dentro da própria instância EC2, mas ficam invisíveis para a internet.

---

## 3. Provisionamento da Instância na AWS (EC2)

### 3.1 Especificação da Instância
* **Ambiente:** AWS Academy / Free Tier
* **Região:** `us-east-1` (N. Virginia) ou `sa-east-1` (São Paulo).
* **Sistema Operacional (AMI):** `Ubuntu Server 24.04 LTS` ou `22.04 LTS` (x86_64).
* **Tipo de Instância:** `t3.micro` (2 vCPUs, 1 GB RAM — padrão obrigatório do AWS Academy).
  > [!WARNING]
  > Devido à restrição física de 1 GB de RAM da `t3.micro`, subir PostgreSQL, Redis e APIs juntos sem paginação de memória causará **OOM (Out of Memory) Killer**, derrubando o banco de dados. A criação de 2 GB de memória Swap é **estritamente obrigatória** antes de executar o deploy.
* **Armazenamento:** Mínimo de 16 GB SSD gp3.

### 3.2 Regras de Firewall (Security Group)
Crie um Security Group denominado `caixanamao-homolog-sg` contendo as seguintes regras de entrada (*Inbound Rules*):

| Tipo | Protocolo | Intervalo de Portas | Origem | Finalidade |
| :--- | :---: | :---: | :---: | :--- |
| **SSH** | TCP | `22` | `Meu IP /32` *(ou `0.0.0.0/0` se IP dinâmico)* | Acesso administrativo seguro via terminal |
| **Custom TCP** | TCP | `8000` | `0.0.0.0/0` | Acesso à API REST do Serviço de Pedidos |

> [!IMPORTANT]
> Nenhuma outra porta (como `5432`, `6379` ou `50051`) deve constar nas regras de entrada do Security Group.

---

## 4. Passo a Passo de Instalação e Execução na EC2

### 4.1 Conexão SSH
A partir da sua máquina local, acerte as permissões da chave privada e conecte à instância:
```bash
chmod 400 caixanamao-key.pem
ssh -i "caixanamao-key.pem" ubuntu@<IP-PUBLICO-DA-EC2>
```

### 4.2 Instalação dos Pré-requisitos (Docker e Docker Compose v2)
```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git curl
sudo usermod -aG docker ubuntu
```
*(Efetue logout com `exit` e reconecte via SSH para ativar as permissões de grupo do Docker sem necessidade de `sudo`).*

### 4.3 Criação Obrigatória de Swap (2GB)
Logo após a instalação das dependências e **antes** de rodar o deploy, ative o Swap de 2 GB para evitar o OOM Killer na `t3.micro`:
```bash
# Criação de Swap de 2GB para evitar OOM Killer na t3.micro
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```
*(Opcional para persistir após reboot: `echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab`)*

### 4.4 Clonagem do Repositório e Checkout
```bash
git clone https://github.com/c4rlosfb/CaixaNaMao.git
cd CaixaNaMao
git checkout feat/issue-8-deploy-aws
```

---

## 5. Execução do Deploy Automatizado

O deploy é gerenciado pelo script [`scripts/deploy.sh`](file:///c:/Users/Luan%20Dias/Desktop/CaixaNaMao/scripts/deploy.sh), que realiza todo o ciclo de vida da homologação:

```bash
bash scripts/deploy.sh
```

### 5.1 O que o script executa automaticamente:
1. **Valida versões** do Docker e Docker Compose (garantindo compatibilidade com Compose v2);
2. **Gera segredos criptográficos:** se não houver um `.env`, cria-o a partir de `.env.example` populando `POSTGRES_PASSWORD` e `JWT_SECRET` com strings pseudoaleatórias de 24 bytes (hexadecimal de 48 caracteres);
3. **Sobe os microsserviços:** executa o build dos containers combinando `docker-compose.yml` e `docker-compose.homolog.yml`;
4. **Polling de integridade (Health Check):** aguarda os endpoints de health dos serviços (`:8000/health` e `:8002/health`) responderem com HTTP 200 (timeout de 240 segundos);
5. **Resolução de IP:** consulta o serviço de metadados da AWS (IMDSv2 com fallback) e exibe as URLs de teste formatadas no console.

### 5.2 Comandos Úteis do Script
* **Subir incluindo o LocalStack (para testes com AWS SQS):**
  ```bash
  SERVICOS="postgres redis servico-estoque servico-pedidos localstack" bash scripts/deploy.sh
  ```
* **Derrubar a stack completa:**
  ```bash
  bash scripts/deploy.sh --down
  ```

---

## 6. Roteiro de Validação e Evidências

Após o término da execução do script, realize os testes para comprovar a conclusão da issue:

### 6.1 Teste 1: Verificação Externa de Borda (Máquina Local)
Da sua estação de trabalho (fora da AWS):
```bash
# Deve retornar HTTP 200 com status "healthy"
curl http://<IP-PUBLICO-EC2>:8000/health
```

No seu navegador web:
* Acesse: `http://<IP-PUBLICO-EC2>:8000/docs`
* **Evidência:** Capture um print da tela do navegador com a documentação interativa Swagger/OpenAPI carregada a partir do IP público da EC2.

### 6.2 Teste 2: Execução da Suíte de Integração (Na EC2)
Dentro da própria instância EC2 conectada via SSH:
```bash
sudo apt-get install -y python3-pip python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r tests/integracao/requirements.txt
pytest tests/integracao -q
```
* **Evidência:** Capture a saída do terminal com o resultado de **19 passed**, comprovando que os serviços interagem com o banco e o gRPC perfeitamente sob o runtime da nuvem.

---

## 7. Desligamento e Economia de Custos

Para evitar consumo desnecessário de horas ou créditos de nuvem após a validação:
1. Derrube a stack de containers:
   ```bash
   bash scripts/deploy.sh --down
   ```
2. No Console AWS EC2, selecione a instância e clique em **Estado da instância $\rightarrow$ Interromper instância (Stop Instance)**. Quando for necessário apresentar a demonstração na Sprint 5, basta iniciá-la novamente.
