from __future__ import annotations

"""Advanced chemistry integration layer.

Adds conservative redox balancing, net-ionic reduction, atom mapping and
user-directed curved-arrow mechanisms on top of the interactive publication bot.
The legacy ORCA application remains untouched.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import advanced_chemistry_v2 as adv
import chemistry_ui as ui
import publication_entry_balanced as base


_LAST_REACTION: dict[int, dict] = {}


def _format_redox_with_names(result: adv.RedoxResult, reactant_terms, product_terms) -> str:
    def side(items, original_terms):
        out = []
        n_original = len(original_terms)
        for i, sp in enumerate(items):
            label = original_terms[i].name if i < n_original else sp.label
            prefix = "" if sp.coefficient == 1 else f"{sp.coefficient} "
            out.append(prefix + label)
        return " + ".join(out)
    return f"{side(result.reactants, reactant_terms)} → {side(result.products, product_terms)}"


def install_publication_renderer(app_module) -> None:
    base.install_publication_renderer(app_module)
    old_handle_reaction = app_module._handle_reaction

    async def capture_reaction(update, status_msg, text: str, separator: str, index: int) -> None:
        user_id = update.effective_user.id if update.effective_user else 0
        try:
            reaction_text, _, _ = __import__("publication_entry")._extract_conditions(text)
            sep_info = app_module.find_reaction_separator(reaction_text)
            if sep_info:
                sep, idx = sep_info
                if sep == ">>":
                    left, right = reaction_text[:idx], reaction_text[idx + 2:]
                else:
                    left, right = reaction_text[:idx], reaction_text[idx + 1:]
                reactant_terms = app_module.split_compound_terms(left)
                product_terms = app_module.split_compound_terms(right)
                rr, _ = await app_module._resolve_terms(reactant_terms)
                pr, _ = await app_module._resolve_terms(product_terms)
                if all(smi for _, smi in rr + pr):
                    _LAST_REACTION[int(user_id)] = {
                        "reactant_terms": reactant_terms,
                        "product_terms": product_terms,
                        "reactant_pairs": [(coef, smi) for coef, smi in rr],
                        "product_pairs": [(coef, smi) for coef, smi in pr],
                    }
        except Exception as exc:
            app_module.chem_logger.warning("Could not cache last reaction for advanced chemistry: %s", exc)
        await old_handle_reaction(update, status_msg, text, separator, index)

    async def advanced_start(update, context) -> None:
        user_id = update.effective_user.id if update.effective_user else 0
        asyncio.create_task(app_module.notify_admin(update, context))
        await update.message.reply_text(
            app_module.WELCOME_TEXT + "\n\n🧪 Advanced chemistry tools are available from the button below.",
            parse_mode="HTML",
            reply_markup=ui.welcome_keyboard(user_id),
        )

    async def _advanced_action(query, user_id: int, action: str) -> bool:
        if action == "advanced":
            await query.edit_message_text(ui.advanced_text(), parse_mode="HTML", reply_markup=ui.advanced_keyboard())
            return True
        if action == "mechanism_help":
            await query.edit_message_text(ui.mechanism_help_text(), parse_mode="HTML", reply_markup=ui.advanced_keyboard())
            return True
        if action not in {"redox_acidic", "redox_basic", "net_ionic", "atom_map"}:
            return False
        cached = _LAST_REACTION.get(int(user_id))
        if not cached:
            await query.message.reply_text("⚠️ Draw a reaction first, then use the Advanced Chemistry tools on that reaction.")
            return True

        reactant_pairs = cached["reactant_pairs"]
        product_pairs = cached["product_pairs"]
        reactant_smiles = [s for _, s in reactant_pairs]
        product_smiles = [s for _, s in product_pairs]

        if action.startswith("redox_"):
            medium = "acidic" if action.endswith("acidic") else "basic"
            result = adv.balance_redox(reactant_smiles, product_smiles, medium)
            if result is None:
                await query.message.reply_text(
                    "⚠️ No unique redox balance could be determined conservatively for this reaction in "
                    f"{medium} medium. The bot did not guess missing chemistry."
                )
            else:
                equation = _format_redox_with_names(result, cached["reactant_terms"], cached["product_terms"])
                await query.message.reply_text(
                    f"⚡ <b>Redox balance — {medium} medium</b>\n<code>{html.escape(equation)}</code>\n\n"
                    "✅ Balanced using only medium-appropriate H₂O/H⁺/OH⁻/e⁻ helper species.",
                    parse_mode="HTML",
                )
            return True

        if action == "net_ionic":
            result = adv.net_ionic_equation(reactant_pairs, product_pairs)
            if result is None:
                await query.message.reply_text(
                    "⚠️ A conservative net-ionic equation could not be produced. The bot dissociates only explicitly supported strong electrolytes and recognized ionic salts."
                )
            else:
                eq = adv.format_net_ionic(result)
                spectators = ", ".join(result.cancelled) if result.cancelled else "none"
                await query.message.reply_text(
                    f"🧂 <b>Net ionic equation</b>\n<code>{html.escape(eq)}</code>\n\n"
                    f"Cancelled spectator species: <code>{html.escape(spectators)}</code>",
                    parse_mode="HTML",
                )
            return True

        if action == "atom_map":
            mapped = adv.conservative_atom_map(reactant_smiles, product_smiles)
            if mapped is None:
                await query.message.reply_text(
                    "⚠️ Conservative atom mapping was not applied. It currently requires a one-reactant → one-product reaction with a unique maximum common substructure."
                )
            else:
                await query.message.reply_text(
                    "🔢 <b>Conservative atom-mapped reaction SMILES</b>\n"
                    f"<code>{html.escape(mapped)}</code>\n\n"
                    "Mapping was returned only because the common substructure match was unique.",
                    parse_mode="HTML",
                )
            return True
        return False

    async def settings_callback(update, context) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        user_id = update.effective_user.id if update.effective_user else 0
        data = query.data or ""
        action = data.split(":", 1)[1] if ":" in data else "settings"
        if await _advanced_action(query, user_id, action):
            return
        if action == "help":
            await query.edit_message_text(ui.help_text(), parse_mode="HTML", reply_markup=ui.settings_keyboard(ui.get_settings(user_id)))
            return
        if action in {"settings", "exports"}:
            settings = ui.get_settings(user_id)
        else:
            settings = ui.apply_action(user_id, action)
        await query.edit_message_text(ui.settings_text(settings), parse_mode="HTML", reply_markup=ui.settings_keyboard(settings))

    async def mechanism_command(update, context) -> None:
        raw = (update.message.text or "").strip()
        payload = raw.split(maxsplit=1)[1].strip() if len(raw.split(maxsplit=1)) > 1 else ""
        if not payload:
            await update.message.reply_text(ui.mechanism_help_text(), parse_mode="HTML")
            return
        if "||" in payload:
            smiles, spec = [p.strip() for p in payload.split("||", 1)]
            arrows = adv.parse_mechanism_arrows(spec)
            if arrows is None:
                await update.message.reply_text("⚠️ Invalid arrow syntax. Use forms such as <code>1-&gt;2,3~&gt;4</code>.", parse_mode="HTML")
                return
        else:
            smiles, arrows = payload, []
        svg = await asyncio.to_thread(adv.render_mechanism_svg, smiles, arrows)
        if svg is None:
            await update.message.reply_text("⚠️ The mechanism structure or atom indices could not be interpreted.")
            return
        caption = "Atom-numbered mechanism structure." if not arrows else "User-directed curved-arrow mechanism. No electron flow was inferred automatically."
        await update.message.reply_document(
            document=app_module.bytes_to_named_bytesio(svg, "Mechanism_vector.svg"),
            caption=caption,
        )
        if not arrows:
            await update.message.reply_text(
                "Now resend the command with explicit arrows, e.g. <code>/mechanism CCO || 1-&gt;2</code>.",
                parse_mode="HTML",
            )

    def run_chem_bot_advanced() -> None:
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
        application.add_handler(CommandHandler(["start", "help"], advanced_start))
        application.add_handler(CommandHandler("mechanism", mechanism_command))
        application.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^chem:"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, app_module.handle_chemical_message))
        application.add_error_handler(app_module.error_handler)
        app_module.chem_logger.info("Advanced Chemistry UI enabled: redox, net ionic, atom mapping, mechanisms.")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            stop_signals=None,
            close_loop=False,
        )

    app_module._handle_reaction = capture_reaction
    app_module.chem_start_command = advanced_start
    app_module.run_chem_bot = run_chem_bot_advanced
    app_module.chem_logger.info("Advanced chemistry engine installed conservatively without modifying ORCA/Flask.")
