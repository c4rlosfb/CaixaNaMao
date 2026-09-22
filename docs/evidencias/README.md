# Evidências de homologação — EC2 (Sprint 2 / Issue #8)

Capturas de **21/09/2026**, na instância EC2 `caixanamao` (`us-east-1`, Ubuntu Server, `t3.micro`), provisionada no **AWS Academy Sandbox**.

| Arquivo | O que mostra |
|---|---|
| `2026-09-21-docker-ps-ec2.png` | `docker ps` na instância: os quatro containers em **healthy**, com `0.0.0.0:8000` como **única porta pública** e `127.0.0.1` para `5432`, `6379`, `8002` e `50051`. |
| `2026-09-21-deploy-ec2.png` | Saída do `bash scripts/deploy.sh`: build dos serviços, health checks e detecção do IP público via **IMDSv2**. |
| `2026-09-21-curl-interno-ec2.png` | `curl http://127.0.0.1:8000/health` **dentro da instância** → `{"status":"ok","service":"servico-pedidos"}`. |
| `2026-09-21-curl-externo.png` | PowerShell na **máquina local, fora da AWS**: `curl http://34.207.184.111:8000/health` → `StatusCode 200`, `Content-Type: application/json` e o corpo do health check. |

## Por que os endereços mudam entre capturas

O laboratório roda em **AWS Academy Sandbox**: a instância é **destruída ao fim de cada sessão** e o **IP público muda a cada novo provisionamento** — inclusive o privado. É por isso que capturas de ciclos diferentes mostram hostnames distintos (`ip-172-31-19-186`, `ip-172-31-30-199`) e endereços públicos diferentes (`54.226.19.86`, `34.207.184.111`).

**Antes de testar, confira o IP atual no painel da AWS** (EC2 → Instâncias → *Public IPv4 address*). O procedimento e as consequências estão no aviso importante em [`docs/deploy-aws.md` §3.1](../deploy-aws.md).
