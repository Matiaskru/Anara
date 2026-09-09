"""Tradução dos códigos internos para a língua do usuário.

O sistema tem um vocabulário técnico que existe por bons motivos: `A_COTAR` é diferente de
`REVIEW_REQUIRED`, e `FRETE_ICMS_REVIEW_REQUIRED` diz exatamente qual das três pendências de
frete travou o documento. Esse vocabulário precisa continuar no banco, no log, na trilha de
auditoria e na memória do preço — é lá que a precisão vale mais que a leitura.

O que ele **não** pode fazer é aparecer como texto do produto. Uma vendedora que lê
"REVIEW_REQUIRED" numa tela não aprende nada; ela aprende com "Revisão necessária" seguido
do motivo. Este módulo é a fronteira entre os dois mundos, e é o único lugar onde a
tradução mora — espalhar `if status == "A_COTAR"` pelos templates traria de volta o problema
com outro nome.

Código sem tradução cadastrada volta **como veio**, e isso é deliberado: inventar um texto
bonito para um código desconhecido esconderia justamente o caso que precisa de atenção.
"""
from typing import Optional

# ---------------------------------------------------------------------------
# Custo
# ---------------------------------------------------------------------------
STATUS_CUSTO = {
    "CONFIRMADO": "Custo confirmado",
    "ESTIMADO": "Custo estimado",
    "REVALIDAR": "Custo a revalidar",
    "A_COTAR": "Preço sob consulta",
    "REVIEW_REQUIRED": "Revisão necessária",
    # Vocabulário de `CostConfidence`, que também chega neste campo em registros antigos.
    "CALCULATED": "Custo calculado",
    "QUOTED": "Custo cotado",
    "ESTIMATED": "Custo estimado",
    "MANUAL": "Custo informado à mão",
    "LEGACY": "Custo herdado",
}

#: O que cada estado significa na prática, para a tela poder explicar sem obrigar o usuário
#: a decorar a tabela.
EXPLICACAO_CUSTO = {
    "CONFIRMADO": "Referência direta, atual e confiável.",
    "ESTIMADO": "Formado por comparação com itens semelhantes. Serve para propor, "
                "não para assumir compromisso.",
    "REVALIDAR": "O número é direto, mas envelheceu. Reconfirme antes de fechar.",
    "A_COTAR": "Ainda não há base de custo para este item. É preciso cotar com o fornecedor.",
    "REVIEW_REQUIRED": "Há uma premissa deste item que precisa ser resolvida.",
}

# ---------------------------------------------------------------------------
# Fiscal e pagamento
# ---------------------------------------------------------------------------
STATUS_FISCAL = {
    "OK": "Resolvido",
    "REVIEW_REQUIRED": "Cenário fiscal a resolver",
}

STATUS_PAGAMENTO = {
    "OK": "Resolvida",
    "REVIEW_REQUIRED": "Condição de pagamento a resolver",
}

# ---------------------------------------------------------------------------
# Frete
# ---------------------------------------------------------------------------
STATUS_FRETE = {
    "OK": "Frete calculado",
    "FRETE_ESTIMADO": "Frete estimado",
    "FRETE_A_COTAR": "Frete a cotar",
    "FRETE_REVIEW_REQUIRED": "Frete pendente de revisão",
    "FRETE_ICMS_REVIEW_REQUIRED": "Frete pendente de validação fiscal",
}

# ---------------------------------------------------------------------------
# Workflow da cotação
# ---------------------------------------------------------------------------
STATUS_COTACAO = {
    "rascunho": "Rascunho",
    "aguardando_aprovacao": "Aguardando aprovação",
    "aprovada": "Aprovada",
    "emitida": "Emitida",
    "enviada": "Enviada ao cliente",
    "cancelada": "Cancelada",
    # Estados herdados do sistema anterior. Aparecem só em cotações antigas.
    "fechada": "Fechada (registro antigo)",
    "pedido": "Pedido (registro antigo)",
    "perdida": "Perdida (registro antigo)",
}

#: O verbo da ação que leva a cada estado — o botão diz o que vai acontecer, não o nome do
#: estado de destino.
ACAO_PARA_ESTADO = {
    "rascunho": "Voltar para rascunho",
    "aguardando_aprovacao": "Solicitar aprovação",
    "aprovada": "Aprovar",
    "emitida": "Emitir",
    "enviada": "Marcar como enviada",
    "cancelada": "Cancelar cotação",
}

# ---------------------------------------------------------------------------
# Blockers e exceções
# ---------------------------------------------------------------------------
BLOCKER = {
    "SEM_ITENS": "A cotação não tem nenhum produto.",
    "SEM_PRECO": "O item está sem preço.",
    "FISCAL_REVIEW_REQUIRED": "O cenário fiscal do item ainda não foi resolvido.",
    "PAGAMENTO_REVIEW_REQUIRED": "A condição de pagamento do item ainda não foi resolvida.",
    "CUSTO_A_COTAR": "O item está sob consulta — ainda não há custo para formar preço.",
    "CUSTO_REVIEW_REQUIRED": "O custo do item precisa de revisão.",
    "FRETE_A_COTAR": "O frete ainda precisa ser cotado.",
    "FRETE_REVIEW_REQUIRED": "O frete precisa de revisão.",
    "FRETE_ICMS_REVIEW_REQUIRED": "O frete está pendente de validação fiscal.",
    "FRETE_GRUPO": "Há uma pendência no frete deste embarque.",
}

EXCECAO = {
    "PRECO_ABAIXO_RECOMENDADO": "Preço abaixo do recomendado",
    "MARGEM_ABAIXO_ALVO": "Margem abaixo da meta",
    "PREMISSA_DESATUALIZADA_MANTIDA": "Esta cotação mantém premissas anteriores",
    "OUTRA_EXCECAO_COMERCIAL": "Exceção comercial",
}

# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------
ETAPA = {
    "PROSPECCAO": "Prospecção",
    "CONTATO": "Contato",
    "QUALIFICACAO": "Qualificação",
    "COTACAO": "Cotação",
    "NEGOCIACAO": "Negociação",
    "DECISAO": "Decisão",
}

STATUS_OPORTUNIDADE = {"ABERTA": "Aberta", "GANHA": "Ganha", "PERDIDA": "Perdida"}

MOTIVO_PERDA = {
    "PRECO": "Preço", "PRAZO": "Prazo", "CONCORRENTE": "Concorrente",
    "SEM_RETORNO": "Sem retorno", "PROJETO_CANCELADO": "Projeto cancelado",
    "FORA_DE_ESCOPO": "Fora de escopo", "OUTRO": "Outro",
}

ORIGEM_OPORTUNIDADE = {
    "INBOUND": "Veio até nós", "OUTBOUND": "Prospecção ativa", "INDICACAO": "Indicação",
    "EVENTO": "Evento", "PARCERIA": "Parceria", "CARTEIRA": "Carteira", "OUTRO": "Outro",
}

TIPO_ATIVIDADE = {
    "LIGACAO": "Ligação", "EMAIL": "E-mail", "REUNIAO": "Reunião",
    "FOLLOW_UP": "Follow-up", "OUTRO": "Outro",
}

# ---------------------------------------------------------------------------
# Papéis
# ---------------------------------------------------------------------------
PAPEL = {
    "OWNER": "Dono",
    "ADMIN": "Administrador",
    "VENDEDOR_INTERNO": "Vendedor interno",
    "VENDEDOR_COMISSIONADO": "Vendedor comissionado",
}

# ---------------------------------------------------------------------------
# Erros de sistema que o usuário pode encontrar
# ---------------------------------------------------------------------------
ERRO = {
    "AprovacaoVencida": ("A cotação mudou desde que esta aprovação foi aberta. "
                         "Solicite uma nova aprovação."),
    "TransicaoInvalida": "Esta cotação não pode ir para essa situação a partir de onde está.",
    "ConflitoDeVersao": ("Alguém alterou este cadastro enquanto você preenchia. "
                         "Abra de novo para ver os valores atuais."),
    "DadoInvalido": "Há um dado inválido no formulário.",
}


# ---------------------------------------------------------------------------
# Tradutores
# ---------------------------------------------------------------------------
def _traduz(tabela: dict, codigo: Optional[str]) -> str:
    """Traduz, ou devolve o código como veio.

    Devolver o código cru para o que não está cadastrado é de propósito: um rótulo genérico
    do tipo "Situação desconhecida" apagaria a pista de que apareceu um valor que ninguém
    previu.
    """
    if not codigo:
        return ""
    return tabela.get(str(codigo).strip(), str(codigo))


def custo(codigo) -> str:
    return _traduz(STATUS_CUSTO, codigo)


def explicacao_custo(codigo) -> str:
    return EXPLICACAO_CUSTO.get(str(codigo or "").strip(), "")


def fiscal(codigo) -> str:
    return _traduz(STATUS_FISCAL, codigo)


def pagamento(codigo) -> str:
    return _traduz(STATUS_PAGAMENTO, codigo)


def frete(codigo) -> str:
    return _traduz(STATUS_FRETE, codigo)


def cotacao(codigo) -> str:
    if hasattr(codigo, "value"):
        codigo = codigo.value
    return _traduz(STATUS_COTACAO, codigo)


def acao(codigo) -> str:
    return _traduz(ACAO_PARA_ESTADO, codigo)


def blocker(codigo) -> str:
    return _traduz(BLOCKER, codigo)


def excecao(codigo) -> str:
    return _traduz(EXCECAO, codigo)


def etapa(codigo) -> str:
    return _traduz(ETAPA, codigo)


def oportunidade(codigo) -> str:
    return _traduz(STATUS_OPORTUNIDADE, codigo)


def motivo_perda(codigo) -> str:
    return _traduz(MOTIVO_PERDA, codigo)


def origem(codigo) -> str:
    return _traduz(ORIGEM_OPORTUNIDADE, codigo)


def atividade(codigo) -> str:
    return _traduz(TIPO_ATIVIDADE, codigo)


def papel(codigo) -> str:
    return _traduz(PAPEL, codigo)


def erro(excecao_ou_codigo) -> str:
    """Mensagem humana para uma exceção conhecida, ou o texto dela."""
    nome = type(excecao_ou_codigo).__name__ if isinstance(excecao_ou_codigo, Exception) \
        else str(excecao_ou_codigo)
    if nome in ERRO:
        return ERRO[nome]
    return str(excecao_ou_codigo)


def humanizar_blocker(b) -> dict:
    """Um blocker do workflow em linguagem de gente, com o escopo preservado.

    O `detalhe` técnico continua disponível para a área de saúde e para a trilha; o que a
    tela comercial mostra é a frase, e o item a que ela se refere.
    """
    codigo = b.get("codigo") if isinstance(b, dict) else getattr(b, "codigo", "")
    escopo = b.get("escopo") if isinstance(b, dict) else getattr(b, "escopo", "")
    detalhe = b.get("detalhe") if isinstance(b, dict) else getattr(b, "detalhe", "")
    return {"codigo": codigo, "escopo": escopo,
            "texto": blocker(codigo) or detalhe, "detalhe": detalhe}
