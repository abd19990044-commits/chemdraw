from __future__ import annotations

"""Staged entrypoint for the publication-grade Chemistry Drawing renderer.

Run with::

    python publication_entry.py

The ORCA bot remains the original ``app`` implementation.  Only Chemistry Bot
symbols are replaced after import, making this entrypoint suitable for a safe
staged rollout before merging the renderer into app.py.

Reaction conditions can optionally be supplied as::

    Reactants = Products || condition above arrow | condition below arrow

Example::

    Naproxen + NBS = 5-bromonaproxen + succinimide || NBS, CH3COOH | 15 min, dark
"""

import asyncio
import html

import app
import publication_renderer as pub


def _publication_split_compound_terms(text: str):
    return [app.ReactionTerm(t.coefficient, t.name) for t in pub.split_reaction_side(text)]


def _extract_conditions(text: str) -> tuple[str, str, str]:
    """Return reaction text, top annotation and bottom annotation."""
    if "||" not in text:
        return text.strip(), "", ""
    reaction, annotations = text.split("||", 1)
    fields = [p.strip() for p in annotations.split("|", 1)]
    top = fields[0] if fields else ""
    bottom = fields[1] if len(fields) > 1 else ""
    return reaction.strip(), top, bottom


async def _publication_handle_reaction(update, status_msg, text: str, separator: str, index: int) -> None:
    reaction_text, arrow_top, arrow_bottom = _extract_conditions(text)
    sep_info = app.find_reaction_separator(reaction_text)
    if not sep_info:
        await status_msg.edit_text("⚠️ I could not identify the reaction separator after reading the reaction conditions.")
        return
    separator, index = sep_info
    if separator == ">>":
        left, right = reaction_text.split(">>", 1)
    else:
        left, right = reaction_text[:index], reaction_text[index + 1:]

    reactant_terms = _publication_split_compound_terms(left)
    product_terms = _publication_split_compound_terms(right)
    if not reactant_terms or not product_terms:
        await status_msg.edit_text("⚠️ Please provide at least one compound on each side of the reaction.")
        return

    reactant_resolved, reactant_notes = await app._resolve_terms(reactant_terms)
    product_resolved, product_notes = await app._resolve_terms(product_terms)
    unresolved = [term.name for term, (_, smiles) in zip(reactant_terms, reactant_resolved) if smiles is None]
    unresolved += [term.name for term, (_, smiles) in zip(product_terms, product_resolved) if smiles is None]
    if unresolved:
        joined = ", ".join(html.escape(n) for n in dict.fromkeys(unresolved))
        await status_msg.edit_text(app.NOT_FOUND_TEXT.format(name=joined), parse_mode="HTML")
        return

    reactant_pairs = [(coef, smiles) for coef, smiles in reactant_resolved]
    product_pairs = [(coef, smiles) for coef, smiles in product_resolved]
    balance = app.check_reaction_balance(reactant_pairs, product_pairs)
    equation = app.format_equation(reactant_terms, product_terms)

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
            app.generate_rxn_file,
            [app.ReactionTerm(coef, smiles) for coef, smiles in reactant_pairs],
            [app.ReactionTerm(coef, smiles) for coef, smiles in product_pairs],
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
        photo = app.bytes_to_named_bytesio(png_bytes, "Reaction_600dpi.png")
        await update.message.reply_photo(photo=photo, caption=caption, parse_mode="HTML")
        png_doc = app.bytes_to_named_bytesio(png_bytes, "Reaction_600dpi.png")
        await update.message.reply_document(
            document=png_doc,
            caption="Publication PNG (600 DPI; original resolution)",
        )
    if svg_bytes:
        svg = app.bytes_to_named_bytesio(svg_bytes, "Reaction_vector.svg")
        await update.message.reply_document(
            document=svg,
            caption="Publication vector SVG — recommended for manuscripts and graphical editing",
        )
    if rxn_file:
        await update.message.reply_document(document=rxn_file, caption="Editable reaction file (.rxn)")
    await status_msg.delete()


def install_publication_renderer() -> None:
    """Patch only Chemistry Drawing Bot functions, leaving ORCA untouched."""
    app.split_compound_terms = _publication_split_compound_terms
    app.render_reaction_scheme = pub.render_reaction_scheme
    app._handle_reaction = _publication_handle_reaction


if __name__ == "__main__":
    install_publication_renderer()
    app.main()
