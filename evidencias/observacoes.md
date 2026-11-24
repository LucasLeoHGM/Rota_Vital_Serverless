# Observações dos experimentos

Atividade individual 1 — Serverless (Caminho A: AWS SAM CLI local). Todos os valores abaixo vêm das saídas dos prints desta pasta.

## Ambiente
- Sistema operacional: Windows, terminal PowerShell no VS Code
- Docker: `[preencher: saída de docker --version]`
- SAM CLI: `[preencher: saída de sam --version]`
- Python do projeto: 3.12 (runtime `python3.12` no `template.yaml`; build com `sam build --use-container`)
- Memória: 128 MB · Timeout: 3 s (depois 10 s) · Endpoint: `POST /guias`
- Print: [`01-ambiente.png`](01-ambiente.png)

## Resultados

### 1. Invocação com evento (`sam local invoke`, evento gerado com `generate-event`)
| Invocação | Duration | Billed | Init Duration | `invocacaoNesteAmbiente` | Container | Print |
| --- | --- | --- | --- | --- | --- | --- |
| 1ª | 130,36 ms | 131 ms | 0,18 ms | 1 | `05b11164…` | [`02-build-e-invoke-1.png`](02-build-e-invoke-1.png) |
| 2ª | 82,03 ms | 83 ms | 0,03 ms | 1 | `f55dbbda…` | [`03-invoke-2.png`](03-invoke-2.png) |

Observação: cada `invoke` usou um container novo (IDs diferentes) e o contador voltou a 1.

### 2. HTTP sem reaproveitar o ambiente (`sam local start-api`, 3 chamadas `curl.exe`)
| Chamada | HTTP | `invocacaoNesteAmbiente` | Duration | Billed | Init Duration | Container |
| --- | --- | --- | --- | --- | --- | --- |
| 1ª | 201 | 1 | 80,10 ms | 81 ms | 0,03 ms | `bbc6d793…` |
| 2ª | 201 | 1 | 73,87 ms | 74 ms | 0,11 ms | `2881b8e4…` |
| 3ª | 201 | 1 | 67,85 ms | 68 ms | 0,03 ms | `f6901c26…` |

Prints: [`04-start-api-sem-warm.png`](04-start-api-sem-warm.png) (servidor no ar) · [`05-http-sem-warm-curl.png`](05-http-sem-warm-curl.png) (respostas do `curl`) · [`06-http-sem-warm-log.png`](06-http-sem-warm-log.png) (log do servidor).
Observação: um container novo por chamada, contador sempre 1.

### 3. HTTP com reaproveitamento (`sam local start-api --warm-containers LAZY`, 3 chamadas)
| Chamada | HTTP | `invocacaoNesteAmbiente` | Duration | Billed | Init Duration |
| --- | --- | --- | --- | --- | --- |
| 1ª | 201 | 1 | 70,15 ms | 71 ms | 0,04 ms |
| 2ª | 201 | 2 | 1,86 ms | 2 ms | (sem Init) |
| 3ª | 201 | 3 | 1,39 ms | 2 ms | (sem Init) |

Prints: [`07-start-api-warm.png`](07-start-api-warm.png) (servidor) · [`08-http-com-warm-log.png`](08-http-com-warm-log.png) (log do servidor) · [`09-http-com-warm-curl.png`](09-http-com-warm-curl.png) (respostas do `curl`, com o contador).
Observação: o contador subiu 1, 2, 3 (mesmo ambiente reaproveitado). A 2ª e a 3ª chamadas mostram "Lambda function 'GuiaTransporteFunction' is already running" e não têm `Init Duration`.

### 4. Limite de tempo (`demorar: true`, a função dorme 5 s)
| Timeout no `template.yaml` | Resultado | Print |
| --- | --- | --- |
| 3 s | `[preencher: erro e HTTP retornados]` | [`10-template-timeout-3.png`](10-template-timeout-3.png) · [`11-timeout-erro.png`](11-timeout-erro.png) |
| 10 s | `[preencher: HTTP, guia G-R-003 e tempo aproximado]` | [`12-template-timeout-10.png`](12-template-timeout-10.png) · [`13-timeout-ok.png`](13-timeout-ok.png) |

### 5. Extras (formato completo do Rota Vital)
| Teste | Resultado esperado | Print |
| --- | --- | --- |
| `{}` | 400, `requisicaoId obrigatório` | [`14-extra-400.png`](14-extra-400.png) |
| `exemplos/misto.json` | 201, 2 volumes de refrigeração (RN-06) | [`15-extra-misto.png`](15-extra-misto.png) |

## Notas para as respostas do README
- O `Init Duration` local ficou em 0,03 a 0,18 ms em todas as chamadas: o emulador não reproduz o cold start da nuvem.
- `Max Memory Used: 128 MB` é só o limite configurado que o emulador exibe, não é uso real.
- No `invoke` e no `start-api` sem warm, cada chamada usou um container diferente.
