from __future__ import annotations

"""Interactive UI and per-user rendering preferences for Chemistry Drawing Bot."""

from dataclasses import dataclass
from typing import Dict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass
class ChemUserSettings:
    auto_balance: bool = True
    arrow_style: str = "forward"  # forward | equilibrium | reversible
    structure_style: str = "smart"  # smart | skeletal | condensed
    # PNG and editable CDXML are primary exports. SVG/TIFF/RXN remain optional
    # so the free Render instance does not perform avoidable work by default.
    export_png: bool = True
    export_svg: bool = False
    export_tiff: bool = False
    export_cdxml: bool = True
    export_rxn: bool = False
    transparent_background: bool = False
    show_balance_report: bool = True


_SETTINGS: Dict[int, ChemUserSettings] = {}


def get_settings(user_id: int | None) -> ChemUserSettings:
    if user_id is None:
        return ChemUserSettings()
    return _SETTINGS.setdefault(int(user_id), ChemUserSettings())


def _mark(v: bool) -> str:
    return "✅" if v else "▫️"


def advanced_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Redox · acidic", callback_data="chem:redox_acidic"),
            InlineKeyboardButton("⚡ Redox · basic", callback_data="chem:redox_basic"),
        ],
        [
            InlineKeyboardButton("🧂 Net ionic", callback_data="chem:net_ionic"),
            InlineKeyboardButton("🔢 Atom mapping", callback_data="chem:atom_map"),
        ],
        [InlineKeyboardButton("↪️ Curved-arrow mechanism", callback_data="chem:mechanism_help")],
        [InlineKeyboardButton("⬅️ Drawing settings", callback_data="chem:settings")],
    ])


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
        ],
        [
            InlineKeyboardButton(f"{_mark(settings.export_cdxml)} CDXML · ChemDraw", callback_data="chem:cdxml"),
            InlineKeyboardButton(f"{_mark(settings.export_rxn)} RXN · compatibility", callback_data="chem:rxn"),
        ],
        [InlineKeyboardButton(f"{_mark(settings.transparent_background)} Transparent background", callback_data="chem:transparent")],
        [InlineKeyboardButton("🧪 Advanced chemistry", callback_data="chem:advanced")],
        [
            InlineKeyboardButton("⚗️ Reaction help", callback_data="chem:help"),
            InlineKeyboardButton("↩️ Reset settings", callback_data="chem:reset"),
        ],
    ])


def welcome_keyboard(user_id: int | None) -> InlineKeyboardMarkup:
    s = get_settings(user_id)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Drawing settings", callback_data="chem:settings")],
        [InlineKeyboardButton("🧪 Advanced chemistry", callback_data="chem:advanced")],
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
        f"PNG: {'ON' if s.export_png else 'OFF'} · SVG: {'ON' if s.export_svg else 'OFF'} · TIFF: {'ON' if s.export_tiff else 'OFF'}\n"
        f"CDXML (editable in ChemDraw): {'ON' if s.export_cdxml else 'OFF'} · "
        f"RXN compatibility: {'ON' if s.export_rxn else 'OFF'}\n\n"
        "ℹ️ PNG and CDXML are enabled by default. Heavy rendering jobs are queued to protect the free server under concurrent load."
    )


def advanced_text() -> str:
    return (
        "🧪 <b>Advanced Chemistry</b>\n\n"
        "The actions below operate on your most recently drawn reaction.\n\n"
        "⚡ <b>Redox balancing</b> — may add only H2O/H+/e− in acidic medium or H2O/OH−/e− in basic medium.\n"
        "🧂 <b>Net ionic</b> — dissociates only explicitly supported strong electrolytes and cancels spectator ions.\n"
        "🔢 <b>Atom mapping</b> — conservative 1→1 mapping only when the maximum common substructure is unique.\n"
        "↪️ <b>Mechanism</b> — user-directed curved arrows; the bot never guesses electron flow."
    )


def mechanism_help_text() -> str:
    return (
        "↪️ <b>Curved-arrow mechanism mode</b>\n\n"
        "Use an explicit SMILES followed by atom-to-atom electron-flow instructions:\n"
        "<code>/mechanism CCO || 1-&gt;2</code>\n\n"
        "For more than one arrow:\n"
        "<code>/mechanism CCO || 1-&gt;2,3~&gt;2</code>\n\n"
        "<code>-&gt;</code> = electron-pair arrow\n"
        "<code>~&gt;</code> = single-electron (fishhook) arrow\n\n"
        "Atom numbers are the RDKit atom order shown on the generated mechanism structure. The bot draws only the arrows you specify."
    )


def help_text() -> str:
    return (
        "⚗️ <b>Reaction syntax</b>\n\n"
        "Basic reaction:\n<code>H2 + O2 = H2O</code>\n\n"
        "Conditions above the arrow:\n<code>Ethene + H2 = Ethane || Ni</code>\n\n"
        "Above and below the arrow:\n<code>Ethene + H2 = Ethane || Ni | 150 °C</code>\n\n"
        "The bot can automatically calculate the smallest whole-number stoichiometric coefficients. "
        "CDXML is the primary editable ChemDraw export; RXN is kept only for compatibility. "
        "Advanced chemistry tools are available from the 🧪 button."
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
    elif action == "cdxml":
        s.export_cdxml = not s.export_cdxml
    elif action == "rxn":
        s.export_rxn = not s.export_rxn
    elif action == "transparent":
        s.transparent_background = not s.transparent_background
    elif action == "reset":
        _SETTINGS[user_id] = ChemUserSettings()
        s = _SETTINGS[user_id]
    return s
