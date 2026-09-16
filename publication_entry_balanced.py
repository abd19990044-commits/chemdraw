from __future__ import annotations

"""Interactive publication chemistry integration.

Installs the established publication parser/resolver, automatic stoichiometric
balancer and a professional Telegram inline-control layer. ORCA/Flask remains
untouched in legacy_app.py.
"""

import asyncio
import html
import io
from fractions import Fraction

from PIL import Image
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import chemistry_ui as ui
import publication_entry as base
import publication_renderer_v3 as pub
import render_capacity as capacity
from cdxml_export import generate_reaction_cdxml
from reaction_balancer import balance_reaction_smiles


def _tiff_from_png(png_bytes: bytes, dpi: int = 600) -> bytes:
    src = Image.open(io.BytesIO(png_bytes))
    out = io.BytesIO()
    src.save(out, format="TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
    return out.getvalue()


def _pairs_key(pairs) -> tuple:
    return tuple((str(Fraction(c)), str(s)) for c, s in pairs)


def install_publication_renderer(app_module) -> None:
    base.install_publication_renderer(app_module)

    app_module.WELCOME_TEXT = (
        "🧪 <b>Chemistry Drawing Bot — Publication Edition</b>\n\n"
        "Create publication-ready chemical structures and reaction schemes from names, SMILES, InChI, CAS RN, or ordinary equations.\n\n"
        "<b>Quick examples</b>\n"
        "<code>H2 + O2 = H2O</code>\n"
        "<code>Ethene + H2 = Ethane || Ni | 150 °C</code>\n\n"
        "<b>Research features</b>\n"
        "• Automatic stoichiometric balancing using elements and formal charge\n"
        "• Forward, equilibrium and reversible reaction arrows\n"
        "• Smart condensed/skeletal molecular depiction\n"
        "• Reaction conditions above and below the arrow\n"
        "• SVG vector, 600 DPI PNG and 600 DPI TIFF exports\n"
        "• <b>CDXML file prepared to be opened and edited directly in ChemDraw</b>\n"
        "• RXN retained only as an optional compatibility format\n"
        "• Optional transparent publication background\n"
        "• Element/charge balance verification\n\n"
        "Use the buttons below to configure your drawing preferences."
    )

    async def interactive_start(update, context) -> None:
        user_id = update.effective_user.id if update.effective_user else None
        app_module.chem_logger.info("/start from user %s", user_id or "unknown")
        asyncio.create_task(app_module.notify_admin(update, context))
        await update.message.reply_text(
            app_module.WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=ui.welcome_keyboard(user_id),
        )

    async def settings_callback(update, context) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        user_id = update.effective_user.id if update.effective_user else 0
        data = query.data or ""
        action = data.split(":", 1)[1] if ":" in data else "settings"
        if action == "help":
            await query.edit_message_text(
                ui.help_text(),
                parse_mode="HTML",
                reply_markup=ui.settings_keyboard(ui.get_settings(user_id)),
            )
            return
        settings = ui.get_settings(user_id) if action in {"settings", "exports"} else ui.apply_action(user_id, action)
        await query.edit_message_text(
            ui.settings_text(settings),
            parse_mode="HTML",
            reply_markup=ui.settings_keyboard(settings),
        )

    async def publication_handle_reaction_balanced(update, status_msg, text: str, separator: str, index: int) -> None:
        user_id = update.effective_user.id if update.effective_user else None
        settings = ui.get_settings(user_id)

        reaction_text, arrow_top, arrow_bottom = base._extract_conditions(text)
        sep_info = app_module.find_reaction_separator(reaction_text)
        if not sep_info:
            await status_msg.edit_text("⚠️ I could not identify the reaction separator after reading the reaction conditions.")
            return
        separator, index = sep_info
        if separator == ">>":
            left, right = reaction_text[:index], reaction_text[index + 2:]
        else:
            left, right = reaction_text[:index], reaction_text[index + 1:]

        reactant_terms = app_module.split_compound_terms(left)
        product_terms = app_module.split_compound_terms(right)
        if not reactant_terms or not product_terms:
            await status_msg.edit_text("⚠️ Please provide at least one compound on each side of the reaction.")
            return

        reactant_resolved, reactant_notes = await app_module._resolve_terms(reactant_terms)
        product_resolved, product_notes = await app_module._resolve_terms(product_terms)
        unresolved = [term.name for term, (_, smiles) in zip(reactant_terms, reactant_resolved) if smiles is None]
        unresolved += [term.name for term, (_, smiles) in zip(product_terms, product_resolved) if smiles is None]
        if unresolved:
            joined = ", ".join(html.escape(name) for name in dict.fromkeys(unresolved))
            await status_msg.edit_text(app_module.NOT_FOUND_TEXT.format(name=joined), parse_mode="HTML")
            return

        reactant_pairs = [(coef, smiles) for coef, smiles in reactant_resolved]
        product_pairs = [(coef, smiles) for coef, smiles in product_resolved]
        original_balance = app_module.check_reaction_balance(reactant_pairs, product_pairs)
        balance = original_balance
        balance_notice = ""
        automatically_balanced = False

        if settings.auto_balance:
            if original_balance.get("checked") and original_balance.get("balanced"):
                balance_notice = "✅ Equation was already balanced; the entered coefficients were preserved."
            else:
                solution = balance_reaction_smiles(
                    [smiles for _, smiles in reactant_pairs],
                    [smiles for _, smiles in product_pairs],
                )
                if solution is not None:
                    left_coeffs, right_coeffs = solution
                    new_reactants = [(Fraction(c), smi) for c, (_, smi) in zip(left_coeffs, reactant_pairs)]
                    new_products = [(Fraction(c), smi) for c, (_, smi) in zip(right_coeffs, product_pairs)]
                    verified = app_module.check_reaction_balance(new_reactants, new_products)
                    if verified.get("checked") and verified.get("balanced"):
                        reactant_pairs, product_pairs = new_reactants, new_products
                        reactant_terms = [app_module.ReactionTerm(Fraction(c), t.name) for c, t in zip(left_coeffs, reactant_terms)]
                        product_terms = [app_module.ReactionTerm(Fraction(c), t.name) for c, t in zip(right_coeffs, product_terms)]
                        balance = verified
                        automatically_balanced = True
                        balance_notice = "✅ Equation automatically balanced by the bot using the smallest whole-number coefficients."
                    else:
                        balance_notice = "⚠️ Automatic balancing could not be independently verified; entered coefficients were retained."
                else:
                    balance_notice = "⚠️ No unique positive stoichiometric solution was found from the entered species; coefficients were retained."
        else:
            balance_notice = "ℹ️ Automatic balancing is disabled in your drawing settings."

        equation = app_module.format_equation(reactant_terms, product_terms)
        small_as_formula = settings.structure_style != "skeletal"

        final_hook = getattr(app_module, "_publication_final_reaction_hook", None)
        if callable(final_hook):
            try:
                final_hook(user_id, reactant_terms, product_terms, reactant_pairs, product_pairs, equation)
            except Exception as exc:
                app_module.chem_logger.warning("Could not store final reaction state: %s", exc)

        if not capacity.try_reserve():
            snap = capacity.capacity_snapshot()
            await status_msg.edit_text(
                "⏳ The free server is currently processing many chemistry jobs. "
                "Please retry shortly. Your request was rejected before rendering to protect the service from running out of memory."
            )
            app_module.chem_logger.warning("Chemistry render queue full: %s", snap)
            return

        try:
            left_key = _pairs_key(reactant_pairs)
            right_key = _pairs_key(product_pairs)
            common_key = (
                left_key,
                right_key,
                bool(small_as_formula),
                str(arrow_top),
                str(arrow_bottom),
                str(settings.arrow_style),
                bool(settings.transparent_background),
            )

            png_bytes = await capacity.run_cached_bytes(
                ("png",) + common_key,
                pub.render_reaction_png,
                reactant_pairs,
                product_pairs,
                small_as_formula=small_as_formula,
                arrow_top=arrow_top,
                arrow_bottom=arrow_bottom,
                arrow_style=settings.arrow_style,
                transparent_background=settings.transparent_background,
            )

            svg_bytes = None
            if settings.export_svg:
                svg_bytes = await capacity.run_cached_bytes(
                    ("svg",) + common_key,
                    pub.render_reaction_svg,
                    reactant_pairs,
                    product_pairs,
                    small_as_formula=small_as_formula,
                    arrow_top=arrow_top,
                    arrow_bottom=arrow_bottom,
                    arrow_style=settings.arrow_style,
                    transparent_background=settings.transparent_background,
                )

            cdxml_bytes = None
            if settings.export_cdxml:
                cdxml_bytes = await capacity.run_cached_bytes(
                    ("cdxml", left_key, right_key, str(arrow_top), str(arrow_bottom), str(settings.arrow_style)),
                    generate_reaction_cdxml,
                    reactant_pairs,
                    product_pairs,
                    arrow_top=arrow_top,
                    arrow_bottom=arrow_bottom,
                    arrow_style=settings.arrow_style,
                    title=equation,
                )

            rxn_file = None
            if settings.export_rxn:
                rxn_file = await capacity.run_heavy(
                    app_module.generate_rxn_file,
                    [app_module.ReactionTerm(c, s) for c, s in reactant_pairs],
                    [app_module.ReactionTerm(c, s) for c, s in product_pairs],
                    "Reaction.rxn",
                    equation,
                )
        finally:
            capacity.release()

        caption_lines = ["<b>Reaction scheme</b>", f"<code>{html.escape(equation)}</code>"]
        if automatically_balanced:
            caption_lines.append("<b>✅ Equation automatically balanced by the bot.</b>")
        elif balance_notice:
            caption_lines.append(html.escape(balance_notice))
        if arrow_top:
            caption_lines.append("Conditions: " + html.escape(arrow_top))
        if arrow_bottom:
            caption_lines.append("Additional conditions: " + html.escape(arrow_bottom))
        if settings.show_balance_report and balance.get("checked"):
            caption_lines.append(html.escape(balance["message"]))
        for note in (reactant_notes + product_notes)[:3]:
            caption_lines.append("ℹ️ " + html.escape(note))
        caption = "\n".join(caption_lines)

        if png_bytes:
            preview = app_module.bytes_to_named_bytesio(png_bytes, "Reaction_preview.png")
            await update.message.reply_photo(
                photo=preview,
                caption=caption,
                parse_mode="HTML",
                reply_markup=ui.welcome_keyboard(user_id),
            )
        if settings.export_png and png_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(png_bytes, "Reaction_600dpi.png"),
                caption="Publication PNG — 600 DPI",
            )
        if settings.export_svg and svg_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(svg_bytes, "Reaction_vector.svg"),
                caption="Publication vector SVG — recommended for manuscripts and graphical editing",
            )
        if settings.export_tiff and png_bytes:
            tiff_bytes = await capacity.run_heavy(_tiff_from_png, png_bytes, 600)
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(tiff_bytes, "Reaction_600dpi.tiff"),
                caption="Publication TIFF — 600 DPI (LZW)",
            )
        if settings.export_cdxml and cdxml_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(cdxml_bytes, "Reaction_editable.cdxml"),
                caption="Editable ChemDraw CDXML — prepared to be opened and edited directly in ChemDraw",
            )
        if settings.export_rxn and rxn_file:
            await update.message.reply_document(
                document=rxn_file,
                caption="MDL RXN compatibility file — optional interchange format",
            )
        await status_msg.delete()

    async def interactive_run_message(update, context) -> None:
        await app_module.handle_chemical_message(update, context)

    def run_chem_bot_interactive() -> None:
        if not app_module.CHEM_BOT_TOKEN:
            app_module.chem_logger.error("CRITICAL: CHEM_BOT_TOKEN is not set. Chemistry bot will NOT start.")
            return
        application = (
            Application.builder()
            .token(app_module.CHEM_BOT_TOKEN)
            .post_init(app_module._on_startup)
            .post_shutdown(app_module._on_shutdown)
            .build()
        )
        application.add_handler(CommandHandler(["start", "help"], interactive_start))
        application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^chem:"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, interactive_run_message))
        application.add_error_handler(app_module.error_handler)
        app_module.chem_logger.info("Interactive Chemistry Bot controls enabled with bounded Render-free chemistry executor: %s", capacity.capacity_snapshot())
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            stop_signals=None,
            close_loop=False,
        )

    app_module.chem_start_command = interactive_start
    app_module._handle_reaction = publication_handle_reaction_balanced
    app_module.run_chem_bot = run_chem_bot_interactive
    app_module.render_reaction_scheme = pub.render_reaction_scheme
    app_module.chem_logger.info(
        "Professional chemistry UI enabled: CDXML primary editable export, bounded CPU rendering, optional RXN/PNG/SVG/TIFF."
    )
