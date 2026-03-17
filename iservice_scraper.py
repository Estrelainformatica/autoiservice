"""
Automação do site iService Midea (ics-amer.midea.com)
Fluxo:
  1. Login (com reutilização de sessão salva)
  2. Navega para Consulta de OS
  3. Remove data de criação
  4. Expande filtros → Sinalizador Pendente = Y
  5. Clica na lupa (pesquisar)
  6. Exportar por modelo
  7. Exportar a lista
  8. Aguarda modal → faz refresh a cada 5s até relatório disponível
  9. Baixa o .xlsx

Renomeado de scraper.py para iservice_scraper.py para evitar conflito
com o pacote pip 'scraper' (https://pypi.org/project/scraper/).
"""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import (
    Download,
    Page,
    BrowserContext,
    async_playwright,
)

load_dotenv()

SITE_URL: str = os.getenv("SITE_URL", "https://ics-amer.midea.com")
WORK_ORDER_PATH: str = "/#/wom/views/serviceExecution/workOrderQuery/index"
SITE_USER: str = os.getenv("SITE_USER", "")
SITE_PASSWORD: str = os.getenv("SITE_PASSWORD", "")
DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "./downloads"))
SESSION_FILE: Path = Path("session.json")

MAX_DOWNLOAD_CYCLES: int = 60  # 60 × 5s = 5 minutos máximo


# ---------------------------------------------------------------------------
# Login e navegação
# ---------------------------------------------------------------------------

async def _is_login_page(page: Page) -> bool:
    """Verifica se a página atual é a tela de login."""
    try:
        return await page.locator('input[type="password"]').is_visible(timeout=3000)
    except Exception:
        return False


async def login(page: Page, context: BrowserContext) -> None:
    """Realiza o login e salva a sessão para uso posterior."""
    print("[scraper] Realizando login...")

    # Campo de usuário — tenta seletores comuns
    for selector in [
        'input[name="username"]',
        'input[id="username"]',
        'input[placeholder*="usu"], input[placeholder*="user"], input[placeholder*="conta"], input[placeholder*="account"]',
        'input[type="text"]:visible',
    ]:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=1500):
                await loc.fill(SITE_USER)
                break
        except Exception:
            continue

    await page.fill('input[type="password"]', SITE_PASSWORD)

    # Botão de submit
    for selector in ['button[type="submit"]', 'button:has-text("Login")', 'button:has-text("Entrar")', 'input[type="submit"]']:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                break
        except Exception:
            continue

    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)

    # Salva sessão (cookies + localStorage) para próximas execuções
    await context.storage_state(path=str(SESSION_FILE))
    print(f"[scraper] Sessão salva em {SESSION_FILE}")


async def navigate_to_work_order(page: Page) -> None:
    """Navega para a página de Consulta de OS."""
    target = SITE_URL + WORK_ORDER_PATH
    print(f"[scraper] Navegando para {target}")
    await page.goto(target)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

async def clear_creation_date(page: Page) -> None:
    """Remove o conteúdo dos campos de data de criação."""
    print("[scraper] Limpando data de criação...")

    # Tenta botões de limpar do Element UI (ícone ×)
    clear_selectors = [
        ".el-range__close-icon",
        ".el-input__clear",
        "i.el-icon-close",
    ]
    for sel in clear_selectors:
        btns = page.locator(sel)
        count = await btns.count()
        for i in range(count):
            try:
                btn = btns.nth(i)
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    await page.wait_for_timeout(200)
            except Exception:
                pass

    # Fallback: limpa manualmente os inputs de data
    date_inputs = page.locator(".el-date-editor input, .el-date-range-picker input")
    count = await date_inputs.count()
    for i in range(count):
        try:
            inp = date_inputs.nth(i)
            if await inp.is_visible(timeout=500):
                await inp.triple_click()
                await inp.press("Delete")
                await inp.press("Backspace")
        except Exception:
            pass

    await page.wait_for_timeout(500)


async def expand_filters(page: Page) -> None:
    """
    Expande o painel de filtros avançados no canto superior direito.
    O botão é descrito como uma seta < apontando para baixo.
    """
    print("[scraper] Expandindo painel de filtros...")

    # Tenta seletores comuns para o botão de expandir filtros
    expand_selectors = [
        # Ícone de seta que abre filtros avançados
        "i.el-icon-arrow-down:visible",
        "i.el-icon-caret-bottom:visible",
        ".filter-toggle:visible",
        "button.collapse-btn:visible",
        # Texto comum em botões de filtro
        'span:has-text("Filtro"):visible',
        'span:has-text("Filter"):visible',
        'button:has(i.el-icon-arrow-down):visible',
    ]

    for sel in expand_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1000):
                await loc.click()
                await page.wait_for_timeout(800)
                print(f"[scraper] Filtros expandidos via: {sel}")
                return
        except Exception:
            continue

    print("[scraper] Aviso: não encontrou botão de expandir filtros. Continuando...")


async def set_sinalizador_pendente(page: Page) -> None:
    """
    Seleciona 'Y' no campo 'Sinalizador Pendente'.
    O campo fica visível após expandir o painel de filtros.
    """
    print("[scraper] Configurando Sinalizador Pendente = Y...")

    # Localiza o contêiner do campo "Sinalizador Pendente"
    label_selectors = [
        ':text-is("Sinalizador Pendente")',
        ':text("Sinalizador Pendente")',
        'label:has-text("Sinalizador Pendente")',
    ]

    for label_sel in label_selectors:
        try:
            label = page.locator(label_sel).first
            if not await label.is_visible(timeout=1500):
                continue

            # Tenta clicar no el-select mais próximo do label
            container = page.locator(label_sel).locator("xpath=ancestor::*[contains(@class,'form-item') or contains(@class,'el-form-item')][1]")
            dropdown_input = container.locator(".el-select .el-input__inner, .el-input__inner").first

            if await dropdown_input.is_visible(timeout=1000):
                await dropdown_input.click()
                await page.wait_for_timeout(500)

                # Seleciona a opção "Y"
                option = page.locator('.el-select-dropdown__item:visible').filter(has_text="Y").first
                if await option.is_visible(timeout=2000):
                    await option.click()
                    await page.wait_for_timeout(300)
                    print("[scraper] Sinalizador Pendente = Y selecionado.")
                    return
        except Exception:
            continue

    print("[scraper] Aviso: não encontrou 'Sinalizador Pendente'. Verifique o seletor.")


# ---------------------------------------------------------------------------
# Pesquisa e exportação
# ---------------------------------------------------------------------------

async def click_search(page: Page) -> None:
    """Clica na lupa de pesquisa."""
    print("[scraper] Clicando na lupa de pesquisa...")

    search_selectors = [
        "i.isicon--search",
        "button:has(i.isicon--search)",
        "i.el-icon-search",
        "button:has(i.el-icon-search)",
        'button:has-text("Pesquisar")',
        'button:has-text("Search")',
        'button:has-text("Consultar")',
    ]

    for sel in search_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1000):
                await loc.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)
                print(f"[scraper] Pesquisa realizada via: {sel}")
                return
        except Exception:
            continue

    raise RuntimeError("[scraper] Botão de pesquisa não encontrado.")


async def _open_export_dropdown(page: Page) -> None:
    """Abre o menu dropdown de exportação no meio da página."""
    export_btn_selectors = [
        'button:has-text("Exportar"):visible',
        'span:has-text("Exportar"):visible',
        '.export-btn i.el-icon-arrow-down',
        '.el-dropdown:has(.el-dropdown-menu__item:has-text("Exportar")) > button',
        'i.el-icon-arrow-down:visible',
    ]

    for sel in export_btn_selectors:
        try:
            locs = page.locator(sel)
            count = await locs.count()
            for i in range(count):
                loc = locs.nth(i)
                if await loc.is_visible(timeout=500):
                    await loc.click()
                    await page.wait_for_timeout(600)
                    menu = page.locator('.el-dropdown-menu:visible')
                    if await menu.is_visible(timeout=1000):
                        print(f"[scraper] Menu de exportação aberto via: {sel}")
                        return
        except Exception:
            continue

    raise RuntimeError("[scraper] Não foi possível abrir o menu de exportação.")


async def export_by_model(page: Page) -> None:
    """Abre menu de exportação e clica em 'Exportar por modelo'."""
    print("[scraper] Abrindo menu → Exportar por modelo...")
    await _open_export_dropdown(page)

    await page.click('.el-dropdown-menu__item:has-text("Exportar por modelo"):visible')
    await page.wait_for_timeout(2000)
    print("[scraper] 'Exportar por modelo' clicado.")


async def export_list(page: Page) -> None:
    """Abre menu de exportação novamente e clica em 'Exportar a lista'."""
    print("[scraper] Abrindo menu → Exportar a lista...")
    await _open_export_dropdown(page)

    await page.click('.el-dropdown-menu__item:has-text("Exportar a lista"):visible')
    await page.wait_for_timeout(1000)
    print("[scraper] 'Exportar a lista' clicado.")


# ---------------------------------------------------------------------------
# Modal de relatórios e download
# ---------------------------------------------------------------------------

async def handle_download_modal(page: Page) -> Path:
    """
    Aguarda o modal de relatórios.
    - Se houver linhas com status 'waiting' ou 'Tratamento', clica em Reiniciar a cada 5s.
    - Quando o botão de download (span.icon-download) estiver visível, clica e salva.
    """
    print("[scraper] Monitorando modal de relatórios...")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for cycle in range(MAX_DOWNLOAD_CYCLES):
        await page.wait_for_timeout(5000)

        waiting_rows = page.locator(
            '.el-table__body tr, .el-table__row'
        ).filter(
            has=page.locator(
                ':text("waiting"), :text("Waiting"), :text("Tratamento"), :text("tratamento"), :text("Processing")'
            )
        )

        waiting_count = await waiting_rows.count()
        if waiting_count > 0:
            print(f"[scraper] Ciclo {cycle + 1}: {waiting_count} relatório(s) processando. Clicando Reiniciar...")
            reiniciar_selectors = [
                'button:has-text("Reiniciar"):visible',
                'button:has-text("reiniciar"):visible',
                'button:has-text("Refresh"):visible',
                'button:has-text("Atualizar"):visible',
                "i.icon-refresh-all:visible",
                "i.el-icon-refresh:visible",
            ]
            for sel in reiniciar_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=500):
                        await btn.click()
                        print(f"[scraper] Reiniciar clicado via: {sel}")
                        break
                except Exception:
                    pass
            continue

        download_btn_selectors = [
            "span.icon-download:visible",
            ".el-table__body tr:first-child span.icon-download:visible",
            "i.icon-download:visible",
            ".el-table__row:first-child .icon-download:visible",
        ]

        for sel in download_btn_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    print(f"[scraper] Botão de download encontrado no ciclo {cycle + 1}. Baixando...")
                    async with page.expect_download(timeout=60000) as download_info:
                        await btn.click()
                    download: Download = await download_info.value
                    filename = download.suggested_filename or "relatorio.xlsx"
                    file_path = DOWNLOAD_DIR / filename
                    await download.save_as(str(file_path))
                    print(f"[scraper] Arquivo salvo: {file_path}")
                    return file_path
            except Exception:
                continue

        print(f"[scraper] Ciclo {cycle + 1}: download não disponível ainda...")

    raise TimeoutError("[scraper] Download não ficou disponível após 5 minutos.")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

async def run() -> Path:
    """Executa todo o fluxo de scraping e retorna o caminho do .xlsx baixado."""
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=300)

        ctx_kwargs: dict = {"accept_downloads": True}
        if SESSION_FILE.exists():
            ctx_kwargs["storage_state"] = str(SESSION_FILE)
            print(f"[scraper] Sessão anterior encontrada em {SESSION_FILE}. Reutilizando...")

        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        try:
            await page.goto(SITE_URL)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            if await _is_login_page(page):
                await login(page, context)
            else:
                print("[scraper] Sessão válida, login não necessário.")

            await navigate_to_work_order(page)

            if await _is_login_page(page):
                await login(page, context)
                await navigate_to_work_order(page)

            await clear_creation_date(page)
            await expand_filters(page)
            await set_sinalizador_pendente(page)
            await click_search(page)
            await export_by_model(page)
            await export_list(page)

            file_path = await handle_download_modal(page)
            return file_path

        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    result = asyncio.run(run())
    print(f"\n[scraper] Concluído! Arquivo: {result}")
