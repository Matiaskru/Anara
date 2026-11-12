"""Itens da cotação KTC ANARA20102026 (20/10/2026) — compatibilidade com os scripts desta pasta.

Desde 12/11/2026 a fonte e a montagem do produto moram no sistema (`app/catalogo_tabela.py`, dados em
`app/ktc_20_10_2026_itens.json`), porque o Admin cadastra os itens novos por lá. Este módulo só reexporta.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.catalogo_tabela import carregar, fonte, material_por_nome, produto_novo  # noqa: E402,F401
