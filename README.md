# StrongPrint — orquestrador central

Serviço central da rede de kiosks de impressão StrongPrint: gere parceiros, kiosks,
impressoras, carteiras dos clientes e o ciclo de vida de cada trabalho de impressão.

## Arrancar em desenvolvimento

```bash
make up          # cria .env a partir de .env.example e levanta a stack
make superuser   # cria o superadmin (login pelo nº de telefone)
make test        # testes
make cov         # testes + cobertura
make lint        # ruff
```

- Admin (superadmin StrongPrint): http://localhost:8000/admin/
- S3 de desenvolvimento (SeaweedFS): http://localhost:8333/

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `domain/` | Lógica pura, sem Django (máquina de estados; análise, preços e gateways nas fases seguintes) |
| `apps/accounts` | Utilizador com login por telefone (clientes, parceiros e equipa) |
| `apps/partners` | Parceiro → Local → Kiosk, acessos de parceiros, tabelas de preço herdáveis |
| `apps/devices` | Raspberry Pi (EdgeDevice) e impressora |
| `apps/jobs` | Job, JobEvent (auditoria) e o serviço `transition()` |
| `apps/wallets` | Carteira pré-paga: livro-razão, cativações e receita por kiosk |
| `common/` | Logs JSON, armazenamento S3 |

## Máquina de estados do Job

```
CREATED → FILE_RECEIVED → ANALYZED → RESERVED → QUEUED → PRINTING → COMPLETED | PARTIAL | FAILED
             └→ REJECTED     (CREATED/FILE_RECEIVED/ANALYZED) → EXPIRED
CANCELLED a partir de qualquer estado antes de PRINTING
```

`RESERVED` = valor cativo na carteira. No fim debita-se só o que foi impresso e
o resto é libertado. A receita fica atribuída ao kiosk onde se imprimiu.
