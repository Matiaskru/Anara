"""A linguagem do produto não é a linguagem do código.

O sistema tem vocabulário técnico por bons motivos — `A_COTAR` é diferente de
`REVIEW_REQUIRED`, e `fingerprint` é o nome exato do que aquele hash é. Esse vocabulário
precisa continuar no banco, no log, na trilha e na memória do preço, onde precisão vale mais
que leitura.

O que ele não pode fazer é aparecer como texto do produto. Quem vende não aprende nada
lendo `FRETE_ICMS_REVIEW_REQUIRED`; aprende com "Frete pendente de validação fiscal".

Estes testes são canários: varrem os templates comerciais atrás dos termos que já vazaram
uma vez. A **área técnica** — Saúde e Admin — pode mostrar código, desde que a descrição
humana venha antes.
"""
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(RAIZ, "app", "templates")

#: Telas que uma vendedora abre no dia a dia. Aqui não entra código interno.
TELAS_COMERCIAIS = [
    "dashboard.html", "crm_home.html", "crm_pipeline.html", "crm_lista.html",
    "crm_oportunidade.html", "vendas_list.html", "venda_detail.html", "clientes_list.html", "cliente_detail.html",
    "cotacoes_list.html", "cotacao_nova.html", "cotacao_detail.html", "_cotacao_situacao.html",
    "produtos_list.html", "relatorios.html", "relatorio_cotacoes.html",
    "relatorio_aprovacoes.html", "login.html", "primeiro_acesso.html",
    "erro_acao.html", "base.html",
]

#: Termos que não podem aparecer como **texto** numa tela comercial.
PROIBIDOS = [
    "REVIEW_REQUIRED", "FRETE_A_COTAR", "FRETE_REVIEW_REQUIRED",
    "FRETE_ICMS_REVIEW_REQUIRED", "PREMISSA_DESATUALIZADA_MANTIDA", "AprovacaoVencida",
    "TransicaoInvalida", "ConflitoDeVersao", "fingerprint", "pinning",
    "Claude", "por decisão explícita",
    "Sessão 1", "Sessão 2", "Sessão 3", "Sessão 4",
    "Sessão 5", "Sessão 6", "Sessão 7", "Sessão 8",
]

#: Onde o código técnico é bem-vindo — desde que acompanhado de descrição humana.
TELAS_TECNICAS = ["saude.html", "admin_sku.html", "admin_trilha.html",
                  "admin_painel.html", "configuracoes.html", "relatorio_qualidade.html",
                  "aprovacao_detalhe.html", "relatorio_economico.html",
                  "admin_hub.html", "admin_usuarios.html", "importar.html",
                  "importar_preview.html", "calculadora.html", "aprovacoes_fila.html",
                  "403.html"]


def _texto_visivel(html: str) -> str:
    """O que a pessoa lê: sem comentários Jinja, sem `<script>`, sem `<style>`."""
    html = re.sub(r"\{#.*?#\}", " ", html, flags=re.S)
    html = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b.*?</style>", " ", html, flags=re.S | re.I)
    return html


def _todas_as_telas():
    return sorted(f for f in os.listdir(TEMPLATES) if f.endswith(".html"))


def test_a_lista_de_telas_cobre_o_diretorio():
    """Template novo entra numa das duas listas — não some da varredura por esquecimento."""
    conhecidas = set(TELAS_COMERCIAIS) | set(TELAS_TECNICAS) | {"_abas_relatorios.html"}
    faltando = set(_todas_as_telas()) - conhecidas
    assert not faltando, (
        f"telas fora da auditoria de copy: {sorted(faltando)} — "
        "classifique como comercial ou técnica")


@pytest.mark.parametrize("tela", TELAS_COMERCIAIS)
def test_tela_comercial_nao_mostra_termo_tecnico(tela):
    caminho = os.path.join(TEMPLATES, tela)
    if not os.path.exists(caminho):
        pytest.skip(f"{tela} não existe")
    visivel = _texto_visivel(open(caminho, encoding="utf-8").read())

    achados = [t for t in PROIBIDOS if t in visivel]
    assert achados == [], f"{tela} mostra termo técnico: {achados}"


@pytest.mark.parametrize("tela", TELAS_COMERCIAIS)
def test_tela_comercial_nao_mostra_enum_de_status_cru(tela):
    """`A_COTAR` e companhia passam pelo tradutor antes de virar texto."""
    caminho = os.path.join(TEMPLATES, tela)
    if not os.path.exists(caminho):
        pytest.skip(f"{tela} não existe")
    visivel = _texto_visivel(open(caminho, encoding="utf-8").read())
    # `value="A_COTAR"` é dado que volta para o servidor, não texto lido.
    sem_values = re.sub(r'(value|name|class|id|data-\w+)="[^"]*"', " ", visivel)

    for termo in ("A_COTAR", "REVALIDAR", "CONFIRMADO", "ESTIMADO"):
        assert termo not in sem_values, f"{tela} mostra '{termo}' cru"


def test_area_tecnica_mostra_descricao_antes_do_codigo():
    """Saúde pode citar o código — mas a frase em português vem primeiro."""
    html = open(os.path.join(TEMPLATES, "saude.html"), encoding="utf-8").read()
    assert "Cotações com item sob consulta" in html
    assert "Cotações com item aguardando revisão" in html
    # o código continua disponível, num `<code>` discreto ao lado
    assert "codigos" in html


# ---------------------------------------------------------------------------
# Navegação
# ---------------------------------------------------------------------------
def test_menu_e_por_tarefa_e_curto():
    html = open(os.path.join(TEMPLATES, "base.html"), encoding="utf-8").read()
    nav = html.split("<nav>")[1].split("</nav>")[0]
    destinos = re.findall(r'href="(/[^"]*)"', nav)

    # Fase 3C: Dashboard (só OWNER/ADMIN) → Vendas → Clientes → Cotações → Produtos, e depois
    # as portas por permissão (Aprovações, Admin). Relatórios saiu do menu principal: mora
    # dentro do Admin e do Dashboard. "Meu dia" e o quadro antigo continuam alcançáveis.
    assert destinos[:5] == ["/", "/vendas", "/clientes", "/cotacoes", "/produtos"]
    assert "/relatorios" not in destinos, "Relatórios voltou ao menu principal"
    assert len(destinos) <= 7, f"o menu voltou a crescer: {destinos}"


def test_menu_nao_tem_ferramenta_tecnica_solta():
    """Calculadora, importação, saúde, trilha e configurações moram dentro do Admin."""
    html = open(os.path.join(TEMPLATES, "base.html"), encoding="utf-8").read()
    nav = html.split("<nav>")[1].split("</nav>")[0]
    for destino in ("/calculadora", "/importar", "/saude", "/admin/trilha",
                    "/configuracoes", "/relatorios/qualidade", "/relatorios/economico"):
        assert f'href="{destino}"' not in nav, f"'{destino}' voltou ao menu principal"


def test_configuracoes_nao_e_destino_concorrente_do_admin():
    html = open(os.path.join(TEMPLATES, "base.html"), encoding="utf-8").read()
    assert 'href="/configuracoes"' not in html
    assert 'href="/admin"' in html


def test_relatorios_tem_um_grupo_so():
    abas = open(os.path.join(TEMPLATES, "_abas_relatorios.html"), encoding="utf-8").read()
    for destino in ("/relatorios", "/relatorios/economico", "/relatorios/cotacoes",
                    "/relatorios/aprovacoes"):
        assert destino in abas
    assert "/saude" not in abas, "saúde é diagnóstico técnico, não relatório comercial"


@pytest.mark.parametrize("tela", ["relatorios.html", "relatorio_economico.html",
                                  "relatorio_cotacoes.html", "relatorio_aprovacoes.html"])
def test_toda_tela_de_relatorio_mostra_as_abas(tela):
    html = open(os.path.join(TEMPLATES, tela), encoding="utf-8").read()
    assert "_abas_relatorios.html" in html, f"{tela} não oferece caminho para as irmãs"


# ---------------------------------------------------------------------------
# Erros como página
# ---------------------------------------------------------------------------
def test_erro_html_vira_pagina_e_nao_json():
    """O handler global renderiza HTML quando quem pediu foi um navegador."""
    import inspect
    from app import main

    corpo = inspect.getsource(main.tratar_http_exception)
    assert "pagina_de_erro" in corpo
    assert "aceita_html" in corpo


def test_pdf_bloqueado_devolve_pagina():
    import inspect
    from app.routers import cotacoes

    corpo = inspect.getsource(cotacoes.gerar_pdf)
    assert "pagina_de_erro" in corpo
    assert "JSONResponse" not in corpo, "o PDF voltou a recusar com JSON cru"
