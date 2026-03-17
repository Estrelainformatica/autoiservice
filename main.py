"""
Ponto de entrada principal.

Uso:
    python main.py               → baixa o Excel e sincroniza com Notion
    python main.py --only-scrape → só baixa o Excel (sem enviar ao Notion)
    python main.py --only-notion <arquivo.xlsx> → só envia um Excel ao Notion

Pré-requisitos:
    pip install -r requirements.txt
    playwright install chromium
    Configure o .env (copie .env.example e preencha os valores)
"""

import asyncio
import sys
from pathlib import Path

from iservice_scraper import run as scrape
from notion_sender import sync as send_to_notion


def main() -> None:
    args = sys.argv[1:]

    # Modo: só envia um arquivo Excel já existente ao Notion
    if "--only-notion" in args:
        idx = args.index("--only-notion")
        if idx + 1 >= len(args):
            print("Erro: informe o caminho do arquivo após --only-notion")
            print("  Exemplo: python main.py --only-notion downloads/relatorio.xlsx")
            sys.exit(1)
        file_path = Path(args[idx + 1])
        if not file_path.exists():
            print(f"Erro: arquivo não encontrado: {file_path}")
            sys.exit(1)
        send_to_notion(file_path)
        return

    # Modo: só baixa o Excel (sem Notion)
    only_scrape = "--only-scrape" in args

    print("=" * 60)
    print("  autoiservice — Midea ICS Automation")
    print("=" * 60)

    # Etapa 1: Baixar o Excel via Playwright
    print("\n[main] Etapa 1: Baixando relatório do site...")
    try:
        file_path: Path = asyncio.run(scrape())
        print(f"[main] Arquivo baixado: {file_path}")
    except Exception as e:
        print(f"[main] Erro ao baixar relatório: {e}")
        sys.exit(1)

    if only_scrape:
        print("\n[main] Modo --only-scrape: não enviando ao Notion.")
        print(f"[main] Arquivo disponível em: {file_path}")
        return

    # Etapa 2: Sincronizar com Notion
    print("\n[main] Etapa 2: Sincronizando com Notion...")
    try:
        send_to_notion(file_path)
    except Exception as e:
        print(f"[main] Erro ao sincronizar com Notion: {e}")
        sys.exit(1)

    print("\n[main] Tudo concluído!")


if __name__ == "__main__":
    main()
