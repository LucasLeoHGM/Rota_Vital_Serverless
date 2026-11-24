# Rota Vital — Guia de Transporte Serverless

Atividade individual 1 (Serverless) — Infraestrutura de Software, CESAR School, 2026.2.

**Caminho escolhido:** A — AWS SAM CLI local (Docker), sem publicar na nuvem.
Função AWS Lambda (Python 3.12), 128 MB, endpoint `POST /guias`, acionada por evento e por HTTP.

## O que a função faz
Gera a guia de transporte de uma requisição do Rota Vital. Aceita dois formatos de pedido:

**Simples** (o do enunciado): `{"requisicaoId":"R-002","hospital":"H-Caruaru","bolsas":["O-NEG-0142"]}`
devolve `201` com `guia`, `hospital`, `bolsas` e `invocacaoNesteAmbiente`. Sem `requisicaoId`, devolve `400`.

**Completo** (com `itens`, mesmo corpo do `POST /api/requisicoes` do backend Java, mais o `requisicaoId`):
aplica as regras do Rota Vital. O estoque e os 5 hospitais são sintéticos, os mesmos do projeto.

| Regra do Rota Vital | Origem no Java | Na função |
| --- | --- | --- |
| RN-01 compatibilidade ABO/Rh | `TipoSanguineo` | `dominio.compativel` |
| RN-02 FEFO (validade; empate: tipo idêntico; depois coleta) | `RegraFEFO` | `dominio.chave_fefo` |
| RN-03 bolsa vencida nunca sai | `RegraValidadeBolsa` | `dominio.vencida` |
| RN-06 cadeia fria: uma faixa de refrigeração por veículo | `RegraRefrigeracao` | `volumes`, `despachoUnico` |
| RN-08 tudo ou nada | `AlocacaoService` | nenhuma bolsa sai se um item falha |
| HU05 menor rota (Dijkstra), tempo = km / 60 km/h | `GrafoRotas`, `RotaController` | `dominio.menor_rota` |

No formato completo: `400` entrada inválida · `404` hospital inexistente · `422` estoque insuficiente.
A função **não reserva** bolsas (isso é do serviço Java); por isso a mesma requisição dá sempre a mesma guia.
A única variável global é `contador`, usada para observar o reaproveitamento do ambiente.

## Estrutura
```
guia_transporte/app.py       # handler (lê event["body"])
guia_transporte/dominio.py   # regras portadas do Java
events/                      # eventos no formato do API Gateway (para sam local invoke)
exemplos/                    # corpos JSON do formato completo (para curl -d @arquivo)
tests/test_app.py            # 29 testes unitários, sem SAM/Docker
template.yaml                # SAM: POST /guias, 128 MB, Timeout em Globals
evidencias/                  # prints e observacoes.md
roteiro/video-ate-3-minutos.md
```

## Requisitos
Docker em execução, AWS SAM CLI, Python 3.12. No Windows, use Git Bash ou WSL.
```bash
docker --version && sam --version && python3 --version
```

## Testes unitários
```bash
python3 -m unittest discover -s tests -v
```

## Passo 3 — invocar com evento
Gere o evento (formato igual ao que o Lambda recebe do API Gateway) e invoque duas vezes, anotando o tempo de cada uma:
```bash
sam local generate-event apigateway aws-proxy --method POST --path guias \
  --body '{"requisicaoId":"R-001","hospital":"H-Recife","bolsas":["O-NEG-0142"]}' > events/pedido.json
sam build            # se faltar Python 3.12 com pip no seu PC: sam build --use-container
sam local invoke GuiaTransporteFunction -e events/pedido.json
sam local invoke GuiaTransporteFunction -e events/pedido.json
```
Formato completo (regras do Rota Vital): `sam local invoke GuiaTransporteFunction -e events/pedido-completo.json`.

## Passo 4 — acionar por HTTP
```bash
sam local start-api                      # terminal 1
curl -i -X POST http://127.0.0.1:3000/guias -H "Content-Type: application/json" \
  -d '{"requisicaoId":"R-002","hospital":"H-Caruaru"}'        # terminal 2, três vezes
```
Pare o servidor (`Ctrl+C`), suba com `sam local start-api --warm-containers LAZY` e repita as três chamadas.
Compare `invocacaoNesteAmbiente`. Teste também:
```bash
curl -i -X POST http://127.0.0.1:3000/guias -H "Content-Type: application/json" -d '{}'               # 400
curl -i -X POST http://127.0.0.1:3000/guias -H "Content-Type: application/json" -d @exemplos/misto.json  # 201, 2 volumes (RN-06)
```

## Passo 5 — limite de tempo
Com `Timeout: 3` em `Globals` do `template.yaml`:
```bash
curl -i -X POST http://127.0.0.1:3000/guias -H "Content-Type: application/json" \
  -d '{"requisicaoId":"R-003","demorar":true}'
```
A função dorme 5 s e o limite é 3 s: registre o erro. Depois mude para `Timeout: 10`, rode `sam build`,
reinicie a API e repita: a guia `G-R-003` deve ser emitida (`201`).

## Resultados e evidências
Tabela e prints em `evidencias/` (ver `evidencias/observacoes.md`).

## Respostas às perguntas


1. Por que a primeira invocação demorou mais que a segunda? O que acontece na fase de Init?

No sam local invoke, a 1ª invocação levou 130,36 ms e a 2ª, 82,03 ms (cerca de 48 ms a mais). Isso é compatível com um cold start: antes de executar o handler, o Lambda cria o ambiente de execução, monta o código, inicia o runtime (Python 3.12) e executa o código que está fora do handler (imports, a variável global contador e, no meu projeto, o grafo de rotas e as tabelas de dominio.py). Essa preparação é a fase de Init e aparece como Init Duration na linha REPORT. Só depois roda o handler.

Uma ressalva: no emulador local o Init Duration ficou em 0,18 ms e 0,03 ms, valores muito pequenos, e cada invoke subiu um container novo (IDs diferentes, contador sempre 1). Então o emulador não reproduz o cold start real da nuvem, e a diferença que medi pode vir também de efeitos da primeira execução. O reaproveitamento ficou mais claro com --warm-containers LAZY: a 1ª chamada levou 70,15 ms (com Init) e a 2ª e a 3ª levaram 1,86 ms e 1,39 ms, sem Init Duration.

2. O que invocacaoNesteAmbiente mostrou com e sem --warm-containers? O que isso diz sobre estado em variável global?

Sem --warm-containers, o campo foi 1, 1, 1 nas três chamadas HTTP, porque cada chamada usou um ambiente novo. Com --warm-containers LAZY, foi 1, 2, 3, porque o mesmo ambiente foi reaproveitado e a variável global contador manteve o valor entre as chamadas.

Isso mostra que uma variável global só sobrevive se o ambiente for reaproveitado, e isso não é garantido: o ambiente pode ser descartado, e várias chamadas simultâneas podem usar ambientes diferentes, cada um com o seu próprio contador. Por isso não se deve guardar estado importante (como o estoque de bolsas) em variável global. Esse tipo de dado precisa ficar em um serviço externo, como um banco de dados. A variável global serve, no máximo, como otimização (por exemplo, reaproveitar uma conexão ou dados já carregados).

3. O que aconteceu com demorar = true? Qual é o tempo máximo no Lambda e o que fazer com um trabalho mais longo?

Com Timeout: 3 no template.yaml, a função dorme 5 s e passou do limite. O servidor registrou Function 'GuiaTransporteFunction' timed out after 3 seconds, Duration: 3000.00 ms, a mensagem Invalid lambda response received: Lambda response must be valid json e respondeu POST /guias ... 502. A execução foi interrompida e nenhuma guia foi gerada. Com Timeout: 10, [descreva o que aconteceu: HTTP, guia G-R-003 e o tempo aproximado da resposta].

O tempo máximo de uma execução no Lambda é de 15 minutos (900 s). Um trabalho mais longo deve ser dividido em etapas menores, ou ser colocado em uma fila (por exemplo, SQS) com processamento assíncrono, ou ser orquestrado (por exemplo, Step Functions), ou rodar em outro serviço próprio para processos longos, como contêineres.

4. Custo de 1 milhão de execuções (128 MB, 100 ms)
Memória: 128 MB = 0,125 GB. Duração: 100 ms = 0,1 s.
Por execução: 0,125 GB × 0,1 s = 0,0125 GB-s.
Um milhão de execuções: 1.000.000 × 0,0125 = 12.500 GB-s.
Custo de processamento: 12.500 × US$ 0,0000166667 = US$ 0,20833.
Custo de requisições: 1 milhão × US$ 0,20 por milhão = US$ 0,20.
Total: US$ 0,20833 + US$ 0,20 = US$ 0,40833, cerca de US$ 0,41, sem considerar a faixa gratuita.
5. Onde uma função assim faria sentido no Rota Vital, e onde não faria?

Faz sentido na emissão da guia de transporte sob demanda: é uma tarefa curta, sem estado e acionada por um evento (a requisição de um hospital), com demanda irregular. Não precisa de servidor ligado o tempo todo e paga-se só pelo que é executado. O mesmo vale para outras tarefas pontuais disparadas por evento, como enviar um alerta quando a temperatura sair da faixa.

Não faz sentido para o estoque e os dados principais (bolsas, reservas, requisições), que exigem persistência e consistência, algo que uma função sem estado garantido não oferece sozinha. Também não faz sentido para o monitoramento contínuo de temperatura e telemetria, que roda o tempo todo e funcionaria melhor em um serviço sempre ativo. E há uma ressalva para requisições urgentes: um cold start na hora errada acrescenta latência, o que pode pesar em um sistema de saúde.

## Uso de IA
Foi utilizado o ChatGPT e Claude para auxiliar na compreensão dos conceitos e na implementação da função AWS Lambda. As sugestões foram validadas por meio da execução dos comandos, testes HTTP com curl e análise dos resultados obtidos.