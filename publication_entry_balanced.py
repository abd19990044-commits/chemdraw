from __future__ import annotations

"""Automatic-balancing integration for the publication chemistry bot.

This layer installs the existing publication renderer first, then replaces only
its reaction handler.  ORCA/Flask and the legacy application remain untouched.
"""

import asyncio
import html
from fractions import Fraction

import publication_entry as base
import publication_renderer_v2 as pub
from reaction_balancer import balance_reaction_smiles


def install_publication_renderer(app_module) -> None:
    # Install all established publication features first: parser, name handling,
    # SVG/PNG renderer, welcome text, etc.
    base.install_publication_renderer(app_module)

    # Advertise the new capability without changing the private/general examples.
    app_module.WELCOME_TEXT = app_module.WELCOME_TEXT.replace(
        "• Automatic element/charge balance check",
        "• Automatic stoichiometric equation balancing (elements + charge)\n"
        "• Automatic element/charge balance verification",
    )

    async def publication_handle_reaction_balanced(
        update,
        status_msg,
        text: str,
        separator: str,
        index: int,
    ) -> None:
        reaction_text, arrow_top, arrow_bottom = base._extract_conditions(text)
        sep_info = app_module.find_reaction_separator(reaction_text)
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

        reactant_terms = app_module.split_compound_terms(left)
        product_terms = app_module.split_compound_terms(right)
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
        original_balance = app_module.check_reaction_balance(reactant_pairs, product_pairs)

        balance_notice = ""
        automatically_balanced = False

        if original_balance.get("checked") and original_balance.get("balanced"):
            balance_notice = "✅ Equation was already balanced; the entered coefficients were preserved."
        else:
            solution = balance_reaction_smiles(
                [smiles for _, smiles in reactant_pairs],
                [smiles for _, smiles in product_pairs],
            )
            if solution is not None:
                left_coeffs, right_coeffs = solution
                reactant_pairs = [
                    (Fraction(coef), smiles)
                    for coef, (_, smiles) in zip(left_coeffs, reactant_pairs)
                ]
                product_pairs = [
                    (Fraction(coef), smiles)
                    for coef, (_, smiles) in zip(right_coeffs, product_pairs)
                ]
                reactant_terms = [
                    app_module.ReactionTerm(Fraction(coef), term.name)
                    for coef, term in zip(left_coeffs, reactant_terms)
                ]
                product_terms = [
                    app_module.ReactionTerm(Fraction(coef), term.name)
                    for coef, term in zip(right_coeffs, product_terms)
                ]
                verified = app_module.check_reaction_balance(reactant_pairs, product_pairs)
                if verified.get("checked") and verified.get("balanced"):
                    automatically_balanced = True
                    balance_notice = "✅ Equation automatically balanced by the bot using the smallest whole-number coefficients."
                    balance = verified
                else:
                    balance = original_balance
                    balance_notice = (
                        "⚠️ Automatic balancing was attempted but could not be independently verified; "
                        "the entered coefficients were retained."
                    )
            else:
                balance = original_balance
                balance_notice = (
                    "⚠️ Automatic balancing was not applied because a unique positive stoichiometric "
                    "solution could not be determined from the entered species."
                )

        if original_balance.get("checked") and original_balance.get("balanced"):
            balance = original_balance

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
        if automatically_balanced:
            caption_lines.append("<b>✅ Equation automatically balanced by the bot.</b>")
        elif balance_notice:
            caption_lines.append(html.escape(balance_notice))
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
                caption=(
                    "Publication PNG (600 DPI; original resolution)"
                    + (" — automatically balanced" if automatically_balanced else "")
                ),
            )

        if svg_bytes:
            svg_document = app_module.bytes_to_named_bytesio(
                svg_bytes,
                "Reaction_vector.svg",
            )
            await update.message.reply_document(
                document=svg_document,
                caption=(
                    "Publication vector SVG — recommended for manuscripts and graphical editing"
                    + (" — automatically balanced" if automatically_balanced else "")
                ),
            )

        if rxn_file:
            await update.message.reply_document(
                document=rxn_file,
                caption=(
                    "Editable reaction file (.rxn)"
                    + (" — automatically balanced" if automatically_balanced else "")
                ),
            )
        await status_msg.delete()

    app_module._handle_reaction = publication_handle_reaction_balanced
    app_module.chem_logger.info(
        "Automatic stoichiometric balancer enabled: elements + formal charge, exact integer coefficients."
    )
