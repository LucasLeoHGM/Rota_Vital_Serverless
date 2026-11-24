import json
import os
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "guia_transporte"))
import app  # noqa: E402
import dominio  # noqa: E402

HOJE = date(2026, 11, 24)
RAIZ = os.path.join(os.path.dirname(__file__), "..")


def pedido(**extra):
    base = {"requisicaoId": "R-T1", "hospitalId": 2, "prioridade": "NORMAL",
            "itens": [{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS", "quantidade": 2}]}
    base.update(extra)
    return base


def bolsa(id_, tipo, validade_dias, comp="HEMACIAS", coleta_dias=5):
    return {"id": id_, "hemoComponente": comp, "tipoSanguineo": tipo,
            "dataColeta": HOJE - timedelta(days=coleta_dias), "dataValidade": HOJE + timedelta(days=validade_dias)}


def http(body):
    return {"httpMethod": "POST", "path": "/guias", "body": body, "requestContext": {}}


def corpo(resp):
    return json.loads(resp["body"])


class TestRegras(unittest.TestCase):
    def test_rn01_compatibilidade(self):
        self.assertTrue(dominio.compativel("O_NEG", "AB_POS"))
        self.assertTrue(dominio.compativel("A_NEG", "A_POS"))
        self.assertFalse(dominio.compativel("A_POS", "A_NEG"))   # Rh+ não vai para Rh-
        self.assertFalse(dominio.compativel("A_POS", "O_POS"))   # A não vai para O
        for receptor in dominio.TIPOS_SANGUINEOS:                # todo receptor aceita o próprio tipo
            self.assertTrue(dominio.compativel(receptor, receptor))

    def test_rn02_fefo_ordena_por_validade(self):
        estoque = [bolsa(1, "A_POS", 20), bolsa(2, "A_POS", 3), bolsa(3, "A_POS", 10)]
        g = dominio.emitir_guia(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS",
                                               "quantidade": 2}]), HOJE, estoque)
        self.assertEqual([b["id"] for b in g["bolsas"]], [2, 3])

    def test_rn02_empate_prefere_tipo_identico_e_preserva_o_neg(self):
        estoque = [bolsa(1, "O_NEG", 5), bolsa(2, "A_POS", 5)]
        g = dominio.emitir_guia(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS",
                                               "quantidade": 1}]), HOJE, estoque)
        self.assertEqual(g["bolsas"][0]["id"], 2)

    def test_rn02_empate_total_usa_coleta_mais_antiga(self):
        estoque = [bolsa(1, "A_POS", 5, coleta_dias=2), bolsa(2, "A_POS", 5, coleta_dias=9)]
        g = dominio.emitir_guia(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS",
                                               "quantidade": 1}]), HOJE, estoque)
        self.assertEqual(g["bolsas"][0]["id"], 2)

    def test_rn02_auditoria(self):
        d = HOJE
        self.assertTrue(dominio.respeita_fefo([d], [d + timedelta(days=1)]))
        self.assertFalse(dominio.respeita_fefo([d + timedelta(days=5)], [d + timedelta(days=1)]))

    def test_rn03_bolsa_vencida_nunca_sai(self):
        estoque = [bolsa(1, "A_POS", -1), bolsa(2, "A_POS", 0), bolsa(3, "A_POS", 9)]
        g = dominio.emitir_guia(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS",
                                               "quantidade": 2}]), HOJE, estoque)
        self.assertEqual([b["id"] for b in g["bolsas"]], [2, 3])  # vence hoje ainda vale; ontem não

    def test_rn06_requisicao_mista_gera_dois_volumes(self):
        g = dominio.emitir_guia(pedido(itens=[
            {"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS", "quantidade": 1},
            {"hemoComponente": "PLASMA", "tipoSanguineo": "A_POS", "quantidade": 1}]), HOJE)
        self.assertFalse(g["despachoUnico"])
        self.assertEqual({v["refrigeracao"] for v in g["volumes"]},
                         {"REFRIGERADO_2_6", "CONGELADO_MENOS_20"})
        self.assertIn("aviso", g)

    def test_rn06_mesma_faixa_e_despacho_unico(self):
        g = dominio.emitir_guia(pedido(), HOJE)
        self.assertTrue(g["despachoUnico"])
        self.assertEqual(len(g["volumes"]), 1)
        self.assertNotIn("aviso", g)

    def test_rn08_tudo_ou_nada(self):
        estoque = dominio.criar_estoque(HOJE)
        antes = list(estoque)
        ped = pedido(itens=[
            {"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS", "quantidade": 1},
            {"hemoComponente": "PLAQUETAS", "tipoSanguineo": "O_POS", "quantidade": 9}])
        with self.assertRaises(dominio.ErroGuia) as ctx:
            dominio.emitir_guia(ped, HOJE, estoque)
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(estoque, antes)  # nada foi consumido

    def test_itens_do_mesmo_pedido_nao_repetem_bolsa(self):
        g = dominio.emitir_guia(pedido(itens=[
            {"hemoComponente": "HEMACIAS", "tipoSanguineo": "O_NEG", "quantidade": 1},
            {"hemoComponente": "HEMACIAS", "tipoSanguineo": "O_NEG", "quantidade": 1}]), HOJE)
        ids = [b["id"] for b in g["bolsas"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_estoque_insuficiente_informa_necessario_e_disponivel(self):
        with self.assertRaises(dominio.ErroGuia) as ctx:
            dominio.emitir_guia(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "O_NEG",
                                               "quantidade": 3}]), HOJE)
        self.assertEqual(ctx.exception.status, 422)
        self.assertEqual(ctx.exception.detalhes["necessario"], 3)
        self.assertEqual(ctx.exception.detalhes["disponivel"], 2)


class TestRota(unittest.TestCase):
    def test_dijkstra_do_hemocentro_ate_cada_hospital(self):
        self.assertEqual(dominio.menor_rota("N0", "N2"), (["N0", "N2"], 15.0))
        self.assertEqual(dominio.menor_rota("N0", "N5"), (["N0", "N5"], 10.0))
        self.assertEqual(dominio.menor_rota("N0", "N0"), (["N0"], 0.0))

    def test_dijkstra_prefere_atalho_indireto_mais_barato(self):
        grafo = dominio.criar_grafo([("N0", "N3", 20.0), ("N0", "N5", 5.0), ("N5", "N3", 6.0)])
        self.assertEqual(dominio.menor_rota("N0", "N3", grafo), (["N0", "N5", "N3"], 11.0))

    def test_dijkstra_sem_rota(self):
        grafo = dominio.criar_grafo([("N0", "N1", 1.0), ("N2", "N3", 1.0)])
        self.assertEqual(dominio.menor_rota("N0", "N3", grafo), ([], 0.0))

    def test_tempo_estimado_usa_60_kmh(self):
        g = dominio.emitir_guia(pedido(hospitalId=2), HOJE)
        self.assertEqual(g["rota"]["distanciaTotalKm"], 15.0)
        self.assertEqual(g["rota"]["tempoEstimadoMinutos"], 15.0)
        g = dominio.emitir_guia(pedido(hospitalId=3), HOJE)
        self.assertEqual(g["rota"]["tempoEstimadoMinutos"], 20.0)


class TestValidacao(unittest.TestCase):
    def assert_erro(self, ped, status):
        with self.assertRaises(dominio.ErroGuia) as ctx:
            dominio.emitir_guia(ped, HOJE)
        self.assertEqual(ctx.exception.status, status)
        return ctx.exception

    def test_requisicao_id_obrigatorio(self):
        ped = pedido()
        del ped["requisicaoId"]
        self.assertIn("requisicaoId é obrigatório", self.assert_erro(ped, 400).detalhes)

    def test_campos_invalidos_viram_400(self):
        self.assert_erro(pedido(prioridade="TALVEZ"), 400)
        self.assert_erro(pedido(itens=[]), 400)
        self.assert_erro(pedido(itens=[{"hemoComponente": "SANGUE", "tipoSanguineo": "X", "quantidade": 0}]), 400)
        self.assert_erro(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "A_POS",
                                        "quantidade": 51}]), 400)
        self.assert_erro(pedido(hospitalId="2"), 400)
        self.assert_erro([], 400)

    def test_hospital_inexistente_e_404(self):
        self.assert_erro(pedido(hospitalId=99), 404)


class TestHandler(unittest.TestCase):
    def setUp(self):
        app.contador = 0

    def test_eventos_de_exemplo_geram_guia(self):
        for pasta, nome in (("events", "pedido.json"), ("events", "pedido-completo.json"),
                            ("exemplos", "http-pedido.json"), ("exemplos", "misto.json")):
            with open(os.path.join(RAIZ, pasta, nome), encoding="utf-8") as f:
                resp = app.lambda_handler(json.load(f), None)
            self.assertEqual(resp["statusCode"], 201, nome)

    def test_evento_gerado_pelo_sam_com_body_em_base64(self):
        evento = {"httpMethod": "POST", "path": "/guias", "isBase64Encoded": True, "requestContext": {},
                  "body": "eyJyZXF1aXNpY2FvSWQiOiJSLTAwMSIsImhvc3BpdGFsIjoiSC1SZWNpZmUiLCJib2xzYXMiOlsiTy1ORUctMDE0MiJdfQ=="}
        r = app.lambda_handler(evento, None)
        c = corpo(r)
        self.assertEqual((r["statusCode"], c["guia"], c["hospital"], c["bolsas"]),
                         (201, "G-R-001", "H-Recife", ["O-NEG-0142"]))

    def test_base64_invalido_vira_400(self):
        evento = {"httpMethod": "POST", "isBase64Encoded": True, "requestContext": {}, "body": "%%%nao-e-base64%%%"}
        self.assertEqual(app.lambda_handler(evento, None)["statusCode"], 400)

    # ---- pedido simples, exatamente como nos comandos do enunciado
    def test_enunciado_evento_proxy_gerado_pelo_sam(self):
        c = corpo(app.lambda_handler(http('{"requisicaoId":"R-001","hospital":"H-Recife","bolsas":["O-NEG-0142"]}'), None))
        self.assertEqual((c["guia"], c["hospital"], c["bolsas"]), ("G-R-001", "H-Recife", ["O-NEG-0142"]))
        self.assertEqual(c["invocacaoNesteAmbiente"], 1)

    def test_enunciado_curl_sem_bolsas(self):
        r = app.lambda_handler(http('{"requisicaoId":"R-002","hospital":"H-Caruaru"}'), None)
        self.assertEqual(r["statusCode"], 201)
        self.assertEqual(corpo(r)["bolsas"], [])

    def test_enunciado_demorar_sem_hospital_gera_guia_depois_de_esperar(self):
        with mock.patch.object(app.time, "sleep") as dormir:
            r = app.lambda_handler(http('{"requisicaoId":"R-003","demorar":true}'), None)
        dormir.assert_called_once_with(5)
        self.assertEqual((r["statusCode"], corpo(r)["guia"]), (201, "G-R-003"))

    def test_sem_requisicao_id_nao_espera_nem_gera_guia(self):
        with mock.patch.object(app.time, "sleep") as dormir:
            r = app.lambda_handler(http('{"demorar":true}'), None)
        dormir.assert_not_called()
        self.assertEqual((r["statusCode"], corpo(r)["erro"]), (400, "requisicaoId obrigatório"))

    def test_evento_direto(self):
        resp = app.lambda_handler(pedido(requisicaoId="R-001"), None)
        c = corpo(resp)
        self.assertEqual((resp["statusCode"], c["guia"]), (201, "G-R-001"))
        self.assertEqual(c["invocacaoNesteAmbiente"], 1)

    def test_http_com_body_string(self):
        resp = app.lambda_handler(http(json.dumps(pedido(requisicaoId="R-002"))), None)
        self.assertEqual(corpo(resp)["guia"], "G-R-002")

    def test_vazio_json_invalido_e_404_e_422(self):
        self.assertEqual(app.lambda_handler(http("{}"), None)["statusCode"], 400)
        self.assertEqual(app.lambda_handler(http("{quebrado"), None)["statusCode"], 400)
        self.assertEqual(app.lambda_handler(http(None), None)["statusCode"], 400)
        self.assertEqual(app.lambda_handler(pedido(hospitalId=99), None)["statusCode"], 404)
        r = app.lambda_handler(pedido(itens=[{"hemoComponente": "HEMACIAS", "tipoSanguineo": "O_NEG",
                                              "quantidade": 40}]), None)
        self.assertEqual(r["statusCode"], 422)

    def test_contador_persiste_no_mesmo_ambiente(self):
        n = [corpo(app.lambda_handler(pedido(requisicaoId=f"R-{i}"), None))["invocacaoNesteAmbiente"]
             for i in range(3)]
        self.assertEqual(n, [1, 2, 3])

    def test_a_mesma_requisicao_da_a_mesma_guia(self):
        a = corpo(app.lambda_handler(pedido(), None))["bolsas"]
        b = corpo(app.lambda_handler(pedido(), None))["bolsas"]
        self.assertEqual(a, b)  # a função não reserva estoque

    def test_demorar_espera_antes_de_responder(self):
        with mock.patch.object(app.time, "sleep") as dormir:
            resp = app.lambda_handler(pedido(requisicaoId="R-003", demorar=True), None)
        dormir.assert_called_once_with(app.ESPERA_DEMORAR_SEGUNDOS)
        self.assertEqual(resp["statusCode"], 201)


if __name__ == "__main__":
    unittest.main()
