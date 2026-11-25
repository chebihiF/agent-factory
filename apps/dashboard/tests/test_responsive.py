"""Refonte mobile (#192, #193, #194) : nav unique, cibles >= 44 px, polices >= 16 px, 360 px.

Spec : docs/design/pause-plafond.md (« Responsive et navigation », « Accessibilité »). Les tests
lisent le HTML rendu et les feuilles de style ; la mesure à 360 px est dans la PR.
"""
# ruff: noqa: F811

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_main import TEMPLATES, client, env, new_client  # noqa: F401

from dashboard import github

STATIC = Path(github.__file__).parent / "static"
ROOT = Path(github.__file__).parents[4]
PAGES = ["/", "/tokens", "/interactions"]
LABELS = ["Accueil", "Tokens", "Interactions"]
CSS_FILES = ["nav.css", "board.css", "tokens.css"]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def inline_styles(name: str) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", read(TEMPLATES / name), re.S))


def all_css() -> str:
    parts = [read(STATIC / name) for name in CSS_FILES]
    parts += [inline_styles(n) for n in ("tokens.html", "interactions.html", "login.html")]
    return "\n".join(parts)


def rule(css: str, selector: str) -> str:
    """Corps de la première règle dont le sélecteur est exactement `selector`."""
    match = re.search(rf"(?:^|\}}|\n)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, f"règle absente : {selector}"
    return match.group(1)


# --- Une seule nav exposée (#192) ----------------------------------------------------------
@pytest.mark.parametrize("path", PAGES)
def test_page_exposes_exactly_one_nav(client: TestClient, path: str) -> None:
    html = client.get(path).text
    assert html.count("<nav") == 1
    assert '<nav aria-label="Navigation principale">' in html


@pytest.mark.parametrize("path", PAGES)
def test_nav_has_labels_icons_and_current_page(client: TestClient, path: str) -> None:
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", client.get(path).text, re.S)
    assert nav
    links = re.findall(r"<a href=\"([^\"]+)\"([^>]*)>(.*?)</a>", nav.group(1), re.S)
    assert [re.sub(r"<[^>]+>", "", body).strip() for _, _, body in links] == LABELS
    assert [href for href, attrs, _ in links if 'aria-current="page"' in attrs] == [path]
    for _, _, body in links:
        assert '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"' in body


def test_login_has_no_nav(env: None) -> None:
    assert "<nav" not in new_client().get("/login").text


def test_board_section_shortcuts_are_a_group_not_a_second_nav(client: TestClient) -> None:
    html = client.get("/").text
    assert '<div class="jump" role="group" aria-label="Aller à une section">' in html


# --- Viewport et zone sûre ------------------------------------------------------------------
@pytest.mark.parametrize("path", [*PAGES, "/login"])
def test_viewport_covers_the_safe_area(client: TestClient, path: str) -> None:
    content = "width=device-width, initial-scale=1, viewport-fit=cover"
    assert f'<meta name="viewport" content="{content}">' in client.get(path).text


def test_tab_bar_is_bottom_bar_below_900px_and_header_nav_above() -> None:
    css = read(STATIC / "nav.css")
    base = css.split("@media (min-width: 900px)")[0]
    assert "position: fixed" in rule(base, 'nav[aria-label="Navigation principale"]')
    assert "env(safe-area-inset-bottom)" in base
    assert "max(var(--space-3), env(safe-area-inset-bottom))" in base
    assert "@media (min-width: 900px)" in css
    wide = css.split("@media (min-width: 900px)")[1]
    assert "position: static" in wide
    assert "max-width: 1200px" in css


def test_tab_targets_are_56px_on_mobile_and_44px_on_desktop() -> None:
    tokens = read(STATIC / "tokens.css")
    assert "--target-min: 44px" in tokens and "--target-tab: 56px" in tokens
    css = read(STATIC / "nav.css")
    base, wide = css.split("@media (min-width: 900px)")[:2]
    tab = rule(base, 'nav[aria-label="Navigation principale"] a')
    assert "min-height: var(--target-tab)" in tab
    assert "min-height: var(--target-min)" in wide


def test_current_tab_is_marked_by_more_than_color() -> None:
    css = read(STATIC / "nav.css")
    base = css.split("@media (min-width: 900px)")[0]
    current = rule(base, 'nav[aria-label="Navigation principale"] a[aria-current="page"]')
    assert "var(--color-surface-2)" in current and "font-weight: 700" in current
    wide = css.split("@media (min-width: 900px)")[1]
    assert "border-bottom: 3px solid var(--color-accent)" in wide


# --- Polices >= 16 px (#194) ----------------------------------------------------------------
def font_sizes_px(css: str) -> list[float]:
    sizes = [float(v) for v in re.findall(r"font-size:\s*([\d.]+)px", css)]
    sizes += [float(v) * 16 for v in re.findall(r"font-size:\s*([\d.]+)r?em", css)]
    sizes += [float(v) for v in re.findall(r"font:[^;]*?(\d+)px", css)]
    return sizes


def test_no_font_below_16px_in_any_stylesheet_or_inline_style() -> None:
    assert all(size >= 16 for size in font_sizes_px(all_css())), font_sizes_px(all_css())


@pytest.mark.parametrize("name", ["tokens", "interactions", "login", "board"])
def test_templates_use_no_small_or_relative_shrink(name: str) -> None:
    html = read(TEMPLATES / f"{name}.html")
    assert "<small" not in html
    assert not re.search(r"font-size:\s*(?:small|smaller|x-small|[.\d]+%)", html)


# --- 360 px sans défilement horizontal (#192, #193) -----------------------------------------
def test_no_min_width_or_nowrap_that_can_overflow_360px() -> None:
    css = all_css()
    for value in re.findall(r"(?<![-\w(])min-width:\s*(\d+)px", css):
        assert int(value) <= 360, value
    without_sr_only = re.sub(r"\.sr-only\s*\{[^}]*\}", "", css)
    assert "white-space: nowrap" not in without_sr_only
    assert "overflow-x: auto" not in css and "overflow-x: scroll" not in css


def test_long_unbreakable_text_wraps_inside_the_page() -> None:
    assert "overflow-wrap: anywhere" in rule(read(STATIC / "nav.css"), ".page")


def test_tokens_page_footer_is_not_fixed_over_the_tab_bar() -> None:
    assert "position: fixed" not in inline_styles("tokens.html")


def test_body_leaves_room_for_the_fixed_tab_bar() -> None:
    css = read(STATIC / "nav.css")
    body = rule(css.split("@media (min-width: 900px)")[0], "body")
    assert "padding-bottom" in body and "var(--target-tab)" in body


# --- Cibles >= 44 px (#194) ------------------------------------------------------------------
def test_form_fields_and_buttons_are_at_least_44px() -> None:
    login = inline_styles("login.html")
    assert "36px" not in login
    assert "min-height: var(--target-min)" in rule(login, "input")
    assert "min-height: var(--target-min)" in rule(login, "button")


def test_retry_link_of_interactions_has_a_44px_target(client: TestClient) -> None:
    css = inline_styles("interactions.html")
    assert "min-height: var(--target-min)" in rule(css, '[role="alert"] a')


# --- Spec copiée dans le dépôt ----------------------------------------------------------------
def test_design_spec_is_in_the_repository_and_validated() -> None:
    spec = read(ROOT / "docs" / "design" / "pause-plafond.md")
    assert "Responsive et navigation" in spec and "viewport-fit=cover" in spec
    assert "en attente de validation" not in spec.lower()


def test_routes_contract_describes_the_tab_bar() -> None:
    text = read(ROOT / "contracts" / "dashboard-routes.md")
    assert "barre d'onglets" in text and "Accueil" in text
