"""
Lê o arquivo .xlsx baixado e sincroniza com um banco de dados do Notion.

Comportamento:
- Usa NOTION_PRIMARY_KEY (.env) como chave única para evitar duplicatas.
- Se a linha já existir no Notion → atualiza.
- Se não existir → cria nova página.
- Detecta automaticamente o tipo de cada coluna (data, número, texto).
"""

import os
import math
from pathlib import Path
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()

NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
DATABASE_ID: str = os.getenv("NOTION_DATABASE_ID", "")
PRIMARY_KEY: str = os.getenv("NOTION_PRIMARY_KEY", "Número OS")

notion = Client(auth=NOTION_TOKEN)


# ---------------------------------------------------------------------------
# Helpers de tipo Notion
# ---------------------------------------------------------------------------

def _safe_str(value) -> str:
    """Converte um valor para string segura (sem NaN/None)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _to_notion_property(value) -> dict:
    """
    Mapeia automaticamente um valor Python para a propriedade Notion correta.
    Suporta: datas (datetime), números (int/float) e texto (str).
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return {"rich_text": []}

    if isinstance(value, (datetime, pd.Timestamp)):
        iso = value.isoformat()
        return {"date": {"start": iso}}

    if isinstance(value, (int, float)) and not math.isnan(value):
        return {"number": float(value)}

    text = _safe_str(value)
    if not text:
        return {"rich_text": []}

    # Trunca para o limite do Notion (2000 chars)
    text = text[:2000]
    return {"rich_text": [{"text": {"content": text}}]}


def _title_property(value) -> dict:
    """Cria uma propriedade do tipo 'title' (obrigatória para a primeira coluna)."""
    text = _safe_str(value)[:2000]
    return {"title": [{"text": {"content": text}}]}


# ---------------------------------------------------------------------------
# Busca de registros existentes
# ---------------------------------------------------------------------------

def _fetch_existing_pages(primary_key_col: str) -> dict[str, str]:
    """
    Consulta o banco de dados do Notion e retorna um mapa
    { valor_da_chave_primária → page_id }.
    """
    existing: dict[str, str] = {}
    cursor = None

    while True:
        params: dict = {"database_id": DATABASE_ID, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        try:
            response = notion.databases.query(**params)
        except APIResponseError as e:
            print(f"[notion] Erro ao consultar banco de dados: {e}")
            break

        for page in response.get("results", []):
            props = page.get("properties", {})
            prop = props.get(primary_key_col)
            if not prop:
                continue

            # Extrai valor dependendo do tipo
            p_type = prop.get("type")
            if p_type == "title":
                parts = prop["title"]
            elif p_type == "rich_text":
                parts = prop["rich_text"]
            else:
                continue

            key_value = "".join(p.get("plain_text", "") for p in parts).strip()
            if key_value:
                existing[key_value] = page["id"]

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return existing


# ---------------------------------------------------------------------------
# Sincronização
# ---------------------------------------------------------------------------

def _build_properties(row: pd.Series, columns: list[str], title_col: str) -> dict:
    """Constrói o dicionário de propriedades Notion para uma linha do DataFrame."""
    properties: dict = {}
    for col in columns:
        value = row[col]
        if col == title_col:
            properties[col] = _title_property(value)
        else:
            properties[col] = _to_notion_property(value)
    return properties


def sync(file_path: Path) -> None:
    """
    Lê o arquivo Excel e sincroniza cada linha com o banco de dados do Notion.
    """
    if not NOTION_TOKEN or NOTION_TOKEN.startswith("secret_xxx"):
        print("[notion] NOTION_TOKEN não configurado. Pulando envio ao Notion.")
        return

    if not DATABASE_ID or DATABASE_ID.startswith("xxx"):
        print("[notion] NOTION_DATABASE_ID não configurado. Pulando envio ao Notion.")
        return

    print(f"[notion] Lendo arquivo: {file_path}")
    df = pd.read_excel(file_path, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]  # normaliza nomes

    total_rows = len(df)
    print(f"[notion] {total_rows} linhas encontradas. Chave primária: '{PRIMARY_KEY}'")

    if PRIMARY_KEY not in df.columns:
        print(f"[notion] AVISO: coluna '{PRIMARY_KEY}' não encontrada no Excel.")
        print(f"[notion] Colunas disponíveis: {list(df.columns)}")
        print("[notion] Ajuste NOTION_PRIMARY_KEY no .env e tente novamente.")
        return

    columns = list(df.columns)
    title_col = columns[0]  # primeira coluna vira "title" no Notion

    # Busca registros já existentes no Notion
    print("[notion] Buscando registros existentes no Notion...")
    existing = _fetch_existing_pages(PRIMARY_KEY)
    print(f"[notion] {len(existing)} registros encontrados no banco.")

    created = updated = skipped = 0

    for idx, row in df.iterrows():
        key_value = _safe_str(row.get(PRIMARY_KEY, ""))
        if not key_value:
            skipped += 1
            continue

        properties = _build_properties(row, columns, title_col)

        try:
            if key_value in existing:
                # Atualiza página existente
                notion.pages.update(
                    page_id=existing[key_value],
                    properties=properties,
                )
                updated += 1
            else:
                # Cria nova página
                notion.pages.create(
                    parent={"database_id": DATABASE_ID},
                    properties=properties,
                )
                created += 1
        except APIResponseError as e:
            print(f"[notion] Erro na linha {idx} (chave={key_value}): {e}")
            skipped += 1
            continue

        # Progresso a cada 50 linhas
        done = created + updated + skipped
        if done % 50 == 0:
            print(f"[notion] Progresso: {done}/{total_rows} | criadas={created} atualizadas={updated} puladas={skipped}")

    print(f"\n[notion] Sincronização concluída!")
    print(f"  Criadas  : {created}")
    print(f"  Atualizadas: {updated}")
    print(f"  Puladas  : {skipped}")
    print(f"  Total    : {total_rows}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python notion_sender.py <caminho_do_arquivo.xlsx>")
        sys.exit(1)

    sync(Path(sys.argv[1]))
