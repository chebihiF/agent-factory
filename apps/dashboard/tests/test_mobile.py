"""Lisibilité et cibles tactiles à 360 px (QA #117 ; tickets #192, #193, #194).

Les pages /activity, /controls et /progress ont disparu avec la refonte #179 : restent la nav
commune, /tokens, /interactions et /login. Les règles sont vérifiées dans le CSS livré.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard import auth, db, interactions
from dashboard.main import app

PASSWORD = "mot-de-passe-de-test"  # noqa: S105 - valeur factice de test
HERE = Path(interactions.__file__).parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"
NAV_LINK = 'nav[aria-label="Navigation principale"] a'
SIZED = [STATIC / "nav.css", *sorted(TEMPLATES.glob("*.html"))]


def rule(css: str, selector: str) -> str:
    """Corps de la règle dont le sélecteur est exactement `selector` (le dernier l'emporte)."""
    bodies = re.findall(rf"(?:^|[\s}}]){re.escape(selector)}\s*\{{([^{{}}]*)\}}", css)
    assert bodies, f"règle absente : {selector}"
    return " ".join(bodies)


def declared(body: str, prop: str) -> str:
    values = re.findall(rf"(?:^|[;\s]){prop}\s*:\s*([^;]+)", body)
    assert values, f"{prop} absent de : {body}"
    return values[-1].strip()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VVA_DB_PATH", str(tmp_path / "vva.db"))
    monkeypatch.setenv(auth.PASSWORD_HASH_ENV, auth.hash_password(PASSWORD, iterations=1_000))
    monkeypatch.setenv(auth.SESSION_KEY_ENV, "s" * 48)
    auth.LOGIN_LIMITER.reset()
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert c.post("/login", data={"password": PASSWORD}).status_code == 303
    return c


# --- #192 : liens de la nav >= 44 px -----------------------------------------------------
def test_nav_links_are_tappable_44px() -> None:
    body = rule((STATIC / "nav.css").read_text(encoding="utf-8"), NAV_LINK)
    assert declared(body, "min-height") == "var(--target-min)"
    assert declared(body, "display") == "inline-flex"
    assert declared(body, "align-items") == "center"


def test_nav_wraps_instead_of_scrolling_sideways() -> None:
    body = rule(
        (STATIC / "nav.css").read_text(encoding="utf-8"), 'nav[aria-label="Navigation principale"]'
    )
    assert declared(body, "flex-wrap") == "wrap"


# --- #194 : texte >= 16 px ----------------------------------------------------------------
def _font_sizes(text: str) -> list[str]:
    return re.findall(r"font-size\s*:\s*([^;}]+)", text) + re.findall(r"font\s*:\s*([^;}]+)", text)


@pytest.mark.parametrize("path", SIZED, ids=lambda p: p.name)
def test_no_font_size_under_16px(path: Path) -> None:
    for value in _font_sizes(path.read_text(encoding="utf-8")):
        number = re.search(r"(\d*\.?\d+)\s*(px|rem|em|%)", value)
        if not number:
            continue
        amount, unit = float(number.group(1)), number.group(2)
        pixels = amount if unit == "px" else amount * 16 if unit in ("rem", "em") else amount * 0.16
        assert pixels >= 16, f"{path.name} : {value}"


def test_tokens_page_footer_and_notes_are_readable() -> None:
    text = (TEMPLATES / "tokens.html").read_text(encoding="utf-8")
    assert "opacity" not in text, "l'opacité réduit le contraste du pied de page"
    assert "position: fixed" not in text, "un pied fixe recouvre le contenu à 360 px"
    assert "small { font-size: var(--font-size-small)" in text


def test_tokens_base_scale_stays_16px_minimum() -> None:
    css = (STATIC / "tokens.css").read_text(encoding="utf-8")
    sizes = dict(re.findall(r"--font-size-([a-z]+):\s*(\d+)px", css))
    assert sizes and all(int(v) >= 16 for v in sizes.values()), sizes


# --- #194 : cibles du formulaire de connexion et du lien « Réessayer » -------------------
@pytest.mark.parametrize("selector", ["input", "button"])
def test_login_fields_and_button_are_44px(selector: str) -> None:
    css = (TEMPLATES / "login.html").read_text(encoding="utf-8")
    assert declared(rule(css, selector), "min-height") == "var(--target-min)"


def test_retry_link_is_44px() -> None:
    css = (TEMPLATES / "interactions.html").read_text(encoding="utf-8")
    body = rule(css, '[role="alert"] a')
    assert declared(body, "min-height") == "var(--target-min)"
    assert declared(body, "display") == "inline-flex"


# --- #193 : tableau large, défilement dans son cadre avec indice visible ------------------
def test_wide_table_scrolls_inside_its_frame_with_a_hint(client: TestClient) -> None:
    with db.connect() as conn:
        db.insert_events(
            conn,
            [
                (
                    "a1",
                    "2026-11-18T10:00:00+00:00",
                    "SubagentStart",
                    "sess-1",
                    "backend",
                    "sub-1",
                    None,
                    "viral-video-agents",
                    None,
                    "sonnet",
                )
            ],
        )
    html = client.get("/interactions").text
    assert re.search(r'<div class="scroll"[^>]*role="region"[^>]*tabindex="0"', html)
    assert 'class="scroll-hint"' in html
    css = (TEMPLATES / "interactions.html").read_text(encoding="utf-8")
    assert re.search(r"\n\s*\.scroll-hint\s*\{[^}]*display:\s*none", css)
    assert re.search(
        r"@media \(max-width: \d+px\)\s*\{[^}]*\.scroll-hint\s*\{[^}]*display:\s*block", css
    )
    assert declared(rule(css, ".scroll"), "overflow-x") == "auto"


def test_tokens_page_wraps_long_words() -> None:
    """Le diagnostic OTLP contient des noms de variables sans espace (débordait à 360 px)."""
    css = (TEMPLATES / "tokens.html").read_text(encoding="utf-8")
    assert declared(rule(css, "body"), "overflow-wrap") == "anywhere"
