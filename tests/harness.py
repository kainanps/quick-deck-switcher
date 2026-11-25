"""Minimal `aqt` stub (real PyQt6 + a real Anki collection) to test the
add-on outside Anki. Import this module before the add-on."""
import os
import re
import sys
import tempfile
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)

import anki.collection  # noqa: E402,F401  (inside Anki this is already loaded)
from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402


class Hook(list):
    def __call__(self, *args):
        for fn in list(self):
            fn(*args)


aqt = types.ModuleType("aqt")
qt = types.ModuleType("aqt.qt")
for mod in (QtCore, QtGui, QtWidgets):
    for name in dir(mod):
        if not name.startswith("_"):
            setattr(qt, name, getattr(mod, name))
qt.qconnect = lambda signal, fn: signal.connect(fn)
theme = types.ModuleType("aqt.theme")
theme.theme_manager = types.SimpleNamespace(night_mode=True)
utils = types.ModuleType("aqt.utils")
TOOLTIPS: list[str] = []
utils.tooltip = lambda msg, period=3000, **kw: TOOLTIPS.append(msg)
aqt.gui_hooks = types.SimpleNamespace(
    **{
        name: Hook()
        for name in (
            "main_window_did_init state_did_change theme_did_change profile_did_open "
            "profile_will_close operation_did_execute overview_did_refresh "
            "reviewer_did_show_question"
        ).split()
    }
)
aqt.mw = None
aqt.qt, aqt.theme, aqt.utils = qt, theme, utils
sys.modules.update({"aqt": aqt, "aqt.qt": qt, "aqt.theme": theme, "aqt.utils": utils})

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

DECKS = [
    "Física", "Física::Óptica", "Física::Óptica::Dispersão", "Física::Mecânica",
    "Biologia", "Biologia::Célula", "Biologia::Genética", "história",
]
DECKS_WITH_CARDS = ["Física::Óptica::Dispersão", "Biologia::Genética", "história"]


def make_collection():
    from anki.collection import Collection

    col = Collection(os.path.join(tempfile.mkdtemp(), "collection.anki2"))
    ids = {name: col.decks.id(name) for name in DECKS}
    model = col.models.by_name("Basic")
    for name in DECKS_WITH_CARDS:
        note = col.new_note(model)
        note["Front"], note["Back"] = name, "x"
        col.add_note(note, ids[name])
    return col, ids


class FakeAddonManager:
    def __init__(self, defaults):
        self.defaults, self.user, self.callback, self.writes = defaults, None, None, 0

    def getConfig(self, _module):
        return self.user if self.user is not None else dict(self.defaults)

    def writeConfig(self, _module, conf):
        import copy

        self.user = copy.deepcopy(conf)
        self.writes += 1

    def setConfigUpdatedAction(self, _module, fn):
        self.callback = fn


class FakeTopWeb:
    """Literal copy of the relevant logic in aqt/toolbar.py (Anki 25.09)."""

    web_height = 0

    def __init__(self, mw):
        self.mw, self.evals = mw, []

    def eval(self, js):
        self.evals.append(js)

    def update_background_image(self) -> None:
        if self.mw.pm.minimalist_mode():
            return

        def set_background(computed: str) -> None:
            background = re.sub(r"-\d+px ", "0%", computed)
            background = re.sub(r"\sfixed", "", background)
            background = re.sub(r"\d+px", "100vw", background)
            self.eval(f"""document.body.style.setProperty("background", '{background}');""")

        self.mw.web.evalWithCallback(
            """window.getComputedStyle(document.body).background; """, set_background
        )


class FakeWeb(QtWidgets.QLabel):
    results: list = []

    def evalWithCallback(self, js, cb):
        cb(self.results.pop(0))


class FakeMW(QtWidgets.QMainWindow):
    def __init__(self, col, addon_manager):
        super().__init__()
        self.col, self.addonManager = col, addon_manager
        self.state, self.history = "deckBrowser", []
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = FakeWeb('What is the "dispersion of light"?')
        self.web.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.web)
        self.setCentralWidget(central)
        self.form = types.SimpleNamespace(menuTools=self.menuBar().addMenu("Tools"))
        self.pm = types.SimpleNamespace(minimalist_mode=lambda: False)
        self.toolbarWeb = FakeTopWeb(self)
        self.reviewer = types.SimpleNamespace(cleanup=lambda: self.history.append("cleanup"))

    def moveToState(self, state):
        old, self.state = self.state, state
        self.history.append(state)
        aqt.gui_hooks.state_did_change(state, old)
