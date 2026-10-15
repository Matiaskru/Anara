"""Passo 1 — catálogo da REV2 (13/10/2026): regras Daune/Decor 8% + comissão fixa 5%, Daune (Visco/Látex,
protetores e pillow tops 05/08, edredons 250 g de 13/10, 180 g em REVIEW_REQUIRED, desativação dos
superados), Decor/ELIS em REVALIDAR, KTC (cotações diretas PI 23/09 e 07/10; 8 SKUs de fórmula).

Portado de `~/Anara-Cotacao-Backups/tabela_comercial_gerador/sincronizar_catalogo_rev2.py` sem mudar
a lógica: só a leitura das planilhas Daune virou dado embutido (`dados_daune.py`). Idempotente, por
sku_key; nada é apagado. Não faz commit — quem decide é `aplicar.py`.
"""
import os
import re
from datetime import date

from sqlmodel import select

from app import admin_service as adm
from app import custo_service as cs
from app import governanca_produtos as gov
from app import pricing_service as ps
from app.models import CostMethod, CotacaoItem, Fornecedor, MargemRegra, Produto

import dados_daune

S = os.path.dirname(os.path.abspath(__file__))
HOJE = date.today()
ORIGEM = "rev2-2026-10-13"

LOG = []


def log(msg):
    LOG.append(msg)
    print(msg)


def cm(t):
    m2 = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", str(t))
    if m2:
        return (int(m2.group(1)), int(m2.group(2)))
    m = re.fullmatch(r"\s*(\d+),(\d+)\s*x\s*(\d+),(\d+)\s*", str(t))
    return (int(m.group(1)) * 100 + int(m.group(2).ljust(2, "0")), int(m.group(3)) * 100 + int(m.group(4).ljust(2, "0")))


NAO_COPIAR = {"id", "sku_key", "nome", "especificacao", "custo_unitario", "preco_base", "custo_ref_valor",
              "custo_ref_moeda", "custo_ref_data", "custo_ref_documento", "status_custo", "precisa_revisao",
              "revisao_motivo", "criado_em", "atualizado_em", "exw_cotado_usd", "exw_cotado_data",
              "exw_cotado_fonte", "peso_kg", "peso_tipo", "peso_fonte", "peso_data", "peso_documento"}


def clonar(s, base, sku_key, nome, especificacao, **campos):
    """SKU novo a partir de um SKU-base (mesma família/fornecedor/fiscal). Idempotente."""
    p = s.exec(select(Produto).where(Produto.sku_key == sku_key)).first()
    if p is not None:
        return p, False
    cols = [c.name for c in Produto.__table__.columns if c.name not in NAO_COPIAR]
    p = Produto(sku_key=sku_key, nome=nome, especificacao=especificacao, **{c: getattr(base, c) for c in cols})
    for k, v in campos.items():
        setattr(p, k, v)
    p.ativo = True
    s.add(p)
    s.flush()
    return p, True


def persistir(s, mem_prod, sku_key, nome, especificacao, **campos):
    """SKU novo a partir de um produto montado em memória (calculadora/PI) — mesmos campos."""
    p = s.exec(select(Produto).where(Produto.sku_key == sku_key)).first()
    if p is not None:
        return p, False
    cols = [c.name for c in Produto.__table__.columns if c.name not in NAO_COPIAR]
    p = Produto(sku_key=sku_key, nome=nome, especificacao=especificacao, **{c: getattr(mem_prod, c) for c in cols})
    for k, v in campos.items():
        setattr(p, k, v)
    p.ativo = True
    s.add(p)
    s.flush()
    return p, True


def desativar(s, ator, p, motivo):
    usado = s.exec(select(CotacaoItem).where(CotacaoItem.produto_id == p.id)).first()
    if usado is not None:
        log(f"  ! SKU {p.id} {p.sku_key} JÁ USADO em cotação — mantido ativo (não se desativa histórico em uso)")
        return
    if not p.ativo:
        return
    p.ativo = False
    p.revisao_motivo = motivo
    s.add(p)
    adm.registrar(s, ator=ator, acao="DESATIVAR_SKU", entidade="Produto", entidade_id=p.id,
                  escopo=f"SKU {p.sku_key}", antes="ativo", depois="inativo", motivo=motivo, origem=ORIGEM)
    log(f"  desativado SKU {p.id} {p.sku_key}")


def daune_0508():
    """Linhas da 'Nova Cotação 05.08.26' (Projeto Anastacio.xlsx) — embutidas em dados_daune.py."""
    return [dict(r) for r in dados_daune.NOVA_COTACAO_0508]


# --------------------------------------------------------------------------- 1. regras
def regras(s, ator):
    forn = {f.codigo: f for f in s.exec(select(Fornecedor)).all()}
    for codigo, rotulo in (("DAUNE", "Daune"), ("DECOR_TRICOT", "Decor Tricot")):
        f = forn[codigo]
        vig = [r for r in s.exec(select(MargemRegra).where(MargemRegra.fornecedor_id == f.id)).all()
               if r.ativo and r.valid_to is None and not r.familia and not r.sku_key]
        assert len(vig) == 1, [(r.id, r.nome) for r in vig]
        r = vig[0]
        if abs(r.margem_pct - 0.08) < 1e-9 and r.comissao_fixa:
            log(f"regra {rotulo}: já 8% + comissão fixa (#{r.id})")
            continue
        kw = dict(margem_pct=0.08, nome=f"{rotulo} — B2B 8% · comissão fixa 5% · 13/10/2026", fornecedor_id=f.id,
                  prioridade=r.prioridade, fonte="Decisão comercial do Matias — 09/10/2026 (margem 8%, comissão 5% FIXA)",
                  vigente_a_partir_de=HOJE, motivo="Daune/Decor: margem-alvo do B2B 8%; comissão 5% fixa, sem escada",
                  comissao_formacao_pct=0.05, comissao_fixa=True)
        nova = adm.aplicar_margem(s, adm.preview_margem(s, **kw), ator=ator, origem=ORIGEM, **kw)
        log(f"regra {rotulo}: #{r.id} {r.margem_pct:.0%} encerrada → #{nova.id} 8% · comissão fixa {nova.comissao_formacao_pct:.0%} · {nova.politica}")


# --------------------------------------------------------------------------- 2. Daune
PROT = {"alça": "Matelassado com alça", "slip": "Matelassado com slip"}
PT = {"pena": ("90% pena e 10% plumas", "90% penas 10% plumas", 258), "fibra": ("100% fibras de poliester", "100% fibras de poliéster", 262)}


def daune(s, ator):
    linhas = daune_0508()
    # 2a. Visco / Látex — preço aprovado pelo proprietário; documento do fornecedor PENDENTE
    base = s.get(Produto, 246)
    for chave, nome, esp, bruto in (
            ("DAUNE · Travesseiros · Visco poliuretano · 50x70", "Travesseiro 50x70 · Visco poliuretano", "50x70 · Visco poliuretano", 84.48),
            ("DAUNE · Travesseiros · Látex poliuretano · 50x70", "Travesseiro 50x70 · Látex poliuretano", "50x70 · Látex poliuretano", 116.22)):
        p, novo = clonar(s, base, chave, nome, esp, cost_method=CostMethod.daune_direct.value)
        ref = cs.registrar_daune(s, p, bruto, fonte="MANUAL_OWNER_APPROVED_2026-10-09 — preço bruto Daune out/2026 informado pelo proprietário",
                                 documento="PENDENTE — anexar tabela/e-mail Daune", data_ref=date(2026, 10, 9),
                                 origem_registro=ORIGEM, notas="Documento do fornecedor pendente (auditoria 13/10)")
        log(f"Daune {'novo' if novo else 'ok'} {p.id} {nome}: bruto {bruto} → CNET {ref.cnet_brl:.4f} ({ref.status_custo})")
    # 2b. protetores de colchão — grade da Nova Cotação 05/08 (alça e slip são produtos distintos)
    base = s.get(Produto, 255)
    for modelo, spec in PROT.items():
        for r in [x for x in linhas if x["item"].startswith("Protetor de Colchão") and x["spec"] == spec]:
            l, c = r["medida"]
            chave = f"DAUNE · Protetor Colchão · {spec} · {l}x{c}"
            p, novo = clonar(s, base, chave, f"Protetor de colchão {l}x{c} · {spec.lower()}", f"{l}x{c} · {spec.lower()}",
                             largura_cm=float(l), comprimento_cm=float(c), cost_method=CostMethod.daune_direct.value)
            ref = cs.registrar_daune(s, p, r["preco"], fonte="Daune — Nova Cotação 05/08/2026 (Projeto Anastacio.xlsx)",
                                     documento="Projeto Anastacio.xlsx · Nova Cotação 05.08.26", data_ref=date(2026, 8, 5),
                                     origem_registro=ORIGEM)
            log(f"Daune {'novo' if novo else 'ok'} {p.id} {chave}: bruto {r['preco']} → CNET {ref.cnet_brl:.4f}")
    # 2c. pillow tops — grade da Nova Cotação 05/08 (1,03…2,03 x 2,03)
    for tipo, (spec, txt, base_id) in PT.items():
        base = s.get(Produto, base_id)
        for r in [x for x in linhas if x["item"].startswith("Pillow Top") and x["spec"] == spec]:
            l, c = r["medida"]
            chave = f"DAUNE · Topper Colchão · {txt} · {l}x{c}"
            p, novo = clonar(s, base, chave, f"Pillow top {l}x{c} · {txt}", f"{l}x{c} · {txt}",
                             largura_cm=float(l), comprimento_cm=float(c), cost_method=CostMethod.daune_direct.value)
            ref = cs.registrar_daune(s, p, r["preco"], fonte="Daune — Nova Cotação 05/08/2026 (Projeto Anastacio.xlsx)",
                                     documento="Projeto Anastacio.xlsx · Nova Cotação 05.08.26", data_ref=date(2026, 8, 5),
                                     origem_registro=ORIGEM)
            log(f"Daune {'novo' if novo else 'ok'} {p.id} {chave}: bruto {r['preco']} → CNET {ref.cnet_brl:.4f}")
    # 2d. edredons poliéster 250 g — tabela oficial Daune 13/10 (P054 interpolado, aprovado)
    t1310 = dict(dados_daune.EDREDOM_250_1310)
    for pid in (334, 335, 336, 337, 338):
        p = s.get(Produto, pid)
        assert p.gsm == 250 and "fibras de poliéster" in p.sku_key, p.sku_key
        ch = (int(p.largura_cm), int(p.comprimento_cm))
        if ch in t1310:
            bruto, status, nota = t1310[ch], "CONFIRMADO", "medida exata na tabela Daune 13/10"
        else:
            a, b = t1310[(ch[0], ch[1] - 15)], t1310[(ch[0], ch[1] - 5)]
            bruto, status = round(b + (b - a) * 5 / 10, 2), "ESTIMADO"
            nota = f"interpolação aprovada {ch[0]}x{ch[1] - 15}→{ch[0]}x{ch[1] - 5} +5 cm"
        p.construcao = "tecido externo microfibra + manta siliconizada 250 g/m²"
        ref = cs.registrar_daune(s, p, bruto, fonte="Daune — Custo Edredons manta 250 gramas Anara.xlsx (13/10/2026)",
                                 documento="Custo Edredons manta 250 gramas Anara.xlsx", data_ref=date(2026, 10, 13),
                                 status=status, origem_registro=ORIGEM, notas=nota)
        log(f"Daune edredom 250 {pid}: bruto {bruto} ({status}) → CNET {ref.cnet_brl:.4f}")
    # 2e. edredom poliéster 180 g — custo preservado, status REVIEW_REQUIRED (sob consulta)
    obs = ("Aguardando confirmação Daune sobre construção/custo da linha 180 g. Fonte 05/08 existe, porém não é "
           "tecnicamente comparável à nova linha microfibra + manta siliconizada 250 g.")
    for pid in (324, 325, 326, 327, 328):
        p = s.get(Produto, pid)
        assert p.gsm == 180 and "fibras de poliéster" in p.sku_key, p.sku_key
        vig = cs.referencia_vigente(s, pid)
        ref = cs.registrar_daune(s, p, vig.valor_bruto, fonte="Daune — Nova Cotação 05/08/2026 (Projeto Anastacio.xlsx), construção a confirmar",
                                 documento=vig.documento, data_ref=vig.data_ref, status="REVIEW_REQUIRED",
                                 origem_registro=ORIGEM, notas=obs)
        log(f"Daune edredom 180 fibra {pid}: REVIEW_REQUIRED (bruto {vig.valor_bruto} preservado, v{ref.versao})")
    # 2f. superados — desativar (histórico intacto)
    for pid in (254, 255, 256, 257):
        desativar(s, ator, s.get(Produto, pid), "Superado pela grade da Nova Cotação Daune 05/08 (matelassado com alça / com slip); "
                                               "a lista 27/07 'impermeável' não identifica o modelo (auditoria 13/10)")
    for pid in range(258, 266):
        desativar(s, ator, s.get(Produto, pid), "Superado pela grade 1,03…2,03 x 2,03 da Nova Cotação Daune 05/08 (auditoria 13/10)")
    for pid in range(340, 349):
        desativar(s, ator, s.get(Produto, pid), "Linha '280 g' sem fonte primária: os valores (03/09) são os da lista Daune 12/08 sem "
                                               "gramatura, que a Daune 05/08 identifica como 180 g (auditoria 13/10)")


# --------------------------------------------------------------------------- 3. Decor / ELIS
def decor_elis(s, ator):
    for p in s.exec(select(Produto)).all():
        f = ps.fornecedor_do_produto(s, p) if p.fornecedor_id else None
        if not p.ativo or f is None:
            continue
        vig = cs.referencia_vigente(s, p.id)
        if vig is None:          # sem referência versionada: não há o que rebaixar
            continue
        if f.codigo == "DECOR_TRICOT" and p.familia == "Bed Runner":
            if vig.status_custo != "REVALIDAR":
                gov.marcar_status(s, p, status="REVALIDAR", ator=ator,
                                  fonte="SOURCE_DOCUMENT_MISSING — valor legado do sistema; orçamento original Decor (24/08) não localizado",
                                  motivo="Auditoria 13/10: sem documento primário do custo Decor")
                log(f"Decor {p.id} → REVALIDAR")
        if f.codigo == "ELIS":
            if vig.status_custo != "REVALIDAR":
                gov.marcar_status(s, p, status="REVALIDAR", ator=ator,
                                  fonte="Fonte comercial original não localizada",
                                  motivo="Confirmar tratamento econômico de 'preço NET' da ELIS (auditoria 13/10)")
                log(f"ELIS {p.id} → REVALIDAR")


# --------------------------------------------------------------------------- 4. KTC
def ktc(s, ator):
    from ktc_07_10 import KTC, produto_ktc
    from pi_23_09 import PI, produto_pi
    import catalogo_rev2 as cat
    for cod, d in cat.KTC_SKUS.items():
        if d["origem"] == "pi":
            cpi = d.get("pi_de", cod)
            mem = produto_pi(s, cpi)
            exw, peso, peso_tipo = PI[cpi][1], PI[cpi][2], "REAL KTC"
            doc, data = "PI KTC IQA23092026", date(2026, 9, 23)
            fonte = f"PI KTC IQA23092026 (23/09/2026), linha {PI[cpi][3] + (1 if cod == 'P016#azul' else 0)}"
        else:
            e = KTC[cod]
            mem = produto_ktc(s, cod)
            exw, peso, peso_tipo = e["exw"], e["peso"], e["peso_tipo"]
            doc = e["doc"] or "cotação KTC 29/07/2026 (amostra 35)"
            data = e["data"]
            fonte = (f"{e['doc']}, {e['linha_doc']}" if e["doc"] else mem.exw_cotado_fonte).replace(" · ", ", ")
        if d.get("sku_id"):
            p = s.get(Produto, d["sku_id"])
            assert p is not None and p.ativo, (cod, d)
            novo = False
            if p.cost_method == CostMethod.ktc_calculated.value and exw is not None:
                # existe cotação DIRETA exata para este SKU: ela vence a fórmula (precedência
                # da rodada 13/10) — troca explícita e auditada do método de custo
                p.cost_method = CostMethod.ktc_quoted.value
                s.add(p)
                adm.registrar(s, ator=ator, acao="METODO_CUSTO", entidade="Produto", entidade_id=p.id,
                              escopo=f"SKU {p.sku_key}", antes="KTC_CALCULATED", depois="KTC_QUOTED",
                              motivo=f"cotação direta exata: {fonte}", origem=ORIGEM)
        else:
            p, novo = persistir(s, mem, d["sku_key"], d["nome"], d["especificacao"], **(d.get("campos") or {}))
        ja = (p.exw_cotado_usd is not None and exw is not None and abs(p.exw_cotado_usd - exw) < 1e-9
              and p.exw_cotado_data == data and p.peso_kg is not None and abs(p.peso_kg - peso) < 1e-9
              and p.peso_tipo == peso_tipo and cs.referencia_vigente(s, p.id) is not None)
        if exw is None and p.peso_kg is not None and abs(p.peso_kg - peso) < 1e-9 and p.peso_tipo == peso_tipo:
            r = {"status": ps.status_do_produto(s, p)}
        elif ja:
            r = {"status": ps.status_do_produto(s, p)}
        elif exw is None:      # BR-028: mantém o EXW de 29/07; só ganha o peso estimado
            gov.registrar_peso(s, p, peso_kg=peso, tipo=peso_tipo, fonte=mem.peso_fonte, documento=None,
                               data_ref=HOJE, motivo="peso logístico estimado (análogo spa) — auditoria 13/10", ator=ator)
            r = {"status": ps.status_do_produto(s, p)}
        else:
            r = gov.registrar_exw_ktc(s, p, exw_usd=exw, data_ref=data, documento=doc, fonte=fonte,
                                      motivo=f"tabela 13-10 REV2 — {cod}", ator=ator, peso_kg=peso, peso_tipo=peso_tipo,
                                      observacao=d.get("obs"))
        if d.get("ncm") and p.ncm != d["ncm"]:
            p.ncm = d["ncm"]
            s.add(p)
        if d.get("rebaixar"):
            vig = cs.referencia_vigente(s, p.id)
            if vig is None or vig.status_custo != "REVALIDAR":
                gov.marcar_status(s, p, status="REVALIDAR", ator=ator, fonte=fonte, motivo=d["rebaixar"])
        log(f"KTC {cod} {'novo' if novo else 'atualizado'} {p.id} {p.sku_key}: EXW {exw} · peso {peso} {peso_tipo} → "
            f"{ps.status_do_produto(s, p)}")
    # fórmula de alto risco: SKU próprio em REVIEW_REQUIRED (sob consulta até cotação direta)
    from app import calculadora as calc
    for cod, d in cat.KTC_REVIEW.items():
        mem = calc.produto_simulado(s, **d["spec"])
        p, novo = persistir(s, mem, d["sku_key"], d["nome"], d["especificacao"])
        custo, memoria = ps.custo_para_precificar(s, p)
        # só no nascimento do SKU: depois, o passo 2 (calibração) decide o status — rebaixar de novo
        # numa reexecução só criaria versões que o passo 2 desfaz (diferença do script original)
        if cs.referencia_vigente(s, p.id) is None:
            cs.registrar_referencia(s, p, cnet_brl=float(custo), metodo=CostMethod.ktc_calculated.value,
                                    status="REVIEW_REQUIRED", fonte="KTC_CALCULATED — auditoria 13/10",
                                    documento=None, data_ref=HOJE, origem_registro=ORIGEM, notas=d["motivo"],
                                    memoria={"exw_formula_usd": memoria.get("exw_usd"), "motivo": d["motivo"]})
        log(f"KTC fórmula {cod} {'novo' if novo else 'ok'} {p.id}: REVIEW_REQUIRED (EXW fórmula {memoria.get('exw_usd')})")


def preflight(s):
    """O patch usa ids fixos (bases de clonagem, SKUs a atualizar/desativar). Antes de gravar
    QUALQUER coisa, cada id precisa ter no banco alvo a mesma sku_key do banco de referência
    (data/anara.db, 13/10) — senão aborta: em produção um id pode ser outro produto."""
    import json
    esp = json.load(open(os.path.join(S, "ids_esperados_rev2.json")))["ids"]
    erros = []
    for i, chave in esp.items():
        p = s.get(Produto, int(i))
        if p is None or p.sku_key != chave:
            erros.append(f"id {i}: esperado '{chave}', banco tem '{getattr(p, 'sku_key', None)}'")
    if erros:
        raise SystemExit("PREFLIGHT FALHOU — nada foi gravado:\n  " + "\n  ".join(erros))
    log(f"preflight ok: {len(esp)} ids conferidos por sku_key")
