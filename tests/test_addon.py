import json
import os
import time

import pytest

import harness
from harness import FakeAddonManager, FakeMW, TOOLTIPS, aqt, app, make_collection

import anki.lang
import quick_deck_switcher as qds

CONFIG_JSON = os.path.join(harness.SRC, "quick_deck_switcher", "config.json")


# ---------------------------------------------------------------- pure logic


@pytest.fixture(scope="module")
def col_ids():
    return make_collection()


def names(entries):
    return [e.name for e in entries]


def test_order_matches_deck_tree(col_ids):
    col, _ = col_ids
    assert names(qds.collect_decks(col)) == [
        "Biologia", "Biologia::Célula", "Biologia::Genética", "Física",
        "Física::Mecânica", "Física::Óptica", "Física::Óptica::Dispersão", "história",
    ]
    tree = []

    def walk(node, path):
        for child in node.children:
            p = path + [child.name]
            tree.append("::".join(p))
            walk(child, p)

    walk(col.sched.deck_due_tree(), [])
    assert [t for t in tree if t != "Default"] == names(qds.collect_decks(col))


@pytest.mark.parametrize(
    "current, action, expected",
    [
        ("Biologia::Célula", "next_sub", "Biologia::Genética"),
        ("história", "next_sub", "Biologia"),  # wraps
        ("Biologia", "prev_sub", "história"),  # wraps
        ("Física::Óptica", "next_root", "história"),
        ("história", "next_root", "Biologia"),  # wraps
        ("Física::Óptica", "prev_root", "Biologia"),
        ("Biologia::Genética", "prev_root", "história"),  # wraps
    ],
)
def test_find_target_all(col_ids, current, action, expected):
    entries = qds.collect_decks(col_ids[0])
    assert qds.find_target(entries, current, action).name == expected


def test_due_mode(col_ids):
    entries = qds.collect_decks(col_ids[0], "due")
    assert names(entries) == [
        "Biologia", "Biologia::Genética", "Física", "Física::Óptica",
        "Física::Óptica::Dispersão", "história",
    ]
    # current deck not in the filtered list
    assert qds.find_target(entries, "Física::Mecânica", "next_sub").name == "Física::Óptica"
    assert qds.find_target(entries, "Física::Mecânica", "prev_sub").name == "Física"


def test_language_resolution(monkeypatch):
    for lang, expected in [("pt_BR", "pt"), ("pt_PT", "pt"), ("de", "en"), ("en_US", "en")]:
        monkeypatch.setattr(anki.lang, "current_lang", lang)
        assert qds.resolve_language("auto") == expected
    assert qds.resolve_language("en") == "en" and qds.resolve_language("pt") == "pt"
    assert set(qds.STRINGS["en"]) == set(qds.STRINGS["pt"])
    assert qds._merge_defaults({"visible": True}, qds.DEFAULT_CONFIG)["language"] == "auto"


# ---------------------------------------------------------------- UI / integration


@pytest.fixture(scope="module")
def env():
    col, ids = make_collection()
    am = FakeAddonManager(json.load(open(CONFIG_JSON, encoding="utf-8")))
    mw = FakeMW(col, am)
    aqt.mw = qds.mw = mw
    mw.resize(1376, 768)
    mw.show()
    app.processEvents()
    aqt.gui_hooks.main_window_did_init()
    return qds.quick_deck_switcher, mw, am, col, ids


def nav(sw, action):
    sw._switching = False
    sw.navigate(action)


def test_hidden_by_default_and_toggle(env):
    sw, mw, am, col, ids = env
    assert sw.bar.isHidden() and sw.config["visible"] is False
    assert all(not s.isEnabled() for s in sw.nav_shortcuts)  # Decks screen
    col.decks.select(ids["Física::Óptica::Dispersão"])
    mw.moveToState("review")
    assert sw.bar.isHidden() and all(s.isEnabled() for s in sw.nav_shortcuts)
    sw.toggle_action.trigger()  # Alt+N / menu
    assert sw.config["visible"] and am.user["visible"] is True and sw.toggle_action.isChecked()
    assert sw.bar.isVisible() and sw.bar.name_label.text() == "Dispersão"
    mw.moveToState("deckBrowser")
    assert sw.bar.isHidden() and am.user["visible"] is True
    col.decks.select(ids["Física::Óptica::Dispersão"])
    mw.moveToState("review")
    assert sw.bar.isVisible()


def test_position_follows_resize(env):
    sw, mw, *_ = env
    central = mw.centralWidget()
    for size in [(1376, 768), (1000, 600)]:
        mw.resize(*size)
        app.processEvents()
        g = sw.bar.geometry()
        assert g.top() == 12 and central.width() - g.right() - 1 == 12


def test_switching(env):
    sw, mw, am, col, ids = env
    col.decks.select(ids["Física::Óptica::Dispersão"])
    mw.moveToState("review")
    mw.history.clear()
    nav(sw, "next_sub")  # -> história (has a card)
    assert col.decks.current()["name"] == "história" and mw.history == ["cleanup", "review"]
    assert sw.bar.name_label.text() == "história"
    nav(sw, "prev_root")  # -> Física (cards through its child)
    assert col.decks.current()["name"] == "Física" and mw.history[-1] == "review"
    mw.history.clear()
    nav(sw, "next_sub")  # -> Física::Mecânica (empty)
    assert col.decks.current()["name"] == "Física::Mecânica" and mw.history == ["overview"]


def test_cooldown(env):
    sw, mw, am, col, ids = env
    nav(sw, "next_sub")
    current = col.decks.current()["name"]
    sw.navigate("next_sub")  # immediately again -> ignored
    assert col.decks.current()["name"] == current
    time.sleep(qds.SWITCH_COOLDOWN_MS / 1000 + 0.1)
    app.processEvents()
    assert sw._switching is False


def test_config_update(env):
    sw, mw, am, col, ids = env
    col.decks.select(ids["Física::Mecânica"])
    mw.moveToState("overview")
    am.callback({**am.user, "label_mode": "path", "navigation_mode": "due"})
    assert sw.bar.name_label.text() == "Física › Mecânica"
    nav(sw, "next_sub")
    assert col.decks.current()["name"] == "Física::Óptica"
    am.callback({**am.user, "label_mode": "short", "navigation_mode": "all"})


def test_language_switch(env, monkeypatch):
    sw, *_ = env
    monkeypatch.setattr(anki.lang, "current_lang", "en")
    sw.set_language("auto")
    tips = lambda: [b.toolTip() for b, _, _ in sw.bar._buttons.values()]  # noqa: E731
    assert sw.toggle_action.text() == "Show deck switcher bar"
    assert sw.language_menu.title() == "Language / Idioma"
    assert tips()[0] == "Previous deck (Alt+Shift+←)"
    sw.language_actions["pt"].trigger()
    assert sw.tr.lang == "pt" and sw.config["language"] == "pt"
    assert sw.toggle_action.text() == "Mostrar barra de baralhos"
    assert sw.language_menu.title() == "Idioma / Language"
    assert tips()[3] == "Avançar baralho (Alt+Shift+→)"
    assert TOOLTIPS[-1] == "Quick Deck Switcher: idioma alterado para português"
    monkeypatch.setattr(anki.lang, "current_lang", "pt_BR")
    sw.language_actions["auto"].trigger()
    assert sw.tr.lang == "pt"


def test_toolbar_guard(env):
    _sw, mw, *_ = env
    # The guard is installed at setup; a None result (page replaced) is dropped.
    mw.web.results[:] = [None, "rgb(1,2,3) url(a.png) -48px 0px fixed"]
    mw.toolbarWeb.evals.clear()
    mw.toolbarWeb.update_background_image()
    mw.toolbarWeb.update_background_image()
    assert len(mw.toolbarWeb.evals) == 1
    assert "evalWithCallback" not in mw.web.__dict__
    qds.install_toolbar_background_guard()  # idempotent
    assert getattr(mw.toolbarWeb.update_background_image, "_qds_guard", False)


def test_toolbar_without_guard_reproduces_anki_bug():
    mw = FakeMW(None, None)
    mw.web.results[:] = [None]
    with pytest.raises(TypeError):
        mw.toolbarWeb.update_background_image()
