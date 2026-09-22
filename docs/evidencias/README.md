# Evidências de homologação — EC2 (Sprint 2 / Issue #8)

Capturas de **21/09/2026**, na instância EC2 `i-09df326d451a729cd` (`caixanamao`, `us-east-1`, Ubuntu Server, `t3.micro`).

| Arquivo | O que mostra |
|---|---|
| `2026-09-21-deploy-ec2.png` | Saída do `bash scripts/deploy.sh` na instância: build dos serviços, criação dos containers e o `docker compose ps` final com `postgres`, `redis`, `servico-estoque` e `servico-pedidos` em **healthy** — apenas a `0.0.0.0:8000` publicada, o resto preso em `127.0.0.1`. |
| `2026-09-21-curl-externo.png` | Requisição partindo da **máquina local, fora da AWS**, contra o IP público da instância: `StatusCode 200`, `Content-Type: application/json` e corpo `{"status":"ok","service":"servico-pedidos"}`. |

## Observação sobre endereços

O **IP público muda quando a instância é parada e iniciada** — e o guia de deploy sugere `Stop Instance` para economizar créditos. Na captura do deploy, a instância respondia em **`34.207.184.111`** (privado `172.31.30.199`); a captura de `curl` interno registra o hostname `ip-172-31-19-186`, de um ciclo anterior de parada/início da mesma instância.

Para a demonstração, o caminho é associar um **Elastic IP** à instância: o endereço passa a sobreviver a stop/start e os roteiros de teste param de depender de editar o Security Group e a URL.
