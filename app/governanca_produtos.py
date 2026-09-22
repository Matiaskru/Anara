"""Governança de produtos e custos — o que o administrador faz sem terminal (22/09/2026).

A tela `Admin → Produtos e custos` existe porque faltava o caminho de volta: o motor sabe
dizer que um SKU está em `REVIEW_REQUIRED` e **por quê**, mas não havia onde resolver isso.
Quem descobria um roupão com EXW cotado e sem peso precisava de SQL.

Três regras moldam este módulo, e são as mesmas do resto do sistema:

1. **Nada aqui inventa premissa.** Liberar um SKU é fornecer o dado que falta — peso, EXW,
   custo, fonte, documento, data. `confirmar_referencia` recusa enquanto o motor apontar
   premissa faltante: confirmar seria afirmar uma evidência que não existe.
2. **Nada aqui sobrescreve.** Toda mudança econômica vira **versão** de `CustoReferencia`
   (`custo_service.registrar_referencia`), com fonte obrigatória; a anterior fecha vigência e
   continua consultável. Cotação emitida não muda: ela guarda o próprio snapshot.
3. **Nada aqui apaga blocker.** `A_COTAR` e `REVALIDAR` são rebaixamentos explícitos e
   auditados; o caminho para `CONFIRMADO` passa por evidência.

O status que a tela mostra é o **canônico vivo** (`pricing_service.status_canonico_do_custo`),
o mesmo que a cotação congela — nunca a coluna-cache `Produto.status_custo`.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from sqlmodel import Session, select

from app import admin_service as adm
from app import custo_service as cs
from app import pricing_service as ps
from app.dinheiro import D, para_float
from app.models import CostMethod, Fornecedor, Produto, StatusCusto, TipoFornecedor, Usuario

ORIGEM = "admin:governanca-produtos"

#: Por que este SKU não está pronto — em frase, para quem vai resolver. A chave é a premissa
#: que o motor declarou faltando (`memoria["premissas_faltantes"]`) ou a fonte do custo.
MOTIVO_PENDENCIA = {
    "peso": ("Falta o peso da peça. Sem ele o frete internacional entraria como zero, então o "
             "custo sairia subestimado. Registre o peso informado pela KTC (ou o estimado, "
             "declarando que é estimativa)."),
    "protecao_comercial": ("A família não tem regra de proteção comercial de precificação "
                           "cadastrada (ParametroKTC `protecao_comercial_pct`). Sem ela o preço "
                           "comercial não se forma."),
}
MOTIVO_FONTE = {
    ps.CUSTO_DO_CATALOGO: ("Não há EXW conhecido para este SKU: o custo que aparece é o número "
                           "herdado do catálogo, sem documento nem data. Registre a cotação da "
                           "KTC (EXW, data e documento) para o motor recalcular."),
    ps.CUSTO_HISTORICO_SEM_EVIDENCIA: ("O EXW vem do preço KTC histórico da planilha, sem data "
                                       "nem documento. Registre a cotação atual."),
}


@dataclass
class LinhaGovernanca:
    """Uma linha da tela: o que o SKU é, o que o motor resolve e o que falta para liberar."""
    produto: Produto
    fornecedor: Optional[str]
    status: str                      # canônico vivo
    situacao: str                    # DISPONIVEL | REVISAR | SOB_CONSULTA
    custo: Optional[float]
    net_fonte: Optional[str]
    exw_usd: Optional[float]
    exw_data: Optional[date]
    exw_fonte: Optional[str]
    exw_frescor: Optional[str]
    peso_kg: Optional[float]
    peso_tipo: Optional[str]
    peso_fonte: Optional[str]           # de onde veio o peso, quando não é do próprio SKU
    peso_origem: Optional[str]          # "ANALOGIA_HISTORICA" quando herdado de outro SKU
    premissas_faltantes: List[str]
    referencia: Optional[object]
    pendencias: List[str]
    pode_confirmar: bool
    importado: bool

    @property
    def peso_situacao(self) -> str:
        """REAL · ESTIMADO · AUSENTE — o que a tela mostra sem o operador ter de interpretar."""
        if not self.peso_kg:
            return "AUSENTE"
        return "REAL" if (self.peso_tipo or "").upper().startswith("REAL") else "ESTIMADO"

    def como_dict(self) -> dict:
        ref = self.referencia
        return {
            "produto_id": self.produto.id, "sku": self.produto.sku_key, "nome": self.produto.nome,
            "familia": self.produto.familia, "fornecedor": self.fornecedor,
            "cost_method": self.produto.cost_method, "status": self.status,
            "situacao": self.situacao, "custo": self.custo, "net_fonte": self.net_fonte,
            "exw_usd": self.exw_usd, "exw_data": str(self.exw_data) if self.exw_data else None,
            "exw_fonte": self.exw_fonte, "exw_frescor": self.exw_frescor,
            "peso_kg": self.peso_kg, "peso_tipo": self.peso_tipo,
            "peso_fonte": self.peso_fonte, "peso_origem": self.peso_origem,
            "peso_situacao": self.peso_situacao,
            "premissas_faltantes": self.premissas_faltantes, "pendencias": self.pendencias,
            "pode_confirmar": self.pode_confirmar, "importado": self.importado,
            "preco_base": self.produto.preco_base,
            "referencia": ({"versao": ref.versao, "status": ref.status_custo,
                            "valor": ref.cnet_brl, "tipo": ref.tipo,
                            "documento": ref.documento, "data": str(ref.valid_from)}
                           if ref else None),
        }


def diagnosticar(session: Session, produto: Produto) -> LinhaGovernanca:
    """O retrato de um SKU: status do motor, evidência vigente e o que falta para liberar."""
    from app.routers.produtos import situacao_comercial
    custo, memoria = ps.custo_para_precificar(session, produto)
    # o mesmo status que a cotação vai congelar: referência vigente manda, senão o motor
    status = ps.status_do_produto(session, produto, custo, memoria)
    faltantes = list(memoria.get("premissas_faltantes") or [])
    pendencias = [MOTIVO_PENDENCIA[p] for p in faltantes if p in MOTIVO_PENDENCIA]
    if status == StatusCusto.review_required.value and not faltantes:
        motivo = MOTIVO_FONTE.get(memoria.get("net_fonte"))
        if motivo:
            pendencias.append(motivo)
    if status == StatusCusto.estimado.value and (memoria.get("peso") or {}).get("origem") == "ANALOGIA_HISTORICA":
        origem = (memoria.get("peso") or {}).get("sku_origem") or "outro SKU da mesma família"
        pendencias.append(
            "Peso logístico estimado para nacionalização — recuperado de «" + origem + "», "
            "mesmo modelo, tamanho, gramatura e composição. O EXW é cotado e documentado, "
            "então o produto forma preço e sai em proposta; confirme o peso com a KTC antes "
            "do pedido/importação.")
    if status == StatusCusto.a_cotar.value:
        pendencias.append("Não há custo nem EXW para este SKU. Registre a cotação do fornecedor "
                          "(EXW em US$ para a KTC, custo em R$ para fornecedor nacional).")
    if status == StatusCusto.revalidar.value:
        pendencias.append("A referência é direta, mas envelheceu ou tem pedido de revisão aberto. "
                          "Confirme a referência atual ou registre a cotação nova.")
    fornecedor = ps.fornecedor_do_produto(session, produto)
    return LinhaGovernanca(
        produto=produto, fornecedor=fornecedor.codigo if fornecedor else None,
        status=status, situacao=situacao_comercial(produto, session), custo=custo,
        net_fonte=memoria.get("net_fonte"), exw_usd=memoria.get("exw_usd"),
        exw_data=produto.exw_cotado_data, exw_fonte=produto.exw_cotado_fonte,
        exw_frescor=memoria.get("exw_frescor"),
        peso_kg=produto.peso_kg or (memoria.get("peso") or {}).get("peso_kg"),
        peso_tipo=produto.peso_tipo or (memoria.get("peso") or {}).get("tipo"),
        peso_fonte=(produto.peso_fonte if produto.peso_kg
                    else (memoria.get("peso") or {}).get("fonte")),
        peso_origem=(None if produto.peso_kg else (memoria.get("peso") or {}).get("origem")),
        premissas_faltantes=faltantes,
        referencia=cs.referencia_vigente(session, produto.id),
        pendencias=pendencias,
        # confirmar só faz sentido quando existe evidência E o motor não aponta premissa
        pode_confirmar=(not faltantes and status in (StatusCusto.confirmado.value,
                                                     StatusCusto.revalidar.value,
                                                     StatusCusto.estimado.value)),
        importado=bool(fornecedor and fornecedor.tipo == TipoFornecedor.importado_ktc),
    )


def listar(session: Session, *, q: str = "", fornecedor: str = "", familia: str = "",
           status: str = "", limite: int = 400) -> List[LinhaGovernanca]:
    """Catálogo ativo com o diagnóstico de cada SKU, filtrado como a tela pede.

    A varredura inteira roda dentro de `ps.cache_de_leitura()`: diagnosticar 380 SKUs relia as
    mesmas tabelinhas de configuração 380 vezes e segurava a conexão por segundos (ver a nota
    em `pricing_service.cache_de_leitura`). É caminho de leitura pura — nada aqui grava.
    """
    produtos = list(session.exec(select(Produto).where(Produto.ativo == True)   # noqa: E712
                                 .order_by(Produto.familia, Produto.nome)).all())
    # o que dá para filtrar sem diagnosticar, filtra antes — `limite` corta depois do filtro,
    # como sempre cortou, senão uma busca no catálogo grande devolveria menos do que devia
    if q or fornecedor or familia:
        fornecedores = {f.id: f.codigo for f in session.exec(select(Fornecedor)).all()}
        produtos = [p for p in produtos
                    if _casa_texto(p, q) and _casa_familia(p, familia)
                    and (not fornecedor or fornecedores.get(p.fornecedor_id) == fornecedor)]
    with ps.cache_de_leitura(session):
        linhas = [diagnosticar(session, p) for p in produtos[:limite]]
    return filtrar(linhas, status=status)


def _casa_texto(produto: Produto, q: str) -> bool:
    if not q:
        return True
    alvo = q.strip().lower()
    return (alvo in (produto.sku_key or "").lower() or alvo in (produto.nome or "").lower()
            or alvo in (produto.exw_cotado_fonte or "").lower())


def _casa_familia(produto: Produto, familia: str) -> bool:
    return not familia or (produto.familia or "") == familia


def filtrar(linhas: List[LinhaGovernanca], *, q: str = "", fornecedor: str = "",
            familia: str = "", status: str = "") -> List[LinhaGovernanca]:
    """Os filtros da tela aplicados sobre linhas **já diagnosticadas**.

    Separado de `listar` para que a tela conte os status do catálogo inteiro e mostre as linhas
    filtradas com uma única varredura — antes eram duas, e a segunda dobrava o custo da página.
    """
    linhas = [l for l in linhas
              if _casa_texto(l.produto, q) and _casa_familia(l.produto, familia)
              and (not fornecedor or l.fornecedor == fornecedor)]
    if status:
        alvo = status.upper()
        if alvo == "SEM_CUSTO":
            linhas = [l for l in linhas if not l.custo]
        elif alvo == "PRECO_DISPONIVEL":
            linhas = [l for l in linhas if l.situacao == "DISPONIVEL"]
        else:
            linhas = [l for l in linhas if l.status == alvo]
    return linhas


# ---------------------------------------------------------------------------
# Ações — todas versionadas e auditadas; nenhuma apaga blocker
# ---------------------------------------------------------------------------
class AcaoInvalida(ValueError):
    """O que foi pedido não se sustenta: falta evidência, falta motivo, ou o dado não serve."""


def _exigir(condicao, mensagem):
    if not condicao:
        raise AcaoInvalida(mensagem)


def _auditar(session, ator, acao, produto, antes, depois, motivo, detalhe=None):
    adm.registrar(session, ator=ator, acao=acao, entidade="Produto", entidade_id=produto.id,
                  escopo=f"SKU {produto.sku_key}", antes=antes, depois=depois, motivo=motivo,
                  origem=ORIGEM, detalhe=detalhe)


def _cnet_vivo(session: Session, produto: Produto):
    custo, memoria = ps.custo_para_precificar(session, produto)
    return custo, memoria


def registrar_peso(session: Session, produto: Produto, *, peso_kg, tipo: str, fonte: str,
                   documento: Optional[str], data_ref: Optional[date], motivo: str,
                   ator: Usuario) -> dict:
    """Grava o peso da peça — a premissa que mais trava SKU importado.

    `tipo` distingue o que a KTC informou (`REAL KTC`) do que a Anara estimou (`ESTIMADO`), e a
    diferença fica no cadastro: estimativa nunca vira "peso informado pela fábrica".
    """
    _exigir(fonte and fonte.strip(), "Peso exige fonte — de onde veio o número.")
    _exigir(motivo and motivo.strip(), "Registrar peso exige motivo.")
    _exigir(tipo in ("REAL KTC", "ESTIMADO"), "Tipo de peso inválido.")
    peso = D(peso_kg)
    _exigir(peso is not None and peso > 0, "O peso precisa ser um número positivo em kg.")
    antes = f"peso {produto.peso_kg} ({produto.peso_tipo or 'sem tipo'})"
    produto.peso_kg = para_float(peso)
    produto.peso_tipo = tipo
    produto.peso_fonte = fonte
    produto.peso_data = data_ref or date.today()
    produto.peso_documento = documento
    session.add(produto)
    session.flush()
    custo, memoria = _cnet_vivo(session, produto)
    _auditar(session, ator, "REGISTRAR_PESO", produto, antes,
             f"peso {para_float(peso)} kg ({tipo})", motivo,
             {"fonte": fonte, "documento": documento,
              "status_depois": ps.status_do_produto(session, produto, custo, memoria)})
    return {"peso_kg": para_float(peso), "tipo": tipo, "custo": custo,
            "status": ps.status_do_produto(session, produto, custo, memoria)}


def registrar_exw_ktc(session: Session, produto: Produto, *, exw_usd, data_ref: date,
                      documento: str, fonte: str, motivo: str, ator: Usuario,
                      peso_kg=None, peso_tipo: str = "REAL KTC",
                      status: str = StatusCusto.confirmado.value,
                      observacao: Optional[str] = None) -> dict:
    """Nova cotação da KTC: EXW em US$ com data e documento, e o peso quando vier junto.

    O CNET **não** é digitado: sai do motor de nacionalização de sempre (EXW + frete + I.I.
    econômico 0% + outras despesas, × câmbio), e é ele que vai para a versão da referência.
    """
    _exigir(produto.fornecedor_id, "SKU sem fornecedor não recebe cotação KTC.")
    _exigir(documento and documento.strip(), "Cotação KTC exige documento.")
    _exigir(fonte and fonte.strip(), "Cotação KTC exige fonte.")
    _exigir(data_ref is not None, "Cotação KTC exige data de referência.")
    _exigir(motivo and motivo.strip(), "Registrar cotação exige motivo.")
    _exigir(status in {s.value for s in StatusCusto}, "Status inválido.")
    exw = D(exw_usd)
    _exigir(exw is not None and exw > 0, "O EXW precisa ser positivo, em US$.")
    antes = (f"EXW {produto.exw_cotado_usd} ({produto.exw_cotado_data}) · "
             f"{produto.exw_cotado_fonte or 'sem fonte'}")
    produto.exw_cotado_usd = para_float(exw)
    produto.exw_cotado_data = data_ref
    produto.exw_cotado_fonte = fonte
    produto.custo_ref_documento = documento
    produto.custo_ref_data = data_ref
    if produto.cost_method not in (CostMethod.ktc_calculated.value,):
        produto.cost_method = CostMethod.ktc_quoted.value
    session.add(produto)
    session.flush()
    if peso_kg not in (None, ""):
        registrar_peso(session, produto, peso_kg=peso_kg, tipo=peso_tipo, fonte=fonte,
                       documento=documento, data_ref=data_ref,
                       motivo=f"peso informado junto com a cotação: {motivo}", ator=ator)
    custo, memoria = _cnet_vivo(session, produto)
    _exigir(custo, "O motor não formou custo com esta cotação — confira EXW, peso e premissas.")
    # a referência não pode afirmar mais do que a evidência sustenta: com premissa faltando
    # (tipicamente o peso), a versão nasce em REVIEW_REQUIRED, com o motivo na memória
    faltantes = memoria.get("premissas_faltantes") or []
    if faltantes and status == StatusCusto.confirmado.value:
        status = StatusCusto.review_required.value
    ref = cs.registrar_referencia(
        session, produto, cnet_brl=para_float(custo), metodo=produto.cost_method,
        status=status, fonte=fonte, documento=documento, data_ref=data_ref,
        valor_bruto=para_float(exw), moeda="USD",
        memoria={"exw_usd": para_float(exw), "caminho": "EXW cotado → nacionalização → CNET",
                 "nacionalizacao": memoria.get("nacionalizacao"),
                 "premissas_faltantes": memoria.get("premissas_faltantes")},
        origem_registro=ORIGEM, notas=observacao)
    session.flush()
    _auditar(session, ator, "REGISTRAR_COTACAO_KTC", produto, antes,
             f"EXW US$ {para_float(exw)} em {data_ref} · {documento} → CNET {para_float(custo)}",
             motivo, {"versao": ref.versao, "status": status})
    return {"versao": ref.versao, "cnet": para_float(custo), "status": status,
            "premissas_faltantes": list(faltantes),
            "status_vivo": ps.status_do_produto(session, produto, custo, memoria)}


def registrar_custo_nacional(session: Session, produto: Produto, *, valor, base: str,
                             data_ref: date, documento: Optional[str], fonte: str, motivo: str,
                             ator: Usuario, status: str = StatusCusto.confirmado.value,
                             observacao: Optional[str] = None) -> dict:
    """Custo de compra de fornecedor nacional. `base` = `bruto` (com créditos) ou `net`.

    `bruto` passa pela regra de créditos do fornecedor (Daune: ICMS + PIS/COFINS; Decor: só
    PIS/COFINS) — a mesma de `custo_service`, sem segunda fórmula. `net` é o custo líquido já
    apurado, para o fornecedor que entrega o número pronto (ELIS).
    """
    _exigir(fonte and fonte.strip(), "Custo exige fonte.")
    _exigir(motivo and motivo.strip(), "Registrar custo exige motivo.")
    _exigir(base in ("bruto", "net"), "Base do custo inválida.")
    _exigir(status in {s.value for s in StatusCusto}, "Status inválido.")
    quantia = D(valor)
    _exigir(quantia is not None and quantia > 0, "O custo precisa ser positivo, em R$.")
    fornecedor = ps.fornecedor_do_produto(session, produto)
    _exigir(fornecedor and fornecedor.tipo == TipoFornecedor.nacional,
            "Este caminho é para fornecedor nacional; para a KTC, registre a cotação em US$.")
    antes = f"CNET {produto.custo_unitario} · {produto.custo_ref_documento or 'sem documento'}"
    if base == "bruto" and (fornecedor.codigo or "").upper() == "DECOR_TRICOT":
        ref = cs.registrar_decor(session, produto, para_float(quantia), fonte=fonte,
                                 documento=documento, data_ref=data_ref, status=status,
                                 origem_registro=ORIGEM, notas=observacao)
    elif base == "bruto":
        ref = cs.registrar_daune(session, produto, para_float(quantia), fonte=fonte,
                                 documento=documento, data_ref=data_ref, status=status,
                                 origem_registro=ORIGEM, notas=observacao)
    else:
        ref = cs.registrar_referencia(
            session, produto, cnet_brl=para_float(quantia),
            metodo=produto.cost_method or CostMethod.national_supplier.value, status=status,
            fonte=fonte, documento=documento, data_ref=data_ref, origem_registro=ORIGEM,
            notas=observacao, memoria={"caminho": "custo NET informado pelo fornecedor"})
    session.flush()
    custo, memoria = _cnet_vivo(session, produto)
    _auditar(session, ator, "REGISTRAR_CUSTO_NACIONAL", produto, antes,
             f"{base} R$ {para_float(quantia)} em {data_ref} → CNET {para_float(custo)}", motivo,
             {"versao": ref.versao, "status": status, "documento": documento})
    return {"versao": ref.versao, "cnet": custo, "status": status,
            "status_vivo": ps.status_do_produto(session, produto, custo, memoria)}


def confirmar_referencia(session: Session, produto: Produto, *, fonte: str, motivo: str,
                         ator: Usuario, documento: Optional[str] = None) -> dict:
    """Reconfirma a evidência que já existe — sem redigitar o número.

    Vira **versão nova** (reconfirmação não é no-op: "conferi hoje e continua valendo" é
    informação econômica). Recusa quando o motor aponta premissa faltando: confirmar aí seria
    afirmar uma evidência inexistente e o blocker voltaria na próxima precificação.
    """
    _exigir(fonte and fonte.strip(), "Confirmar exige declarar a fonte conferida.")
    _exigir(motivo and motivo.strip(), "Confirmar exige motivo.")
    linha = diagnosticar(session, produto)
    _exigir(not linha.premissas_faltantes,
            "Não dá para confirmar: " + " ".join(linha.pendencias))
    # Peso herdado de um SKU análogo não é evidência DESTE SKU. Confirmar aqui gravaria
    # CONFIRMADO por cima de um custo que depende de premissa emprestada — e, como a
    # referência vigente tem precedência em `status_do_produto`, o aviso "peso estimado"
    # sumiria da tela. É o "ESTIMADO promovido a CONFIRMADO em silêncio" que o CLAUDE.md
    # proíbe. Restrição **administrativa** apenas: o SKU continua cotável, entra em proposta
    # e gera PDF normalmente — só não vira evidência confirmada sem peso próprio.
    _exigir(linha.peso_origem != "ANALOGIA_HISTORICA",
            "Este produto usa peso logístico estimado por analogia"
            + (f" (de «{(linha.peso_fonte or '').split('«')[-1].split('»')[0]}»)"
               if "«" in (linha.peso_fonte or "") else "")
            + ". Registre o peso próprio/documentado do SKU antes de confirmar a referência — "
              "o produto segue cotável e pode gerar proposta enquanto isso.")
    _exigir(linha.custo, "Não há custo para confirmar — registre a cotação ou o custo primeiro.")
    _exigir(linha.net_fonte not in (ps.CUSTO_DO_CATALOGO, ps.CUSTO_HISTORICO_SEM_EVIDENCIA),
            "O custo atual não tem evidência (veio do catálogo/planilha). Registre a cotação.")
    vigente = linha.referencia
    ref = cs.registrar_referencia(
        session, produto, cnet_brl=para_float(linha.custo),
        metodo=(vigente.metodo_custo if vigente else produto.cost_method
                or CostMethod.ktc_quoted.value),
        status=StatusCusto.confirmado.value, fonte=fonte,
        documento=documento or (vigente.documento if vigente else produto.custo_ref_documento),
        data_ref=date.today(),
        valor_bruto=(vigente.valor_bruto if vigente else None),
        moeda=(vigente.moeda if vigente else "BRL"),
        memoria={"reconfirmacao": True, "status_vivo_no_momento": linha.status},
        origem_registro=ORIGEM, notas=motivo)
    session.flush()
    produto.precisa_revisao = False
    produto.revisao_motivo = None
    session.add(produto)
    _auditar(session, ator, "CONFIRMAR_REFERENCIA", produto,
             f"{linha.status} · {'V' + str(vigente.versao) if vigente else 'sem versão'}",
             f"CONFIRMADO · V{ref.versao}", motivo, {"fonte": fonte})
    return {"versao": ref.versao, "status": StatusCusto.confirmado.value, "cnet": linha.custo}


def marcar_status(session: Session, produto: Produto, *, status: str, motivo: str,
                  fonte: str, ator: Usuario) -> dict:
    """Rebaixa explicitamente: `REVALIDAR` (reconferir antes de fechar) ou `A_COTAR` (sem base).

    É o oposto de apagar blocker — cria uma versão dizendo, com autor e motivo, que o número
    não sustenta compromisso. `A_COTAR` limpa o cache de custo do catálogo.
    """
    _exigir(status in (StatusCusto.revalidar.value, StatusCusto.a_cotar.value),
            "Esta ação só rebaixa para REVALIDAR ou A_COTAR.")
    _exigir(motivo and motivo.strip(), "Rebaixar status exige motivo.")
    linha = diagnosticar(session, produto)
    ref = cs.registrar_referencia(
        session, produto, cnet_brl=para_float(linha.custo or 0),
        metodo=(linha.referencia.metodo_custo if linha.referencia
                else produto.cost_method or CostMethod.manual.value),
        status=status, fonte=fonte or f"decisão administrativa de {date.today():%d/%m/%Y}",
        documento=None, data_ref=date.today(), origem_registro=ORIGEM, notas=motivo,
        memoria={"decisao_administrativa": status, "status_anterior": linha.status})
    if status == StatusCusto.a_cotar.value:
        produto.custo_unitario = None
        produto.preco_base = None
    produto.precisa_revisao = status == StatusCusto.revalidar.value
    produto.revisao_motivo = motivo if status == StatusCusto.revalidar.value else None
    session.add(produto)
    session.flush()
    _auditar(session, ator, "MARCAR_STATUS_CUSTO", produto, linha.status,
             f"{status} · V{ref.versao}", motivo)
    return {"versao": ref.versao, "status": status}
