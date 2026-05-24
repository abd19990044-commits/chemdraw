import os
import io
import re
import html
import logging
import asyncio
import urllib.request
import urllib.parse
from threading import Thread
from PIL import Image
from rdkit import Chem
from rdkit.Chem import AllChem, Draw, rdChemReactions
from flask import Flask
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ==========================================
# 1. إعدادات المراقبة والسجلات
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# 2. الإعدادات
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 7495822836

# ==========================================
# 3. خادم الويب (لإبقاء الخطة المجانية تعمل)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Chemistry Telegram Bot is Running on Render! 🧪🚀"

def run_flask():
    # Render تحدد المنفذ ديناميكياً، لذا نستدعيه من متغيرات البيئة
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# ==========================================
# 4. المنطق الكيميائي
# ==========================================
def fetch_smiles_sync(name):
    try:
        url = f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{urllib.parse.quote(name.strip())}/property/CanonicalSMILES/TXT"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode('utf-8').strip().split('\n')[0]
    except Exception as e:
        logger.error(f"PubChem API Error for {name}: {e}")
        return None

def get_mol_bytes(smiles, name):
    try:
        mol = Chem.MolFromSmiles(smiles)
        if not mol:
            return None
        AllChem.Compute2DCoords(mol)
        img = Draw.MolToImage(mol, size=(300, 300), legend=name)
        bio = io.BytesIO()
        img.save(bio, format='PNG')
        return bio.getvalue()
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return None

def create_grid_image(images_bytes_list):
    valid_images = [Image.open(io.BytesIO(img)) for img in images_bytes_list if img]
    if not valid_images:
        return None
    total_width = sum(img.width for img in valid_images)
    max_height = max(img.height for img in valid_images)
    grid_img = Image.new('RGB', (total_width, max_height), (255, 255, 255))
    x_offset = 0
    for img in valid_images:
        grid_img.paste(img, (x_offset, (max_height - img.height) // 2))
        x_offset += img.width
    bio = io.BytesIO()
    grid_img.save(bio, format='PNG')
    bio.seek(0)
    bio.name = "Reaction.png"
    return bio

def generate_rxn_file_sync(reaction_smiles, filename):
    try:
        rxn = AllChem.ReactionFromSmarts(reaction_smiles, useSmiles=True)
        if not rxn:
            return None
        rdChemReactions.Compute2DCoordsForReaction(rxn)
        rxn_block = rdChemReactions.ReactionToRxnBlock(rxn)
        rxn_file = io.BytesIO(rxn_block.encode('utf-8'))
        rxn_file.name = filename
        return rxn_file
    except:
        return None

def parse_chemical_names(compounds_str):
    parts = compounds_str.split('+')
    parsed_names = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^(?:\d+)?\s*(.+)$", part)
        if match:
            parsed_names.append(match.group(1).strip())
    return parsed_names

# ==========================================
# 5. دوال التلغرام
# ==========================================
async def notify_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        if user:
            username = f"@{user.username}" if user.username else "No username"
            text = (
                "Notification: New user activity\n\n"
                f"Name: {html.escape(user.full_name)}\n"
                f"Username: {html.escape(username)}\n"
                f"ID: <code>{user.id}</code>\n"
                f"Message: {html.escape(update.message.text or '')}"
            )
            await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except:
        pass

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"🚀 COMMAND RECEIVED: /start from User {update.effective_user.id}")
    asyncio.create_task(notify_admin(update, context))
    welcome_text = (
        "Welcome to the Chemical Bot! 🧪\n\n"
        "I can help you in two ways:\n\n"
        "1. For one compound: Send the name directly (e.g., Aspirin).\n"
        "2. For a full equation: Use '=' to separate "
        "(e.g., Methane + Oxygen = Carbon Dioxide + Water)."
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

async def handle_chemical_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    logger.info(f"📩 MESSAGE RECEIVED from {update.effective_user.id}: {text}")
    
    if not text:
        return

    asyncio.create_task(notify_admin(update, context))
    status_msg = await update.message.reply_text("Processing and performing chemical analysis... ⚙️")

    try:
        if '=' in text:
            parts = text.split('=')
            if len(parts) != 2:
                await status_msg.edit_text("Error: Use only one '=' sign.")
                return

            reactants = parse_chemical_names(parts[0])
            products = parse_chemical_names(parts[1])
            all_compounds = reactants + products

            smiles_results = await asyncio.gather(
                *(asyncio.to_thread(fetch_smiles_sync, name) for name in all_compounds)
            )
            smiles_map = dict(zip(all_compounds, smiles_results))

            if None in smiles_results:
                await status_msg.edit_text(
                    "Compound not found. Ensure you write the name in English correctly."
                )
                return

            reactants_bytes = await asyncio.gather(
                *(asyncio.to_thread(get_mol_bytes, smiles_map[n], f"Reactant: {n}") for n in reactants)
            )
            products_bytes = await asyncio.gather(
                *(asyncio.to_thread(get_mol_bytes, smiles_map[n], f"Product: {n}") for n in products)
            )
            final_grid_image = await asyncio.to_thread(
                create_grid_image, reactants_bytes + products_bytes
            )

            r_smiles_str = ".".join([smiles_map[n] for n in reactants])
            p_smiles_str = ".".join([smiles_map[n] for n in products])
            reaction_smiles = f"{r_smiles_str}>>{p_smiles_str}"
            rxn_file = await asyncio.to_thread(
                generate_rxn_file_sync, reaction_smiles, "Equation.rxn"
            )

            if final_grid_image:
                await update.message.reply_photo(
                    photo=final_grid_image, caption="Chemical reaction structure:"
                )
            if rxn_file:
                await update.message.reply_document(
                    document=rxn_file, caption="Reaction file (.rxn)"
                )

        else:
            compounds = parse_chemical_names(text)
            if not compounds:
                return

            name = compounds[0]
            smiles = await asyncio.to_thread(fetch_smiles_sync, name)

            if not smiles:
                await status_msg.edit_text(
                    "Compound not found. Ensure you write the name in English correctly."
                )
                return

            mol_bytes = await asyncio.to_thread(get_mol_bytes, smiles, name)
            rxn_file = await asyncio.to_thread(
                generate_rxn_file_sync,
                f"{smiles}>>",
                f"{name.replace(' ', '_')}.rxn"
            )

            if mol_bytes:
                bio_img = io.BytesIO(mol_bytes)
                bio_img.name = f"{name}.png"
                await update.message.reply_photo(
                    photo=bio_img,
                    caption=f"<b>{name}</b>\n<code>{smiles}</code>",
                    parse_mode="HTML"
                )
            if rxn_file:
                await update.message.reply_document(
                    document=rxn_file, caption="Compound file (.rxn)"
                )

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Error during processing: {e}")
        try:
            await status_msg.edit_text("A technical error occurred during processing.")
        except:
            pass

# ==========================================
# 6. معالج الأخطاء العالمي
# ==========================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, NetworkError):
        logger.warning(f"⚠️ Temporary network error: {context.error}. Bot will auto-retry.")
        return 
    logger.error(f"❌ Exception while handling an update:", exc_info=context.error)

# ==========================================
# 7. محرك التشغيل
# ==========================================
def main():
    if not BOT_TOKEN:
        logger.error("🚨 CRITICAL ERROR: Bot token not found! Please check Render Environment Variables.")
        return

    # 1. تشغيل خادم الويب الوهمي في الخلفية لإرضاء Render
    keep_alive()
    logger.info("✅ Web Server started. Render will be happy.")

    # 2. بناء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()

    # 3. إضافة المعالجات
    application.add_handler(CommandHandler(["start", "help"], start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chemical_message))
    application.add_error_handler(error_handler)

    # 4. بدء التشغيل
    logger.info("🚀 Bot is polling and ready to receive messages...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == '__main__':
    main()