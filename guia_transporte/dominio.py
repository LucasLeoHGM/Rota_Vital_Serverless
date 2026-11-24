"""Regras do Rota Vital portadas do backend Java (rotavital/src/main/java/com/hemorede).

Fonte de cada regra:
  RN-01  TipoSanguineo.DOADORES_COMPATIVEIS    (compatibilidade ABO/Rh)
  RN-02  RegraFEFO.comparar / respeitaFEFO     (validade; empate: tipo idêntico; depois coleta)
  RN-03  RegraValidadeBolsa.vencida            (bolsa vencida nunca sai)
  RN-06  RegraRefrigeracao                     (faixa de refrigeração por hemocomponente)
  RN-08  AlocacaoService                       (tudo ou nada)
  HU05   GrafoRotas.calcularMenorRota          (Dijkstra; grafo de doc/escopo-grafo.md)
         RotaController                        (tempo = km / 60 km/h * 60)

Estoque e hospitais são SINTÉTICOS (mesmos de DadosSinteticos / DataInitializer).
A função NÃO reserva bolsas: quem grava a reserva é o serviço Java. Aqui a guia é calculada
sobre um retrato do estoque, então a mesma requisição sempre dá a mesma guia.
"""
import heapq
from datetime import date, timedelta

# ------------------------------------------------------------------ catálogo
HEMOCOMPONENTES = {
    "HEMACIAS": {"validade_dias": 42, "refrigeracao": "REFRIGERADO_2_6"},
    "PLASMA": {"validade_dias": 365, "refrigeracao": "CONGELADO_MENOS_20"},
    "PLAQUETAS": {"validade_dias": 5, "refrigeracao": "AMBIENTE_CONTROLADO_20_24"},
    "CRIOPRECIPITADO": {"validade_dias": 365, "refrigeracao": "CONGELADO_MENOS_20"},
}
TEMPERATURA = {
    "REFRIGERADO_2_6": "2 a 6 °C",
    "CONGELADO_MENOS_20": "-20 °C",
    "AMBIENTE_CONTROLADO_20_24": "20 a 24 °C",
}
PRIORIDADES = ("URGENTE", "NORMAL")
TIPOS_SANGUINEOS = ("A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG")
VELOCIDADE_MEDIA_KMH = 60  # hemorede.rota.velocidade-media-kmh

# RN-01: receptor -> doadores compatíveis (do mais específico ao mais universal)
DOADORES_COMPATIVEIS = {
    "A_POS": ("A_POS", "A_NEG", "O_POS", "O_NEG"),
    "A_NEG": ("A_NEG", "O_NEG"),
    "B_POS": ("B_POS", "B_NEG", "O_POS", "O_NEG"),
    "B_NEG": ("B_NEG", "O_NEG"),
    "AB_POS": ("A_POS", "A_NEG", "B_POS", "B_NEG", "AB_POS", "AB_NEG", "O_POS", "O_NEG"),
    "AB_NEG": ("A_NEG", "B_NEG", "AB_NEG", "O_NEG"),
    "O_POS": ("O_POS", "O_NEG"),
    "O_NEG": ("O_NEG",),
}

# Hospitais (DataInitializer): id -> (nome, nó do grafo)
HOSPITAIS = {
    1: ("Hospital Regional Norte", "N1"),
    2: ("Hospital Metropolitano Sul", "N2"),
    3: ("Hospital Esperança Leste", "N3"),
    4: ("Hospital Universitário Oeste", "N4"),
    5: ("Hospital Central de Emergência II", "N5"),
}
HEMOCENTRO = "N0"

# Grafo não direcionado (doc/escopo-grafo.md): (a, b, km)
_ARESTAS = [
    ("N0", "N1", 18.0), ("N0", "N2", 15.0), ("N0", "N3", 20.0), ("N0", "N4", 17.0), ("N0", "N5", 10.0),
    ("N1", "N2", 30.0), ("N1", "N3", 25.0), ("N1", "N4", 22.0), ("N1", "N5", 20.0),
    ("N2", "N3", 28.0), ("N2", "N4", 12.0), ("N2", "N5", 8.0),
    ("N3", "N4", 35.0), ("N3", "N5", 14.0),
    ("N4", "N5", 19.0),
]


def criar_grafo(arestas=_ARESTAS):
    grafo = {}
    for a, b, km in arestas:
        grafo.setdefault(a, {})[b] = km
        grafo.setdefault(b, {})[a] = km
    return grafo


class ErroGuia(Exception):
    """Erro de negócio/validação com o status HTTP que o contrato do Rota Vital usa."""

    def __init__(self, status, mensagem, detalhes=None):
        super().__init__(mensagem)
        self.status = status
        self.mensagem = mensagem
        self.detalhes = detalhes


# ------------------------------------------------------------------ regras
def compativel(doador, receptor):
    """RN-01."""
    return doador in DOADORES_COMPATIVEIS[receptor]


def vencida(validade, hoje):
    """RN-03: vence no fim do dia da validade."""
    return validade is None or validade < hoje


def chave_fefo(bolsa, tipo_pedido):
    """RN-02: validade, depois tipo idêntico ao pedido (preserva O-), depois coleta mais antiga."""
    coleta = bolsa["dataColeta"] or date.max
    return (bolsa["dataValidade"], 0 if bolsa["tipoSanguineo"] == tipo_pedido else 1, coleta, bolsa["id"])


def respeita_fefo(validades_que_sairam, validades_que_ficaram):
    """RN-02 (auditoria): nenhuma bolsa que ficou vence antes da mais longa que saiu."""
    if not validades_que_sairam or not validades_que_ficaram:
        return True
    return min(validades_que_ficaram) >= max(validades_que_sairam)


def menor_rota(origem, destino, grafo=None):
    """Dijkstra. Devolve (caminho, custo_km); caminho vazio se não houver rota."""
    grafo = grafo or GRAFO
    if origem not in grafo or destino not in grafo:
        raise ValueError(f"nó não encontrado no grafo: {origem} / {destino}")
    if origem == destino:
        return [origem], 0.0
    dist = {origem: 0.0}
    anterior = {}
    fila = [(0.0, origem)]
    visitados = set()
    while fila:
        d, no = heapq.heappop(fila)
        if no in visitados:
            continue
        visitados.add(no)
        if no == destino:
            break
        for viz, km in grafo[no].items():
            nd = d + km
            if nd < dist.get(viz, float("inf")):
                dist[viz] = nd
                anterior[viz] = no
                heapq.heappush(fila, (nd, viz))
    if destino not in dist:
        return [], 0.0
    caminho = [destino]
    while caminho[-1] != origem:
        caminho.append(anterior[caminho[-1]])
    return caminho[::-1], dist[destino]


GRAFO = criar_grafo()  # montado uma vez, na fase de Init do ambiente


# ------------------------------------------------------------------ estoque sintético
# (componente, tipo, dias desde a coleta, dias até vencer). As 13 bolsas de hemácias são as de
# DadosSinteticos.criarBolsasExemplo(); plasma e plaquetas foram acrescentadas só para demonstrar a RN-06.
_BOLSAS_BASE = [
    ("HEMACIAS", "O_POS", 10, 2), ("HEMACIAS", "O_POS", 5, 15), ("HEMACIAS", "O_POS", 1, 35),
    ("HEMACIAS", "O_NEG", 8, 4), ("HEMACIAS", "O_NEG", 2, 30),
    ("HEMACIAS", "A_POS", 12, 3), ("HEMACIAS", "A_POS", 3, 25),
    ("HEMACIAS", "A_NEG", 6, 18), ("HEMACIAS", "B_POS", 4, 22), ("HEMACIAS", "B_NEG", 7, 14),
    ("HEMACIAS", "AB_POS", 5, 28), ("HEMACIAS", "AB_NEG", 9, 7),
    ("PLASMA", "A_POS", 20, 300), ("PLASMA", "A_POS", 10, 350), ("PLASMA", "O_NEG", 15, 340),
    ("PLAQUETAS", "A_POS", 1, 4), ("PLAQUETAS", "O_POS", 2, 3),
]


def criar_estoque(hoje):
    return [
        {
            "id": n, "hemoComponente": comp, "tipoSanguineo": tipo,
            "dataColeta": hoje - timedelta(days=coleta), "dataValidade": hoje + timedelta(days=validade),
        }
        for n, (comp, tipo, coleta, validade) in enumerate(_BOLSAS_BASE, start=1)
    ]


# ------------------------------------------------------------------ validação
def _inteiro(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validar_pedido(pedido):
    """Devolve o pedido normalizado ou levanta ErroGuia(400)."""
    if not isinstance(pedido, dict):
        raise ErroGuia(400, "o pedido deve ser um objeto JSON")
    erros = []
    req_id = pedido.get("requisicaoId")
    if not isinstance(req_id, str) or not req_id.strip():
        erros.append("requisicaoId é obrigatório")
    if not _inteiro(pedido.get("hospitalId")):
        erros.append("informe o hospital (hospitalId inteiro)")
    prioridade = pedido.get("prioridade", "NORMAL")
    if prioridade not in PRIORIDADES:
        erros.append("prioridade deve ser URGENTE ou NORMAL")
    itens = pedido.get("itens")
    if not isinstance(itens, list) or not itens:
        erros.append("informe ao menos um item")
    elif len(itens) > 20:
        erros.append("no máximo 20 itens por requisição")
    else:
        for i, it in enumerate(itens):
            if not isinstance(it, dict):
                erros.append(f"itens[{i}] deve ser um objeto")
                continue
            if it.get("hemoComponente") not in HEMOCOMPONENTES:
                erros.append(f"itens[{i}].hemoComponente inválido ({', '.join(HEMOCOMPONENTES)})")
            if it.get("tipoSanguineo") not in TIPOS_SANGUINEOS:
                erros.append(f"itens[{i}].tipoSanguineo inválido ({', '.join(TIPOS_SANGUINEOS)})")
            q = it.get("quantidade")
            if not _inteiro(q) or not 1 <= q <= 50:
                erros.append(f"itens[{i}].quantidade deve ser inteiro entre 1 e 50")
    if erros:
        raise ErroGuia(400, "requisição inválida", erros)
    return {"requisicaoId": req_id.strip(), "hospitalId": pedido["hospitalId"],
            "prioridade": prioridade, "itens": itens}


# ------------------------------------------------------------------ emissão
def selecionar_bolsas(disponiveis, item, hoje):
    """Escolhe as bolsas de um item (RN-01/02/03). `disponiveis` é uma lista de trabalho."""
    comp, tipo, qtd = item["hemoComponente"], item["tipoSanguineo"], item["quantidade"]
    candidatas = [b for b in disponiveis
                  if b["hemoComponente"] == comp and not vencida(b["dataValidade"], hoje)
                  and compativel(b["tipoSanguineo"], tipo)]
    if not candidatas:
        raise ErroGuia(422, "Nenhuma bolsa compatível disponível",
                       {"hemoComponente": comp, "tipoSanguineo": tipo})
    if len(candidatas) < qtd:
        raise ErroGuia(422, "Estoque compatível insuficiente",
                       {"hemoComponente": comp, "tipoSanguineo": tipo,
                        "necessario": qtd, "disponivel": len(candidatas)})
    ordenadas = sorted(candidatas, key=lambda b: chave_fefo(b, tipo))
    escolhidas, ficaram = ordenadas[:qtd], ordenadas[qtd:]
    if not respeita_fefo([b["dataValidade"] for b in escolhidas], [b["dataValidade"] for b in ficaram]):
        raise ErroGuia(500, "Seleção de bolsas violaria o FEFO; emissão abortada")
    return escolhidas


def emitir_guia(pedido, hoje=None, estoque=None):
    """Devolve a guia (dict). Levanta ErroGuia em caso de erro."""
    hoje = hoje or date.today()
    p = validar_pedido(pedido)

    if p["hospitalId"] not in HOSPITAIS:
        raise ErroGuia(404, f"Hospital não encontrado: {p['hospitalId']}")
    nome, no = HOSPITAIS[p["hospitalId"]]

    # Como no AlocacaoService: calcula a rota antes de mexer em qualquer bolsa.
    caminho, km = menor_rota(HEMOCENTRO, no)
    if not caminho:
        raise ErroGuia(422, f"Não há rota alcançável do Hemocentro até {nome} (nó {no})")

    # RN-08 (tudo ou nada): trabalha numa cópia; se qualquer item falhar, nada sai.
    disponiveis = list(estoque if estoque is not None else criar_estoque(hoje))
    bolsas = []
    for item in p["itens"]:
        escolhidas = selecionar_bolsas(disponiveis, item, hoje)
        ids = {b["id"] for b in escolhidas}
        disponiveis = [b for b in disponiveis if b["id"] not in ids]
        bolsas.extend(escolhidas)

    # RN-06: uma faixa de refrigeração = um volume = um veículo.
    volumes = {}
    for b in bolsas:
        faixa = HEMOCOMPONENTES[b["hemoComponente"]]["refrigeracao"]
        volumes.setdefault(faixa, []).append(b["id"])
    lista_volumes = [
        {"volume": n, "refrigeracao": faixa, "temperatura": TEMPERATURA[faixa], "bolsas": ids}
        for n, (faixa, ids) in enumerate(volumes.items(), start=1)
    ]

    guia = {
        "guia": f"G-{p['requisicaoId']}",
        "requisicaoId": p["requisicaoId"],
        "prioridade": p["prioridade"],
        "hospital": {"id": p["hospitalId"], "nome": nome, "no": no},
        "rota": {"caminho": caminho, "distanciaTotalKm": km,
                 "tempoEstimadoMinutos": round(km / VELOCIDADE_MEDIA_KMH * 60.0, 2)},
        "bolsas": [{"id": b["id"], "tipoSanguineo": b["tipoSanguineo"], "hemoComponente": b["hemoComponente"],
                    "dataValidade": b["dataValidade"].isoformat()} for b in bolsas],
        "volumes": lista_volumes,
        "despachoUnico": len(lista_volumes) == 1,
    }
    if len(lista_volumes) > 1:
        guia["aviso"] = (f"RN-06: a requisição mistura {len(lista_volumes)} faixas de refrigeração; "
                         f"são necessários {len(lista_volumes)} veículos")
    return guia
