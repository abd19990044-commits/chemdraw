from __future__ import annotations

"""Interactive UI and per-user rendering preferences for Chemistry Drawing Bot.

Kept deliberately separate from the ORCA bot.  The module only depends on
python-telegram-bot UI classes and stores lightweight user preferences in memory.
"""

from dataclasses import dataclass, asdict
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class ChemUserSettings:
    auto_balance: bool = True
    arrow_style: str = "forward"  # forward | equilibrium | reversible
    structure_style: str = "smart"  # smart | skeletal | condensed
    export_png: bool = True
    export_svg: bool = True
    export_tiff: bool = False
    export_rxn: bool = True
    transparent_background: bool = False
    show_balance_report: bool = True


_SETTINGS: Dict[int, ChemUserSettings] = {}


def get_settings(user_id: int | None) -> ChemUserSettings:
    if user_id is None:
        return ChemUserSettings()
    return _SETTINGS.setdefault(int(user_id), ChemUserSettings())


def _mark(v: bool) -> str:
    return "✅" if v else "▫️"


def settings_keyboard(settings: ChemUserSettings) -> InlineKeyboardMarkup:
    arrow_label = {
        "forward": "→ Forward",
        "equilibrium": "⇌ Equilibrium",
        "reversible": "↔ Reversible",
    }.get(settings.arrow_style, "→ Forward")
    style_label = {
        "smart": "Smart",
        "skeletal": "Skeletal",
        "condensed": "Condensed",
    }.get(settings.structure_style, "Smart")

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"{_mark(settings.auto_balance)} Auto-balance", callback_data="chem:balance"),
            InlineKeyboardButton(arrow_label, callback_data="chem:arrow"),
        ],
        [
            InlineKeyboardButton(f"🧬 Style: {style_label}", callback_data="chem:style"),
            InlineKeyboardButton(f"{_mark(settings.show_balance_report)} Balance report", callback_data="chem:report"),
        ],
        [
            InlineKeyboardButton(f"{_mark(settings.export_png)} PNG", callback_data="chem:png"),
            InlineKeyboardButton(f"{_mark(settings.export_svg)} SVG", callback_data="chem:svg"),
            InlineKeyboardButton(f"{_mark(settings.export_tiff)} TIFF", callback_data="chem:tiff"),
            InlineKeyboardButton(f"{_mark(settings.export_rxn)} RXN", callback_data="chem:rxn"),
        ],
        [
            InlineKeyboardButton(
                f"{_mark(settings.transparent_background)} Transparent background",
                callback_data="chem:transparent",
            )
        ],
        [
            InlineKeyboardButton("⚗️ Reaction help", callback_data="chem:help"),
            InlineKeyboardButton("↩️ Reset settings", callback_data="chem:reset"),
        ],
    ])


def welcome_keyboard(user_id: int | None) -> InlineKeyboardMarkup:
    s = get_settings(user_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Drawing settings", callback_data="chem:settings")],
        [
            InlineKeyboardButton(f"{_mark(s.auto_balance)} Auto-balance", callback_data="chem:balance"),
            InlineKeyboardButton("📤 Export options", callback_data="chem:exports"),
        ],
        [InlineKeyboardButton("⚗️ Examples & syntax", callback_data="chem:help")],
    ])


def settings_text(s: ChemUserSettings) -> str:
    arrow = {"forward": "→", "equilibrium": "⇌", "reversible": "↔"}.get(s.arrow_style, "→")
    return (
        "⚙️ <b>Reaction drawing settings</b>\n\n"
        f"Auto-balance: {'ON' if s.auto_balance else 'OFF'}\n"
        f"Reaction arrow: {arrow}\n"
        f"Structure style: {s.structure_style.title()}\n"
        f"Balance report: {'ON' if s.show_balance_report else 'OFF'}\n"
        f"Transparent background: {'ON' if s.transparent_background else 'OFF'}\n\n"
        "<b>Exports</b>\n"
        f"PNG: {'ON' if s.export_png else 'OFF'} · SVG: {'ON' if s.export_svg else 'OFF'} · "
        f"TIFF: {'ON' if s.export_tiff else 'OFF'} · RXN: {'ON' if s.export_rxn else 'OFF'}"
    )


def help_text() -> str:
    return (
        "⚗️ <b>Reaction syntax</b>\n\n"
        "Basic reaction:\n<code>H2 + O2 = H2O</code>\n\n"
        "Conditions above the arrow:\n<code>Ethene + H2 = Ethane || Ni</code>\n\n"
        "Above and below the arrow:\n<code>Ethene + H2 = Ethane || Ni | 150 °C</code>\n\n"
        "The bot can automatically calculate the smallest whole-number stoichiometric coefficients. "
        "It never invents missing chemical species merely to force a balance."
    )


def apply_action(user_id: int, action: str) -> ChemUserSettings:
    s = get_settings(user_id)
    if action == "balance":
        s.auto_balance = not s.auto_balance
    elif action == "arrow":
        order = ["forward", "equilibrium", "reversible"]
        s.arrow_style = order[(order.index(s.arrow_style) + 1) % len(order)]
    elif action == "style":
        order = ["smart", "skeletal", "condensed"]
        s.structure_style = order[(order.index(s.structure_style) + 1) % len(order)]
    elif action == "report":
        s.show_balance_report = not s.show_balance_report
    elif action == "png":
        s.export_png = not s.export_png
    elif action == "svg":
        s.export_svg = not s.export_svg
    elif action == "tiff":
        s.export_tiff = not s.export_tiff
    elif action == "rxn":
        s.export_rxn = not s.export_rxn
    elif action == "transparent":
        s.transparent_background = not s.transparent_background
    elif action == "reset":
        _SETTINGS[user_id] = ChemUserSettings()
        s = _SETTINGS[user_id]
    return s
