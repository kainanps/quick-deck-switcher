"""
Quick Deck Switcher — Anki Desktop add-on (23.10+ / 24.x / 25.x)

A floating pill-shaped bar in the top-right corner of the main window that lets
you jump between decks and subdecks without going back to the Decks screen.
It works while reviewing (question or answer side) and on the deck overview /
congratulations screen.

    [ << ]  [ < ]   Current-deck   [ > ]  [ >> ]

    <<  previous top-level deck        (Alt+Shift+←)
    <   previous deck in the tree      (Alt+←)
    >   next deck in the tree          (Alt+→)
    >>  next top-level deck            (Alt+Shift+→)
    Alt+N toggles the bar (the state is saved across sessions).

The interface is available in English and Portuguese (Brazil). By default it
follows Anki's interface language; it can be changed in Tools > Quick Deck
Switcher > Language, or in the add-on config.

No external dependencies: only Anki's own API (aqt/anki) and its bundled PyQt6.
"""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass
from typing import Any, Callable, Optional

import anki.lang
from anki.decks import DeckId
from aqt import gui_hooks, mw
from aqt.qt import (
    QAction,
    QActionGroup,
    QColor,
    QEvent,
    QFrame,
    QHBoxLayout,
    QIcon,
    QKeySequence,
    QLabel,
    QMenu,
    QObject,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPointF,
    QShortcut,
    QSize,
    QSizePolicy,
    Qt,
    QTimer,
    QToolButton,
    QWidget,
    qconnect,
)
from aqt.theme import theme_manager
from aqt.utils import tooltip

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Add-on module name (folder name inside addons21 — a number when installed
# from AnkiWeb). The add-on manager uses it to read/write the config.
ADDON_MODULE = __name__

# Main-window states in which the bar and the navigation shortcuts are active.
STUDY_STATES = ("review", "overview")

# Hierarchy separator in Anki deck names ("Parent::Child").
DECK_SEP = "::"

# Navigation actions, in the order they appear on the bar.
PREV_ROOT, PREV_SUB, NEXT_SUB, NEXT_ROOT = "prev_root", "prev_sub", "next_sub", "next_root"
NAV_ACTIONS = (PREV_ROOT, PREV_SUB, NEXT_SUB, NEXT_ROOT)

# Interval between deck switches: lets the reviewer page finish loading before
# the next switch (rapid clicks / shortcut presses).
SWITCH_COOLDOWN_MS = 350

# Default values — also used to complete old or partial user configs.
DEFAULT_CONFIG: dict[str, Any] = {
    "visible": False,
    "language": "auto",  # "auto" (follow Anki) | "en" | "pt"
    "navigation_mode": "all",  # "all" = every deck | "due" = decks with cards due today
    "include_filtered_decks": True,
    "label_mode": "short",  # "short" = deck name only | "path" = shortened path
    "shortcuts": {
        "toggle": "Alt+N",
        PREV_SUB: "Alt+Left",
        NEXT_SUB: "Alt+Right",
        PREV_ROOT: "Alt+Shift+Left",
        NEXT_ROOT: "Alt+Shift+Right",
    },
}


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------

LANGUAGES = ("auto", "en", "pt")

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        PREV_ROOT: "Previous deck",
        PREV_SUB: "Previous subdeck",
        NEXT_SUB: "Next subdeck",
        NEXT_ROOT: "Next deck",
        "menu_title": "Quick Deck Switcher",
        "menu_show": "Show deck switcher bar",
        "menu_language": "Language",
        "lang_auto": "Automatic (follow Anki)",
        "bar_shown": "Quick Deck Switcher: bar shown",
        "bar_shown_later": "Quick Deck Switcher: bar enabled (appears when you open a deck)",
        "bar_hidden": "Quick Deck Switcher: bar hidden",
        "no_due": "No decks with cards due today.",
        "no_decks": "No decks found.",
        "no_other": "No other deck in that direction.",
        "language_set": "Quick Deck Switcher: language set to English",
    },
    "pt": {
        PREV_ROOT: "Baralho anterior",
        PREV_SUB: "Sub-baralho anterior",
        NEXT_SUB: "Avançar sub-baralho",
        NEXT_ROOT: "Avançar baralho",
        "menu_title": "Quick Deck Switcher",
        "menu_show": "Mostrar barra de baralhos",
        "menu_language": "Idioma",
        "lang_auto": "Automático (seguir o Anki)",
        "bar_shown": "Quick Deck Switcher: barra visível",
        "bar_shown_later": "Quick Deck Switcher: barra ativada (aparece ao abrir um baralho)",
        "bar_hidden": "Quick Deck Switcher: barra oculta",
        "no_due": "Nenhum baralho com cards pendentes hoje.",
        "no_decks": "Nenhum baralho encontrado.",
        "no_other": "Não há outro baralho nessa direção.",
        "language_set": "Quick Deck Switcher: idioma alterado para português",
    },
}

# Language names are always shown in their own language.
LANGUAGE_NAMES = {"en": "English", "pt": "Português (Brasil)"}


def resolve_language(setting: str) -> str:
    """'auto' follows Anki's interface language (Portuguese for any 'pt*'
    locale, English otherwise)."""
    if setting in ("en", "pt"):
        return setting
    current = (getattr(anki.lang, "current_lang", "") or "").lower()
    return "pt" if current.startswith("pt") else "en"


class Translator:
    def __init__(self, lang: str = "en") -> None:
        self.lang = lang

    def __call__(self, key: str) -> str:
        return STRINGS.get(self.lang, STRINGS["en"]).get(key) or STRINGS["en"][key]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _merge_defaults(user: Optional[dict], defaults: dict) -> dict:
    """Fill in missing keys of the user config with defaults (recursive)."""
    merged = copy.deepcopy(defaults)
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(value, merged[key])
        else:
            merged[key] = value
    return merged


def load_config() -> dict:
    return _merge_defaults(mw.addonManager.getConfig(ADDON_MODULE), DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    # Anki treats config.json as defaults and stores user values in the
    # add-on's meta.json; getConfig() always returns the user's version, so
    # the saved state survives restarts and add-on updates.
    mw.addonManager.writeConfig(ADDON_MODULE, config)


# ---------------------------------------------------------------------------
# Navigation logic (UI-independent)
# ---------------------------------------------------------------------------


def deck_sort_key(name: str) -> tuple[str, ...]:
    """Same ordering as the Decks screen tree: component by component,
    case-insensitive. Sorting by this tuple yields the flattened tree in
    pre-order (parent before its children)."""
    return tuple(part.casefold() for part in name.split(DECK_SEP))


@dataclass(frozen=True)
class DeckEntry:
    id: int
    name: str
    key: tuple[str, ...]

    @property
    def root_key(self) -> str:
        return self.key[0]


def due_deck_ids(col: Any) -> set[int]:
    """IDs of decks with new, learning or review cards due today, respecting
    daily limits (same numbers as the Decks screen)."""
    ids: set[int] = set()
    stack = [col.sched.deck_due_tree()]
    while stack:
        node = stack.pop()
        if node.deck_id and (node.new_count + node.learn_count + node.review_count) > 0:
            ids.add(int(node.deck_id))
        stack.extend(node.children)
    return ids


def collect_decks(col: Any, mode: str = "all", include_filtered: bool = True) -> list[DeckEntry]:
    """Sorted (tree pre-order) list of navigable decks."""
    entries = [
        DeckEntry(int(d.id), d.name, deck_sort_key(d.name))
        for d in col.decks.all_names_and_ids(
            skip_empty_default=True, include_filtered=include_filtered
        )
    ]
    if mode == "due":
        due = due_deck_ids(col)
        entries = [e for e in entries if e.id in due]
    entries.sort(key=lambda e: e.key)
    return entries


def find_target(entries: list[DeckEntry], current_name: str, action: str) -> Optional[DeckEntry]:
    """Compute the destination deck, wrapping around at both ends.

    The position is derived from the current deck's sort key, so it works even
    if the current deck is not in the list (e.g. "due" mode after finishing
    the current deck)."""
    if not entries:
        return None
    cur = deck_sort_key(current_name)

    if action == NEXT_SUB:
        return next((e for e in entries if e.key > cur), entries[0])
    if action == PREV_SUB:
        return next((e for e in reversed(entries) if e.key < cur), entries[-1])

    # Top-level groups: (root key, first deck of the group).
    roots: list[tuple[str, DeckEntry]] = []
    for e in entries:
        if not roots or roots[-1][0] != e.root_key:
            roots.append((e.root_key, e))
    cur_root = cur[0]

    if action == NEXT_ROOT:
        return next((e for rk, e in roots if rk > cur_root), roots[0][1])
    if action == PREV_ROOT:
        return next((e for rk, e in reversed(roots) if rk < cur_root), roots[-1][1])
    return None


# ---------------------------------------------------------------------------
# UI: icons, style and the floating widget
# ---------------------------------------------------------------------------


def chevron_icon(direction: int, double: bool, color: str, size: int = 16) -> QIcon:
    """Draw a crisp chevron icon (‹ › « ») at any DPI.
    direction: -1 = left, +1 = right."""
    dpr = 3.0
    pixmap = QPixmap(int(size * dpr), int(size * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    cy = size / 2
    half_w, half_h = 2.6, 4.6
    for offset in ((-2.6, 2.6) if double else (0.0,)):
        cx = size / 2 + offset
        tip_x = cx + direction * half_w
        arm_x = cx - direction * half_w
        path = QPainterPath(QPointF(arm_x, cy - half_h))
        path.lineTo(QPointF(tip_x, cy))
        path.lineTo(QPointF(arm_x, cy + half_h))
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def theme_palette(night: bool) -> dict[str, str]:
    if night:
        return {
            "bg": "rgba(36, 44, 54, 242)",
            "border": "rgba(255, 255, 255, 40)",
            "btn": "rgba(255, 255, 255, 14)",
            "hover": "rgba(255, 255, 255, 34)",
            "pressed": "rgba(255, 255, 255, 52)",
            "fg": "#c8d2dc",
            "accent": "#7cc8ee",
        }
    return {
        "bg": "rgba(255, 255, 255, 245)",
        "border": "rgba(0, 0, 0, 34)",
        "btn": "rgba(0, 0, 0, 10)",
        "hover": "rgba(0, 0, 0, 22)",
        "pressed": "rgba(0, 0, 0, 36)",
        "fg": "#4a5562",
        "accent": "#1769aa",
    }


def build_qss(p: dict[str, str]) -> str:
    return f"""
    QFrame#qdsBar {{
        background-color: {p['bg']};
        border: 1px solid {p['border']};
        border-radius: 21px;
    }}
    QFrame#qdsBar QToolButton {{
        background-color: {p['btn']};
        border: none;
        border-radius: 9px;
        padding: 0px;
    }}
    QFrame#qdsBar QToolButton:hover {{ background-color: {p['hover']}; }}
    QFrame#qdsBar QToolButton:pressed {{ background-color: {p['pressed']}; }}
    QFrame#qdsBar QLabel#qdsName {{
        color: {p['accent']};
        background: transparent;
        font-size: 14px;
        font-weight: 600;
        padding: 0px 8px;
    }}
    """


def pretty_shortcut(seq: str) -> str:
    """'Alt+Shift+Left' -> 'Alt+Shift+←' (in the platform's native format)."""
    text = QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)
    return text.replace("Left", "←").replace("Right", "→")


class DeckSwitcherBar(QFrame):
    """Pill anchored to the top-right corner of the main window's central
    widget (above mw.web)."""

    MARGIN = 12
    BUTTON_SIZE = 28
    NAME_MAX_WIDTH = 240

    # (action, chevron direction, double chevron?)
    BUTTONS_LEFT = ((PREV_ROOT, -1, True), (PREV_SUB, -1, False))
    BUTTONS_RIGHT = ((NEXT_SUB, 1, False), (NEXT_ROOT, 1, True))

    def __init__(self, host: QWidget, on_action: Callable[[str], None]) -> None:
        super().__init__(host)
        self._host = host
        self._full_name = ""
        self._label_mode = "short"
        self._buttons: dict[str, tuple[QToolButton, int, bool]] = {}

        self.setObjectName("qdsBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        for action, direction, double in self.BUTTONS_LEFT:
            layout.addWidget(self._make_button(action, direction, double, on_action))

        self.name_label = QLabel(self)
        self.name_label.setObjectName("qdsName")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)

        for action, direction, double in self.BUTTONS_RIGHT:
            layout.addWidget(self._make_button(action, direction, double, on_action))

        self.hide()
        # Follow resizes of the central area (and therefore of the window).
        host.installEventFilter(self)
        self.apply_theme()

    def _make_button(
        self, action: str, direction: int, double: bool, on_action: Callable[[str], None]
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setFixedSize(self.BUTTON_SIZE, self.BUTTON_SIZE)
        btn.setIconSize(QSize(16, 16))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # NoFocus: clicking the bar doesn't steal focus from the card, so
        # Space / 1-4 keep working in the reviewer.
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        qconnect(btn.clicked, lambda _checked=False, a=action: on_action(a))
        self._buttons[action] = (btn, direction, double)
        return btn

    # -- appearance ----------------------------------------------------------

    def apply_theme(self) -> None:
        palette = theme_palette(theme_manager.night_mode)
        self.setStyleSheet(build_qss(palette))
        for btn, direction, double in self._buttons.values():
            btn.setIcon(chevron_icon(direction, double, palette["fg"]))
        self._update_label()

    def set_tooltips(self, shortcuts: dict[str, str], tr: Callable[[str], str]) -> None:
        for action, (btn, _d, _dbl) in self._buttons.items():
            seq = shortcuts.get(action)
            hint = f" ({pretty_shortcut(seq)})" if seq else ""
            btn.setToolTip(tr(action) + hint)

    def set_label_mode(self, mode: str) -> None:
        self._label_mode = mode if mode in ("short", "path") else "short"
        self._update_label()

    def set_deck_name(self, full_name: str) -> None:
        if full_name != self._full_name:
            self._full_name = full_name
            self._update_label()

    def _update_label(self) -> None:
        parts = self._full_name.split(DECK_SEP) if self._full_name else [""]
        if self._label_mode == "path":
            text, elide = " › ".join(parts), Qt.TextElideMode.ElideMiddle
        else:
            text, elide = parts[-1], Qt.TextElideMode.ElideRight
        self.name_label.ensurePolished()
        shown = self.name_label.fontMetrics().elidedText(text, elide, self.NAME_MAX_WIDTH)
        self.name_label.setText(shown)
        self.name_label.setToolTip(" › ".join(parts))
        if self.isVisible():
            self.reposition()

    # -- positioning ---------------------------------------------------------

    def reposition(self) -> None:
        self.adjustSize()
        x = max(self.MARGIN, self._host.width() - self.width() - self.MARGIN)
        self.move(x, self.MARGIN)
        self.raise_()  # keep it above mw.web / the top toolbar

    def show_bar(self) -> None:
        self.show()
        self.reposition()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._host and event.type() == QEvent.Type.Resize and self.isVisible():
            self.reposition()
        return False  # never consume the event


# ---------------------------------------------------------------------------
# Guard against a race condition in Anki itself
# ---------------------------------------------------------------------------


def install_toolbar_background_guard() -> None:
    """Prevent the TypeError in aqt/toolbar.py (set_background) on Anki 25.x.

    Every time a card is shown, the reviewer asks the top toolbar to copy the
    page background ("updateToolbar"). Anki reads that background with
    JavaScript on mw.web and passes the result to re.sub without checking for
    None. If the mw.web page is replaced before the answer arrives — which is
    exactly what happens when switching decks quickly — the result is None and
    Anki shows an error dialog.

    We wrap only the mw.toolbarWeb instance: during that call, None results are
    dropped (there is no background to copy). Nothing else changes.
    """
    toolbar_web = getattr(mw, "toolbarWeb", None)
    web = getattr(mw, "web", None)
    original = getattr(toolbar_web, "update_background_image", None)
    if web is None or original is None or getattr(original, "_qds_guard", False):
        return

    def guarded_update_background_image(*args: Any, **kwargs: Any) -> Any:
        had_own = "evalWithCallback" in web.__dict__
        previous = web.__dict__.get("evalWithCallback")
        real_eval = web.evalWithCallback

        def eval_ignoring_none(js: str, cb: Optional[Callable] = None) -> None:
            if cb is None:
                return real_eval(js, None)
            return real_eval(js, lambda val: cb(val) if val is not None else None)

        web.evalWithCallback = eval_ignoring_none
        try:
            return original(*args, **kwargs)
        finally:
            # restore exactly what was there before (including other add-ons' patches)
            if had_own:
                web.evalWithCallback = previous
            else:
                web.__dict__.pop("evalWithCallback", None)

    guarded_update_background_image._qds_guard = True  # type: ignore[attr-defined]
    toolbar_web.update_background_image = guarded_update_background_image


# ---------------------------------------------------------------------------
# Controller: menu, shortcuts, hooks and deck switching
# ---------------------------------------------------------------------------


class QuickDeckSwitcher:
    def __init__(self) -> None:
        self.config: dict = copy.deepcopy(DEFAULT_CONFIG)
        self.tr = Translator()
        self.bar: Optional[DeckSwitcherBar] = None
        self.menu: Optional[QMenu] = None
        self.toggle_action: Optional[QAction] = None
        self.language_menu: Optional[QMenu] = None
        self.language_actions: dict[str, QAction] = {}
        self.nav_shortcuts: list[QShortcut] = []
        self._switching = False

    # -- setup ---------------------------------------------------------------

    def setup(self) -> None:
        self.config = load_config()
        self.tr.lang = resolve_language(self.config["language"])

        self.bar = DeckSwitcherBar(mw.centralWidget(), self.navigate)
        self.bar.set_label_mode(self.config["label_mode"])

        self._build_menu()
        self._apply_shortcuts()
        install_toolbar_background_guard()

        gui_hooks.state_did_change.append(self._on_state_did_change)
        gui_hooks.theme_did_change.append(self._on_theme_did_change)
        gui_hooks.profile_did_open.append(self.update_ui)
        gui_hooks.profile_will_close.append(self._on_profile_will_close)
        gui_hooks.operation_did_execute.append(self._on_operation_did_execute)
        gui_hooks.overview_did_refresh.append(self._on_screen_refreshed)
        gui_hooks.reviewer_did_show_question.append(self._on_screen_refreshed)
        mw.addonManager.setConfigUpdatedAction(ADDON_MODULE, self._on_config_updated)

        self.update_ui()

    def _build_menu(self) -> None:
        """Tools > Quick Deck Switcher > [✓ Show bar (Alt+N)] / Language > …"""
        self.menu = QMenu(mw)
        mw.form.menuTools.addMenu(self.menu)

        self.toggle_action = QAction(mw)
        self.toggle_action.setCheckable(True)
        qconnect(self.toggle_action.triggered, self.set_visible)
        self.menu.addAction(self.toggle_action)

        self.menu.addSeparator()
        self.language_menu = self.menu.addMenu("")
        group = QActionGroup(mw)
        group.setExclusive(True)
        for code in LANGUAGES:
            action = QAction(mw)
            action.setCheckable(True)
            qconnect(action.triggered, functools.partial(self.set_language, code))
            group.addAction(action)
            self.language_menu.addAction(action)
            self.language_actions[code] = action

        self._retranslate()

    def _retranslate(self) -> None:
        """Apply the current language to every visible string."""
        tr = self.tr
        self.menu.setTitle(tr("menu_title"))
        self.toggle_action.setText(tr("menu_show"))
        # Always bilingual, so the option is easy to find in either language.
        other = "Idioma" if self.tr.lang == "en" else "Language"
        self.language_menu.setTitle(f"{tr('menu_language')} / {other}")
        for code, action in self.language_actions.items():
            action.setText(tr("lang_auto") if code == "auto" else LANGUAGE_NAMES[code])
            action.setChecked(code == self.config["language"])
        self.bar.set_tooltips(self.config["shortcuts"], tr)

    def _apply_shortcuts(self) -> None:
        """(Re)create the shortcuts from the config."""
        shortcuts = self.config["shortcuts"]

        for sc in self.nav_shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self.nav_shortcuts = []

        for action in NAV_ACTIONS:
            seq = shortcuts.get(action)
            if not seq:
                continue
            sc = QShortcut(QKeySequence(seq), mw)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.setAutoRepeat(False)  # holding the key doesn't fire repeated switches
            qconnect(sc.activated, functools.partial(self.navigate, action))
            self.nav_shortcuts.append(sc)

        # Alt+N lives on the menu action itself (works on every screen).
        self.toggle_action.setShortcut(QKeySequence(shortcuts.get("toggle") or ""))
        self.toggle_action.setChecked(bool(self.config["visible"]))
        self.bar.set_tooltips(shortcuts, self.tr)

    # -- state / visibility --------------------------------------------------

    @staticmethod
    def _in_study_state() -> bool:
        return mw.col is not None and mw.state in STUDY_STATES

    def update_ui(self, *_args: Any) -> None:
        """Show/hide the bar and enable/disable shortcuts for the current screen."""
        if self.bar is None:
            return
        active = self._in_study_state()
        # Outside review/overview the shortcuts are disabled, so Alt+←/→ are
        # not swallowed on the Decks screen.
        for sc in self.nav_shortcuts:
            sc.setEnabled(active)

        if active and self.config["visible"]:
            self.refresh_label(force=True)
            self.bar.show_bar()
        else:
            self.bar.hide()

    def refresh_label(self, force: bool = False) -> None:
        if self.bar is None or not self._in_study_state():
            return
        if not force and not self.bar.isVisible():
            return
        self.bar.set_deck_name(mw.col.decks.current()["name"])

    def set_visible(self, visible: bool) -> None:
        """Called from the Tools menu or Alt+N. Persisted in the config."""
        self.config["visible"] = bool(visible)
        save_config(self.config)
        self.toggle_action.setChecked(bool(visible))
        self.update_ui()

        if not visible:
            key = "bar_hidden"
        elif self._in_study_state():
            key = "bar_shown"
        else:
            key = "bar_shown_later"
        tooltip(self.tr(key), period=1500)

    def set_language(self, code: str, *_args: Any) -> None:
        """Called from Tools > Quick Deck Switcher > Language."""
        self.config["language"] = code
        save_config(self.config)
        self.tr.lang = resolve_language(code)
        self._retranslate()
        tooltip(self.tr("language_set"), period=1500)

    # -- navigation ----------------------------------------------------------

    def navigate(self, action: str) -> None:
        if not self._in_study_state() or self._switching:
            return
        col = mw.col
        mode = self.config["navigation_mode"]
        entries = collect_decks(col, mode, bool(self.config["include_filtered_decks"]))
        current = col.decks.current()

        if not entries:
            tooltip(self.tr("no_due" if mode == "due" else "no_decks"), period=1500)
            return

        target = find_target(entries, current["name"], action)
        if target is None or target.id == int(current["id"]):
            tooltip(self.tr("no_other"), period=1500)
            return

        self._switching = True
        try:
            self.open_deck(target.id)
        finally:
            QTimer.singleShot(SWITCH_COOLDOWN_MS, self._end_switch)

    def _end_switch(self) -> None:
        self._switching = False

    def open_deck(self, did: int) -> None:
        col = mw.col
        col.decks.select(DeckId(did))

        new, learn, review = col.sched.counts()
        if new + learn + review > 0:
            if mw.state == "review":
                # End the previous deck's session the way Anki does when
                # leaving the reviewer (fires reviewer_will_end for other add-ons).
                mw.reviewer.cleanup()
            col.startTimebox()
            mw.moveToState("review")
        else:
            mw.moveToState("overview")

        # state_did_change also refreshes, but update the name right away.
        self.refresh_label()

    # -- hooks ---------------------------------------------------------------

    def _on_state_did_change(self, _new_state: str, _old_state: str) -> None:
        self.update_ui()

    def _on_theme_did_change(self) -> None:
        if self.bar is not None:
            self.bar.apply_theme()

    def _on_profile_will_close(self) -> None:
        if self.bar is not None:
            self.bar.hide()
        for sc in self.nav_shortcuts:
            sc.setEnabled(False)

    def _on_operation_did_execute(self, changes: Any, _handler: Any) -> None:
        # e.g. the deck was renamed while studying
        if getattr(changes, "deck", False):
            self.refresh_label()

    def _on_screen_refreshed(self, *_args: Any) -> None:
        self.refresh_label()

    def _on_config_updated(self, new_config: dict) -> None:
        """Called when saving in Tools > Add-ons > Config."""
        self.config = _merge_defaults(new_config, DEFAULT_CONFIG)
        self.tr.lang = resolve_language(self.config["language"])
        self.bar.set_label_mode(self.config["label_mode"])
        self._apply_shortcuts()
        self._retranslate()
        self.update_ui()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

quick_deck_switcher = QuickDeckSwitcher()
# The main window must be fully built (mw.form, centralWidget) before the bar
# and the menu entry are created.
gui_hooks.main_window_did_init.append(quick_deck_switcher.setup)
