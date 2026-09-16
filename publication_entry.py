from __future__ import annotations

"""Publication-grade Chemistry Drawing Bot integration.

This module deliberately does not import the application at module-import time.
Instead, :func:`install_publication_renderer` receives the already-loaded app
module and replaces only Chemistry Drawing Bot call points.  The ORCA/Flask
implementation therefore remains untouched.

Optional reaction conditions::

    Reactants = Products || condition above arrow | condition below arrow

Example::

    Naproxen + NBS = 5-bromonaproxen + succinimide || NBS, CH3COOH | 15 min, dark
"""

import asyncio
import html
import importlib

import publication_renderer as pub


def _extract_conditions(text: str) -> tuple[str, str, str]:
    if "||" not in text:
        return text.strip(), "", ""
    reaction, annotations = text.split("||", 1)
    fields = [part.strip() for part in annotations.split("|", 1)]
    top = fields[0] if fields else ""
    bottom = fields[1] if len(fields) > 1 else ""
    return reaction.strip(), top, bottom


def install_publication_renderer(app_module) -> None:
    """Install only Chemistry Drawing Bot upgrades into ``app_module``.

    No ORCA, Flask, webhook, state, or ORCA input-generation symbol is changed.
    """

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
        sep_info = app_module.find_reaction_separator(reaction_text)
        if not sep_info:
            await status_msg.edit_text(
                "⚠️ I could not identify the reaction separator after reading the reaction conditions."
            )
            return

        separator, index = sep_info
        if separator == ">>":
            left, right = reaction_text.split(">>", 1)
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
