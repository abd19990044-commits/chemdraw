from __future__ import annotations

import html
import io
import urllib.parse

from PIL import Image
from rdkit import Chem

import chemistry_ui as ui
import render_capacity as capacity
from cdxml_export import generate_single_compound_cdxml


def _png_with_dpi(png_bytes: bytes, dpi: int = 600) -> bytes:
    src = Image.open(io.BytesIO(png_bytes))
    out = io.BytesIO()
    src.save(out, format="PNG", dpi=(dpi, dpi))
    return out.getvalue()


def _tiff_from_png(png_bytes: bytes, dpi: int = 600) -> bytes:
    src = Image.open(io.BytesIO(png_bytes))
    out = io.BytesIO()
    src.save(out, format="TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
    return out.getvalue()


def _pubchem_namespace(app_module, raw: str) -> str:
    try:
        kind = app_module.parse_compound(raw).kind.name
    except Exception:
        kind = "NAME"
    if kind == "INCHI":
        return "inchi"
    if kind == "SMILES":
        return "smiles"
    return "name"


async def fetch_pubchem_3d_mol(app_module, raw: str, resolved_smiles: str) -> bytes | None:
    """Return a MOL block preserving PubChem's computed 3D conformer coordinates."""
    client_obj = app_module.pubchem_client
    if client_obj._client is None:
        await client_obj.start()
    client = client_obj._client
    if client is None:
        return None

    base = getattr(app_module, "PUBCHEM_BASE_URL", "https://pubchem.ncbi.nlm.nih.gov/rest/pug").rstrip("/")
    primary_ns = _pubchem_namespace(app_module, raw)
    candidates = [(primary_ns, raw.strip())]
    if resolved_smiles and (primary_ns != "smiles" or resolved_smiles != raw.strip()):
        candidates.append(("smiles", resolved_smiles))

    seen: set[tuple[str, str]] = set()
    for namespace, value in candidates:
        key = (namespace, value)
        if not value or key in seen:
            continue
        seen.add(key)
        encoded = urllib.parse.quote(value, safe="")
        url = f"{base}/compound/{namespace}/{encoded}/record/SDF?record_type=3d"
        try:
            async with client_obj._semaphore:
                response = await client.get(url)
            if response.status_code != 200 or not response.text.strip():
                continue
            mol_block = response.text.split("$$$$", 1)[0].strip() + "\n"
            mol = Chem.MolFromMolBlock(mol_block, removeHs=False, sanitize=True)
            if mol is None or mol.GetNumConformers() == 0:
                continue
            conf = mol.GetConformer()
            if not conf.Is3D():
                continue
            # Re-serialize only to normalize the MOL block; atom coordinates are
            # retained from the PubChem conformer and are not regenerated locally.
            out = Chem.MolToMolBlock(mol, confId=conf.GetId(), kekulize=False)
            return out.encode("utf-8")
        except Exception as exc:
            app_module.chem_logger.info("PubChem 3D lookup failed for '%s': %s", raw, exc)
    return None


def install_single_compound_pipeline(app_module) -> None:
    original_handle_single = app_module._handle_single_compound

    app_module.WELCOME_TEXT = (
        app_module.WELCOME_TEXT
        + "\n\n<b>Single compounds</b>\n"
        + "Send a chemical name, SMILES, InChI, or CAS RN. The bot returns a publication PNG and an editable CDXML file. "
        + "When PubChem provides a computed 3D conformer, the bot also imports its original X/Y/Z coordinates and sends a 3D MOL file."
    )

    async def handle_single_compound_publication(update, status_msg, text: str) -> None:
        terms = app_module.split_compound_terms(text)
        if len(terms) != 1:
            await original_handle_single(update, status_msg, text)
            return

        term = terms[0]
        name = term.name
        smiles, note = await app_module.resolve_species(name)
        if not smiles:
            await status_msg.edit_text(app_module.NOT_FOUND_TEXT.format(name=html.escape(name)), parse_mode="HTML")
            return

        user_id = update.effective_user.id if update.effective_user else None
        settings = ui.get_settings(user_id)
        if not capacity.try_reserve():
            await status_msg.edit_text(
                "⏳ The chemistry queue is full. Please retry shortly; the request was stopped before rendering to protect the free server."
            )
            return
        queue_position = capacity.queued_position()
        if queue_position > 0:
            try:
                await status_msg.edit_text(f"⏳ Your compound job is queued (approximately #{queue_position}).")
            except Exception:
                pass

        safe = app_module.safe_filename(name)
        png_bytes = svg_bytes = cdxml_bytes = tiff_bytes = None
        try:
            raw_png = await capacity.run_cached_bytes(
                ("single-png", smiles, name), app_module.render_molecule, smiles, name
            )
            if raw_png:
                png_bytes = await capacity.run_heavy(_png_with_dpi, raw_png, 600)

            if settings.export_svg:
                svg_bytes = await capacity.run_cached_bytes(
                    ("single-svg", smiles, name), app_module.render_molecule, smiles, name, app_module.MOL_IMAGE_SIZE, True
                )
            if settings.export_cdxml:
                cdxml_bytes = await capacity.run_cached_bytes(
                    ("single-cdxml", smiles), generate_single_compound_cdxml, smiles, name
                )
            if settings.export_tiff and png_bytes:
                tiff_bytes = await capacity.run_heavy(_tiff_from_png, png_bytes, 600)
        finally:
            capacity.release()

        # PubChem I/O is network-bound and uses its own bounded HTTP semaphore,
        # so it does not occupy the single CPU-heavy renderer worker.
        pubchem_3d = await fetch_pubchem_3d_mol(app_module, name, smiles)

        caption = f"<b>{html.escape(name)}</b>\n<code>{html.escape(smiles)}</code>"
        if note:
            caption += f"\nℹ️ {html.escape(note)}"
        if pubchem_3d:
            caption += "\n🧊 PubChem 3D conformer coordinates are available."

        if png_bytes:
            await update.message.reply_photo(
                photo=app_module.bytes_to_named_bytesio(png_bytes, f"{safe}_preview.png"),
                caption=caption,
                parse_mode="HTML",
                reply_markup=ui.welcome_keyboard(user_id),
            )
        if settings.export_png and png_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(png_bytes, f"{safe}_600dpi.png"),
                caption="Publication PNG — 600 DPI",
            )
        if settings.export_svg and svg_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(svg_bytes, f"{safe}_vector.svg"),
                caption="Publication vector SVG",
            )
        if settings.export_tiff and tiff_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(tiff_bytes, f"{safe}_600dpi.tiff"),
                caption="Publication TIFF — 600 DPI (LZW)",
            )
        if settings.export_cdxml and cdxml_bytes:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(cdxml_bytes, f"{safe}_editable.cdxml"),
                caption="Editable ChemDraw CDXML — 2D structure prepared for direct editing",
            )
        if pubchem_3d:
            await update.message.reply_document(
                document=app_module.bytes_to_named_bytesio(pubchem_3d, f"{safe}_PubChem_3D.mol"),
                caption="PubChem computed 3D conformer — original X/Y/Z coordinates preserved in MOL format",
            )
        else:
            await update.message.reply_text(
                "ℹ️ PubChem did not provide a 3D conformer for this identifier; the 2D publication outputs were generated normally."
            )
        await status_msg.delete()

    app_module._handle_single_compound = handle_single_compound_publication
