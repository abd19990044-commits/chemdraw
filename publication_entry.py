from __future__ import annotations

"""Publication-grade Chemistry Drawing Bot integration.

This module deliberately does not import the application at module-import time.
Instead, :func:`install_publication_renderer` receives the already-loaded app
module and replaces only Chemistry Drawing Bot call points. The ORCA/Flask
implementation therefore remains untouched.

Optional reaction conditions::

    Reactants = Products || condition above arrow | condition below arrow

General example::

    Ethene + H2 = Ethane || Ni | 150 C
"""

import asyncio
import html
import importlib
import re

import publication_renderer_v2 as pub


PUBLICATION_WELCOME_TEXT = (
    "🧪 <b>Chemistry Drawing Bot — Publication Edition</b>\n\n"
    "Create clean chemical structures and publication-ready reaction schemes directly from names, "
    "SMILES, InChI, CAS RN, or ordinary chemical equations.\n\n"
    "<b>Single compound</b>\n"
    "Send a chemical name, SMILES, InChI, or CAS RN.\n"
    "Examples:\n"
    "<code>Aspirin</code>\n"
    "<code>CC(=O)OC1=CC=CC=C1C(=O)O</code>\n"
    "<code>50-78-2</code>\n\n"
    "<b>Several compounds</b>\n"
    "Separate compounds with <code> + </code>.\n"
    "Example: <code>Aspirin + Caffeine</code>\n\n"
    "<b>Reaction scheme</b>\n"
    "Use <code>=</code> (or <code>&gt;&gt;</code>) between reactants and products. Stoichiometric "
    "coefficients are supported, including integers, decimals, and fractions.\n"
    "Examples:\n"
    "<code>2 H2 + O2 = 2 H2O</code>\n"
    "<code>Methane + Oxygen = Carbon Dioxide + Water</code>\n\n"
    "<b>Conditions above the arrow</b>\n"
    "Add <code>||</code> after the reaction, then type the condition.\n"
    "Example:\n"
    "<code>Ethene + H2 = Ethane || Ni</code>\n\n"
    "<b>Conditions above and below the arrow</b>\n"
    "Use <code>|| above | below</code>.\n"
    "Example:\n"
    "<code>Ethene + H2 = Ethane || Ni | 150 °C</code>\n\n"
    "<b>Research-quality output</b>\n"
    "• SVG vector file for direct manuscript insertion and editing\n"
    "• 600 DPI PNG at original resolution\n"
    "• Editable MDL RXN reaction file\n"
    "• Automatic element/charge balance check\n"
    "• Correct stoichiometric coefficients and ionic charges\n"
    "• Consistent bond lengths, typography, spacing, and reaction-arrow layout\n\n"
    "For the cleanest parsing, place spaces around <code>+</code> and <code>=</code> in ordinary equations."
)


def _extract_conditions(text: str) -> tuple[str, str, str]:
    if "||" not in text:
        return text.strip(), "", ""
    reaction, annotations = text.split("||", 1)
    fields = [part.strip() for part in annotations.split("|", 1)]
    top = fields[0] if fields else ""
    bottom = fields[1] if len(fields) > 1 else ""
    return reaction.strip(), top, bottom


def _publication_reaction_separator(text: str, original_finder):
    """Identify obvious reactions before RDKit ever sees the whole equation."""
    reaction_text, _, _ = _extract_conditions(text or "")
    s = reaction_text.strip()
    if not s:
        return None

    bracket = 0
    paren = 0
    i = 0
    while i < len(s) - 1:
        ch = s[i]
        if ch == "[":
            bracket += 1
        elif ch == "]" and bracket:
            bracket -= 1
        elif not bracket:
            if ch == "(":
                paren += 1
            elif ch == ")" and paren:
                paren -= 1
            elif not paren and s.startswith(">>", i):
                if s[:i].strip() and s[i + 2:].strip():
                    return ">>", i
        i += 1

    candidates: list[int] = []
    bracket = 0
    paren = 0
    for i, ch in enumerate(s):
        if ch == "[":
            bracket += 1
        elif ch == "]" and bracket:
            bracket -= 1
        elif not bracket:
            if ch == "(":
                paren += 1
            elif ch == ")" and paren:
                paren -= 1
            elif ch == "=" and not paren:
                left = s[:i]
                right = s[i + 1:]
                spaced = (
                    i > 0
                    and i + 1 < len(s)
                    and s[i - 1].isspace()
                    and s[i + 1].isspace()
                )
                reaction_like = "+" in left or "+" in right
                if left.strip() and right.strip() and (spaced or reaction_like):
                    candidates.append(i)

    if candidates:
        for idx in candidates:
            if (
                idx > 0
                and idx + 1 < len(s)
                and s[idx - 1].isspace()
                and s[idx + 1].isspace()
            ):
                return "=", idx
        return "=", candidates[-1]

    return original_finder(s)


def _looks_like_plain_chemical_name(token: str) -> bool:
    """Conservatively identify tokens that should not be probed as SMILES."""
    s = (token or "").strip()
    if not s or " " in s:
        return True
    if re.search(r"[\[\]()=#@/\\.%:]", s):
        return False
    if re.fullmatch(r"\d+(?:,\d+)*-[A-Za-z][A-Za-z0-9-]*", s):
        return True
    if "-" in s and re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", s):
        return True
    if re.fullmatch(r"[A-Za-z]+", s):
        smiles_letters = set("BCNOPSFIHclbrnospa")
        if any(ch not in smiles_letters for ch in s):
            return True
        if len(s) > 5 and any(ch.islower() for ch in s):
            return True
    return False


def install_publication_renderer(app_module) -> None:
    """Install only Chemistry Drawing Bot upgrades into ``app_module``.

    No ORCA, Flask, webhook, state, or ORCA input-generation symbol is changed.
    """

    original_find_reaction_separator = app_module.find_reaction_separator
    original_detect_input_kind = app_module.detect_input_kind
    original_resolve_species = app_module.resolve_species

    def publication_find_reaction_separator(text: str):
        return _publication_reaction_separator(text, original_find_reaction_separator)

    def publication_detect_input_kind(token: str):
        stripped = (token or "").strip()
        if app_module.is_valid_cas_number(stripped):
            return app_module.InputKind.CAS
        if stripped.upper().startswith("INCHI="):
            return app_module.InputKind.INCHI
        if _looks_like_plain_chemical_name(stripped):
            return app_module.InputKind.NAME
        return original_detect_input_kind(stripped)

    async def publication_resolve_species(token: str):
        """Resolve verified local aliases before generic remote name probing."""
        raw = (token or "").strip()
        alias_key = re.sub(r"\s+", " ", raw).strip().lower()
        alias = app_module.LOCAL_ALIASES.get(alias_key)
        if alias:
            source, value = alias
            if source == "cid":
                smiles = await app_module.pubchem_client.get_canonical_smiles_by_cid(value)
            else:
                smiles = await app_module.pubchem_client.get_canonical_smiles_by_name(value)
            if smiles:
                return smiles, f"Resolved using verified synonym: {raw}"
        return await original_resolve_species(raw)

    def publication_split_compound_terms(text: str):
        return [
            app_module.ReactionTerm(term.coefficient, term.name)
            for term in pub.split_reaction_side(text)
        ]

    async def publication_handle_reaction(
        update,
        status_msg,
        text: str,
        separator: str,
        index: int,
    ) -> None:
        reaction_text, arrow_top, arrow_bottom = _extract_conditions(text)
        sep_info = publication_find_reaction_separator(reaction_text)
        if not sep_info:
            await status_msg.edit_text(
                "⚠️ I could not identify the reaction separator after reading the reaction conditions."
            )
            return

        separator, index = sep_info
        if separator == ">>":
            left, right = reaction_text[:index], reaction_text[index + 2:]
        else:
            left, right = reaction_text[:index], reaction_text[index + 1:]

        reactant_terms = publication_split_compound_terms(left)
        product_terms = publication_split_compound_terms(right)
        if not reactant_terms or not product_terms:
            await status_msg.edit_text(
                "⚠️ Please provide at least one compound on each side of the reaction."
            )
            return

        reactant_resolved, reactant_notes = await app_module._resolve_terms(reactant_terms)
        product_resolved, product_notes = await app_module._resolve_terms(product_terms)

        unresolved = [
            term.name
            for term, (_, smiles) in zip(reactant_terms, reactant_resolved)
            if smiles is None
        ]
        unresolved += [
            term.name
            for term, (_, smiles) in zip(product_terms, product_resolved)
            if smiles is None
        ]
        if unresolved:
            joined = ", ".join(html.escape(name) for name in dict.fromkeys(unresolved))
            await status_msg.edit_text(
                app_module.NOT_FOUND_TEXT.format(name=joined),
                parse_mode="HTML",
            )
            return

        reactant_pairs = [(coef, smiles) for coef, smiles in reactant_resolved]
        product_pairs = [(coef, smiles) for coef, smiles in product_resolved]
        balance = app_module.check_reaction_balance(reactant_pairs, product_pairs)
        equation = app_module.format_equation(reactant_terms, product_terms)

        png_bytes, svg_bytes, rxn_file = await asyncio.gather(
            asyncio.to_thread(
                pub.render_reaction_png,
                reactant_pairs,
                product_pairs,
                arrow_top=arrow_top,
                arrow_bottom=arrow_bottom,
            ),
            asyncio.to_thread(
                pub.render_reaction_svg,
                reactant_pairs,
                product_pairs,
                arrow_top=arrow_top,
                arrow_bottom=arrow_bottom,
            ),
            asyncio.to_thread(
                app_module.generate_rxn_file,
                [app_module.ReactionTerm(coef, smiles) for coef, smiles in reactant_pairs],
                [app_module.ReactionTerm(coef, smiles) for coef, smiles in product_pairs],
                "Reaction.rxn",
                equation,
            ),
        )

        caption_lines = ["Reaction scheme:", f"<code>{html.escape(equation)}</code>"]
        if arrow_top:
            caption_lines.append("Conditions: " + html.escape(arrow_top))
        if arrow_bottom:
            caption_lines.append("Additional conditions: " + html.escape(arrow_bottom))
        if balance.get("checked"):
            caption_lines.append(html.escape(balance["message"]))
        for note in (reactant_notes + product_notes)[:3]:
            caption_lines.append("ℹ️ " + html.escape(note))
        caption = "\n".join(caption_lines)

        if png_bytes:
            preview = app_module.bytes_to_named_bytesio(png_bytes, "Reaction_600dpi.png")
            await update.message.reply_photo(
                photo=preview,
                caption=caption,
                parse_mode="HTML",
            )
            png_document = app_module.bytes_to_named_bytesio(
                png_bytes,
                "Reaction_600dpi.png",
            )
            await update.message.reply_document(
                document=png_document,
                caption="Publication PNG (600 DPI; original resolution)",
            )

        if svg_bytes:
            svg_document = app_module.bytes_to_named_bytesio(
                svg_bytes,
                "Reaction_vector.svg",
            )
            await update.message.reply_document(
                document=svg_document,
                caption="Publication vector SVG — recommended for manuscripts and graphical editing",
            )

        if rxn_file:
            await update.message.reply_document(
                document=rxn_file,
                caption="Editable reaction file (.rxn)",
            )
        await status_msg.delete()

    app_module.WELCOME_TEXT = PUBLICATION_WELCOME_TEXT
    app_module.find_reaction_separator = publication_find_reaction_separator
    app_module.detect_input_kind = publication_detect_input_kind
    app_module.resolve_species = publication_resolve_species
    app_module.split_compound_terms = publication_split_compound_terms
    app_module.render_reaction_scheme = pub.render_reaction_scheme
    app_module._handle_reaction = publication_handle_reaction
    app_module.chem_logger.info(
        "Publication-grade reaction renderer enabled: SVG vector + 600 DPI PNG."
    )


def main() -> None:
    """Standalone launcher retained for diagnostics and staged deployments."""
    try:
        app_module = importlib.import_module("legacy_app")
    except ModuleNotFoundError:
        app_module = importlib.import_module("app")
    install_publication_renderer(app_module)
    app_module.main()


if __name__ == "__main__":
    main()
