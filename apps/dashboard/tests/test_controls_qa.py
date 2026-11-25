"""QA #185 : cas limites complémentaires (no-store en lecture, double clic, entrées exotiques)."""

from __future__ import annotations

import pytest
from test_controls_page import (  # noqa: F401 - fixture autouse réutilisée
    admin_client,
    controls,
    csrf_of,
    db_path,
    new_client,
    visitor,
)


def test_every_controls_get_is_no_store() -> None:
    assert new_client().get("/controls").headers["cache-control"] == "no-store"
    assert visitor().get("/controls").headers["cache-control"] == "no-store"
    client = admin_client()
    urls = (
        "/controls",
        "/controls?confirmer=pause",
        "/controls?plafond=abc",
        "/controls?fait=pause",
    )
    for url in urls:
        assert client.get(url).headers["cache-control"] == "no-store"
    assert new_client().get("/admin/login").headers["cache-control"] == "no-store"


def test_double_click_on_pause_is_idempotent() -> None:
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    for _ in range(2):
        data = {"csrf": token, "confirm": "oui"}
        assert client.post("/controls/pause", data=data).status_code == 303
    assert controls()[:2] == (True, "manuel")


@pytest.mark.parametrize("raw", [" 500", "500 ", "+500", "5e3", "٥٠٠", "５００", "😀"])
def test_exotic_cap_inputs_are_refused_without_write(raw: str) -> None:
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    resp = client.post("/controls/cap", data={"csrf": token, "cap": raw, "confirm": "oui"})
    assert resp.status_code == 422
    assert controls()[2] == 500_000


def test_confirm_other_than_oui_sends_nothing() -> None:
    client = admin_client()
    token = csrf_of(client.get("/controls?confirmer=pause").text)
    for value in ("", "non", "OUI", "true"):
        resp = client.post("/controls/pause", data={"csrf": token, "confirm": value})
        assert resp.status_code == 422
    assert controls()[0] is False


def test_arabic_script_password_is_just_wrong() -> None:
    resp = new_client().post("/admin/login", data={"password": "كلمة-السر"})
    assert resp.status_code == 401 and "vva_admin" not in resp.headers.get("set-cookie", "")
