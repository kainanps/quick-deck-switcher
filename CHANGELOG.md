# Changelog

## 1.1.0 — 2026-11-25
- English interface, with Portuguese (Brazil) available. Follows Anki's language by default.
- New menu: *Tools › Quick Deck Switcher* with "Show deck switcher bar" (Alt+N) and *Language / Idioma*.
- New `language` config option (`auto`, `en`, `pt`).

## 1.0.1 — 2026-11-25
- Fix: error dialog `TypeError ... got 'NoneType'` in `aqt/toolbar.py` (`set_background`) when switching decks quickly on Anki 25.x.
- Short cooldown between deck switches so the reviewer page can finish loading.

## 1.0.0 — 2026-11-25
- First release.
