"""Rota Vital: função Lambda que devolve a guia de transporte de hemocomponentes.

Entradas aceitas:
  * HTTP / API Gateway (proxy): o JSON vem em event["body"]  -> sam local start-api, generate-event
  * evento JSON direto (sem "body")                           -> sam local invoke -e exemplos/misto.json

Dois formatos de pedido:
  * simples (o do enunciado): {"requisicaoId": "R-002", "hospital": "H-Caruaru", "bolsas": ["O-NEG-0142"]}
      -> devolve a guia com o que veio no pedido.
  * completo (com "itens", como o POST /api/requisicoes do Rota Vital): aplica FEFO, compatibilidade ABO/Rh,
      cadeia fria e rota (ver dominio.py).

Demonstrativa: não consulta o banco do Rota Vital.
"""
import base64
import json
import time
from datetime import datetime, timezone

import dominio

# Código fora do handler roda uma vez por ambiente de execução (fase de Init).
# Em warm start, estas variáveis continuam com o valor da invocação anterior.
_INICIALIZADO_EM = datetime.now(timezone.utc).isoformat()
contador = 0  # variável global: só sobrevive se o ambiente for reaproveitado

ESPERA_DEMORAR_SEGUNDOS = 5


def _resposta(status, corpo):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(corpo, ensure_ascii=False),
    }


def _extrair_pedido(event):
    """Devolve o pedido, venha do body do API Gateway ou de um evento direto."""
    if isinstance(event, dict) and "body" in event:
        corpo = event["body"]
        if corpo is None or corpo == "":
            return {}
        if isinstance(corpo, (bytes, str)):
            if event.get("isBase64Encoded"):  # o sam local generate-event entrega o body em base64
                corpo = base64.b64decode(corpo)
            return json.loads(corpo)
        return corpo
    return event


def lambda_handler(event, context):
    global contador
    contador += 1

    try:
        pedido = _extrair_pedido(event)
    except (ValueError, TypeError):  # JSON inválido, base64 inválido ou UTF-8 inválido
        return _resposta(400, {"erro": "corpo deve ser um JSON válido"})

    if not isinstance(pedido, dict):
        return _resposta(400, {"erro": "o pedido deve ser um objeto JSON"})

    requisicao_id = pedido.get("requisicaoId")
    if not isinstance(requisicao_id, str) or not requisicao_id.strip():
        return _resposta(400, {"erro": "requisicaoId obrigatório"})

    if pedido.get("demorar"):
        time.sleep(ESPERA_DEMORAR_SEGUNDOS)  # passa do Timeout de 3 s do template

    if "itens" in pedido:  # pedido completo: regras do Rota Vital
        try:
            guia = dominio.emitir_guia(pedido)
        except dominio.ErroGuia as erro:
            corpo = {"erro": erro.mensagem}
            if erro.detalhes is not None:
                corpo["detalhes"] = erro.detalhes
            return _resposta(erro.status, corpo)
    else:  # pedido simples (enunciado)
        bolsas = pedido.get("bolsas", [])
        if not isinstance(bolsas, list):
            return _resposta(400, {"erro": "bolsas deve ser uma lista"})
        guia = {"guia": "G-" + requisicao_id.strip(), "hospital": pedido.get("hospital"), "bolsas": bolsas}

    guia["emitidaEm"] = datetime.now(timezone.utc).isoformat()
    guia["invocacaoNesteAmbiente"] = contador
    return _resposta(201, guia)
