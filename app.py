"""
سكربت مدمج يشغّل بوتَي تلغرام مختلفَين معاً كعملية Python واحدة على خطة
Render المجانية (Web Service):

  1) بوت ORCA  — يولّد ملفات إدخال ORCA 6 عبر معالج تفاعلي (Wizard) بالأزرار.
     يعمل بأسلوب Webhook مبني يدوياً فوق Flask (بدون مكتبة python-telegram-bot).
  2) بوت الكيمياء (Chemistry Drawing Bot) — يرسم صيغاً وتفاعلات كيميائية 2D
     عبر RDKit ويستخدم مكتبة python-telegram-bot.
     يعمل بأسلوب Polling (لا يحتاج Webhook ولا منفذ خاص به).

فكرة الدمج:
-----------
خطة Render المجانية تسمح بفتح منفذ واحد (PORT) فقط لكل خدمة. لذلك:
  - بوت ORCA (Webhook) هو من يفتح ذلك المنفذ عبر خادم Flask (نفس آلية عمله
    الأصلية دون أي تغيير في المنطق أو السلوك).
  - بوت الكيمياء (Polling) يعمل في Thread خلفي منفصل (daemon thread) لا يفتح
    أي منفذ، لأن Polling أصلاً لا يحتاج طلبات واردة من Telegram — هو من يسأل
    Telegram باستمرار عن التحديثات الجديدة.
  - كل بوت احتفظ بكامل منطقه ومتغيراته الأصلية كما هي؛ التعديل الوحيد الذي
    طرأ هو تسمية بعض المتغيرات/الدوال المشتركة الاسم (BOT_TOKEN, logger,
    start_command, health, main) بأسماء مختلفة (ORCA_* و CHEM_*) لتفادي
    التعارض داخل نفس ملف بايثون، وإزالة خادم Flask الثانوي (keep-alive) من
    بوت الكيمياء لأنه أصبح غير ضروري (بوت ORCA يوفر بالفعل مسار /health).

متغيرات البيئة المطلوبة على Render (Dashboard > Environment):
  - ORCA_BOT_TOKEN         : توكن بوت ORCA (مطلوب لتشغيله)
  - CHEM_BOT_TOKEN         : توكن بوت الكيمياء (مطلوب لتشغيله)
  - ORCA_WEBHOOK_SECRET    : (اختياري لكن يُنصح به بشدة) سرّ حماية مسارات
                             /registerWebhook و/deleteWebhook الخاصة ببوت ORCA
  - ADMIN_ID               : (اختياري) آيدي أدمن بوت الكيمياء للإشعارات
  - RENDER_EXTERNAL_URL    : يوفّره Render تلقائياً، لا تضبطه يدوياً
  - PORT                   : يوفّره Render تلقائياً، لا تضبطه يدوياً

خطوات النشر على Render:
  1) ادفع هذا الملف (وملف requirements.txt) إلى مستودعك.
  2) أنشئ Web Service جديد على Render يشير إلى هذا المستودع.
  3) Start Command:  python app.py
  4) أضف متغيرات البيئة أعلاه من تبويب Environment.
  5) بعد أول نشر ناجح، افتح مرة واحدة فقط:
     https://<اسم-خدمتك>.onrender.com/registerWebhook?secret=<ORCA_WEBHOOK_SECRET>
     لتسجيل الـ Webhook الخاص ببوت ORCA لدى Telegram.
     (بوت الكيمياء لا يحتاج أي تسجيل — يبدأ Polling تلقائياً عند إقلاع العملية.)
"""
from __future__ import annotations

# ─────────────────────────────────────────────
# استيرادات مُجمَّعة من كلا البوتين (بدون تكرار)
# ─────────────────────────────────────────────
import asyncio
import html
import io
import json
import logging
import os
import re
import threading
from fractions import Fraction
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence

import httpx
from flask import Flask, request as flask_request
from rdkit import Chem
from rdkit.Chem import AllChem, rdChemReactions
from rdkit.Chem.Draw import rdMolDraw2D
from telegram import Update
from telegram.error import NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ─────────────────────────────────────────────
# إعداد تسجيل الأحداث (logging) — مشترك، مرة واحدة للملف كله
# ─────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════════
# ██  القسم الأول: بوت ORCA (Webhook عبر Flask)  ██
# ══════════════════════════════════════════════════════════════════════════

orca_logger = logging.getLogger('orca_bot')

# ─────────────────────────────────────────────
# متغيرات البيئة
# ─────────────────────────────────────────────
# على Render: أضف هذه من تبويب Environment في لوحة التحكم (Dashboard > Environment).
ORCA_BOT_TOKEN = (
    os.environ.get('ORCA_BOT_TOKEN')
    or os.environ.get('TELEGRAM_BOT_TOKEN')
    or os.environ.get('TG_BOT_TOKEN')
    or ''
)
ORCA_WEBHOOK_SECRET = os.environ.get('ORCA_WEBHOOK_SECRET', '')

# إصلاح أمني (S2): توقف مبكراً وبرسالة واضحة في السجلات إن كان التوكن مفقوداً،
# بدل أن تفشل كل طلبات Telegram لاحقاً برسائل غامضة مثل 401 Unauthorized.
if not ORCA_BOT_TOKEN:
    orca_logger.error(
        'ORCA_BOT_TOKEN غير موجود! أضف متغير بيئة باسم ORCA_BOT_TOKEN في إعدادات '
        'Render (Environment) قبل النشر. لن يعمل البوت بدونه.'
    )

# إصلاح أمني (S1): بدون ORCA_WEBHOOK_SECRET فإن أي شخص يعرف رابط الخدمة على Render
# يستطيع مناداة /registerWebhook أو /deleteWebhook وتعطيل البوت. يُنصح بشدة
# بتعيين ORCA_WEBHOOK_SECRET كنص عشوائي طويل (مثلاً بإخراج secrets.token_urlsafe(32)).
if not ORCA_WEBHOOK_SECRET:
    orca_logger.warning(
        'ORCA_WEBHOOK_SECRET غير مضبوط. مسارا /registerWebhook و/deleteWebhook '
        'سيبقيان بلا حماية إضافية. يُنصح بتعيين هذا المتغير في بيئة Render.'
    )

# Render يوفر هذا المتغير تلقائياً برابط الخدمة العام (مثل
# https://your-app.onrender.com) — لا حاجة لتخمين الدومين يدوياً.
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', '')

# ─────────────────────────────────────────────
# كاش بسيط في الذاكرة لبيانات المركّبات (TTL = 6 ساعات)
# ─────────────────────────────────────────────
_compound_cache: dict[str, tuple[datetime, tuple]] = {}
CACHE_TTL = timedelta(hours=6)
# إصلاح (L4): حد أقصى لعدد العناصر المخزّنة لأن كل عنصر قد يحوي bytes صورة PNG،
# وبدون حد أقصى يمكن أن تتضخم الذاكرة على خطة Render المجانية (512MB RAM) حتى
# يُعاد تشغيل الخدمة (OOM). عند تجاوز الحد نحذف أقدم عنصر.
CACHE_MAX_ITEMS = 50


def _cache_get(key: str):
    if key in _compound_cache:
        ts, value = _compound_cache[key]
        if datetime.now() - ts < CACHE_TTL:
            orca_logger.info(f'Cache HIT: {key}')
            return value
        del _compound_cache[key]
    return None


def _cache_set(key: str, value: tuple):
    if len(_compound_cache) >= CACHE_MAX_ITEMS:
        oldest_key = min(_compound_cache, key=lambda k: _compound_cache[k][0])
        del _compound_cache[oldest_key]
    _compound_cache[key] = (datetime.now(), value)


# ─────────────────────────────────────────────
# حالة المستخدم (Wizard state) — في الذاكرة فقط
# ─────────────────────────────────────────────
# إصلاح (C4): كان الكود الأصلي يقرأ/يكتب من Cloudflare KV أولاً، وKV تناسق
# نهائي (eventual consistency) وقد يستغرق نشر التحديث بين المراكز عدة ثوانٍ،
# مما كان يسبب ضياع خطوات في المعالج التفاعلي (wizard) عند الضغط السريع على
# الأزرار. على Render نحن ضمن عملية واحدة فقط، لذا dict عادي في الذاكرة كافٍ
# تماماً ولا يعاني من هذه المشكلة.
#
# القيد الوحيد المتبقي: إن "نامت" الخدمة (انظر ملاحظة sleep في نهاية الملف)
# أو أُعيد نشرها، تُفقد كل الجلسات المفتوحة. هذا مقبول لخطة مجانية؛ إن احتجت
# دوام الحالة عبر عمليات إعادة التشغيل مستقبلاً، أضف قاعدة بيانات خارجية
# بسيطة (مثل Redis المجاني على Upstash) بدل هذا الـ dict.
_state_cache: dict[str, dict[str, Any]] = {}


async def _load_user_data(chat_id: int) -> dict[str, Any]:
    key = f'user:{chat_id}'
    return _state_cache.get(key, {}).copy()


async def _save_user_data(chat_id: int, data: dict[str, Any]):
    key = f'user:{chat_id}'
    _state_cache[key] = data.copy()


async def _clear_user_data(chat_id: int):
    key = f'user:{chat_id}'
    _state_cache.pop(key, None)


# ─────────────────────────────────────────────
# HTTP client مشترك مع connection pooling
# ─────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            # إصلاح: كان الـ User-Agent السابق "Mozilla/5.0 ORCABot/3.0" يحوي
            # كلمة "Bot" صراحةً، وهي علامة يعتمد عليها كثير من أنظمة الحماية
            # ضد الزحف الآلي (WAF/anti-bot) لحظر أو تقييد الطلبات تلقائياً —
            # بصرف النظر عن صحة الطلب نفسه. هذا مرشّح قوي لتفسير ردود PubChem
            # غير JSON (200 بجسم غير صالح) التي نراها بثبات حتى بعد إعادة
            # المحاولة. استبدلناه بسلسلة متصفح حقيقية شائعة الاستخدام.
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                ),
                'Accept': 'application/json,text/html,*/*',
            },
            follow_redirects=True,
        )
    return _http_client


# ─────────────────────────────────────────────
# حلقة asyncio دائمة تعمل في Thread خلفي منفصل
# ─────────────────────────────────────────────
# السبب: Flask خادم متزامن (sync/WSGI) بينما كل منطق البوت async (httpx.AsyncClient،
# asyncio.gather لجلب Wikipedia وPubChem بالتوازي...). أبسط طريقة موثوقة لدمج
# الاثنين على Render هي: تشغيل event loop واحد دائم في Thread مستقل طوال عمر
# العملية، ثم من كل طلب Flask (متزامن) نُجدوِل الكوروتين على تلك الحلقة عبر
# asyncio.run_coroutine_threadsafe. هذا يضمن أن httpx.AsyncClient وكل الحالة
# الداخلية تبقى مرتبطة بحلقة واحدة ثابتة، بدل إنشاء/إغلاق حلقة جديدة (asyncio.run)
# في كل طلب وهو ما يكسر عملاء httpx غير المغلقين بشكل صحيح.
_background_loop: asyncio.AbstractEventLoop | None = None


def _start_background_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()

    def _runner():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_runner, name='bot-event-loop', daemon=True)
    t.start()
    return loop


def run_coro_in_background(coro):
    """يجدول كوروتين على حلقة asyncio الخلفية الدائمة ولا ينتظر انتهاءه
    (fire-and-forget). نستخدمها في مسار /telegram حتى نرد على Telegram بسرعة
    (200 OK) قبل انتهاء المعالجة الفعلية، بدل أن ينتظر Telegram معالجة كاملة
    قد تشمل عدة طلبات شبكة إلى PubChem/Wikipedia."""
    global _background_loop
    if _background_loop is None:
        _background_loop = _start_background_loop()
    return asyncio.run_coroutine_threadsafe(coro, _background_loop)


# ─────────────────────────────────────────────
# Flask — نقطة الدخول الفعلية على Render
# ─────────────────────────────────────────────
app = Flask(__name__)


def home_html() -> str:
    return '''
    <!DOCTYPE html>
    <html dir="rtl">
    <head>
        <meta charset="UTF-8">
        <title>ORCA Bot Server</title>
        <style>
            body { display: flex; justify-content: center; align-items: center; height: 100vh; background: #f0f2f5; font-family: sans-serif; }
            .container { text-align: center; background: white; padding: 2rem; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
            h2 { color: #1a73e8; }
            .status { margin-top: 15px; padding: 10px; background: #e6f4ea; color: #1e8e3e; border-radius: 5px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <h2>خادم ORCA Bot يعمل بنجاح 🟢</h2>
            <div class="status">السكربت والبوت يعملان عبر Webhook على Render</div>
        </div>
    </body>
    </html>
    '''


# ─────────────────────────────────────────────
# ثوابت الواجهة
# ─────────────────────────────────────────────
CALC_TYPES = {
    'Single Point (SP)': 'sp',
    'Geometry Optimization (Opt)': 'opt',
    'Opt + Frequencies (Opt Freq)': 'opt freq',
    'Transition State (OptTS)': 'optts',
    'TD-DFT (UV-Vis)': 'tddft',
    '⌨️ Custom Command (!...)': 'custom',
}

THEORY_FAMILIES = {
    'Composite Methods (r2SCAN-3C...)': 'f_comp',
    'DFT (B3LYP, wB97M-V...)': 'f_dft',
    'MP2 Variants': 'f_mp2',
    'CCSD / Highly Correlated': 'f_ccsd',
    'Hartree-Fock (HF)': 'f_hf',
}

COMPOSITE_METHODS = ['r2SCAN-3C', 'B97-3C', 'PBEh-3C']
DFT_FUNCTIONALS = ['B3LYP', 'CAM-B3LYP', 'PBE0', 'wB97M-V', 'M062X', 'wB97X-D4', 'PBE', 'BP86']
MP2_VARIANTS = ['MP2', 'RI-MP2  def2-tzvp/c', 'DLPNO-MP2  def2-tzvp/c']
CCSD_VARIANTS = ['CCSD', 'CCSD(T)', 'DLPNO-CCSD  def2-tzvp/c', 'DLPNO-CCSD(T) def2-tzvp/c']

RI_OPTIONS = {
    'No RI Acceleration': 'none',
    'RIJCOSX (For Hybrids)': 'rijcosx',
    'RIJK (For HF/DFT)': 'rijk',
    'RI (Standard RI)': 'ri',
}

BASIS_FAMILIES = {
    'Ahlrichs (def2)': 'bf_def2',
    'Dunning (cc-pV)': 'bf_dunning',
    'Pople (6-31G)': 'bf_pople',
}

BASIS_MAP = {
    'def2': ['def2-SVP', 'def2-TZVP', 'def2-TZVPD', 'ma-def2-TZVP', 'def2-TZVPP', 'def2-QZVP'],
    'dunning': ['cc-pVDZ', 'cc-pVTZ', 'aug-cc-pVTZ'],
    'pople': ['6-31G(d)', '6-311G(d,p)', '6-31+G(d,p)', '6-311++G(d,p)'],
}

DISPERSION_MODELS = {'None': 'none', 'D3': 'D3', 'D3BJ': 'D3bj', 'D4': 'D4'}
SOLVATION_MODELS = {'None (Gas)': 'none', 'CPCM': 'cpcm', 'SMD': 'smd'}
SOLVENTS = ['Water', 'Ethanol', 'Methanol', 'Acetone', 'Chloroform', 'DCM', 'DMSO', 'THF', 'Toluene']


# ─────────────────────────────────────────────
# طبقة Telegram API المباشرة (بديل polling/python-telegram-bot)
# ─────────────────────────────────────────────
class TelegramAPI:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    @property
    def base_url(self) -> str:
        return f'https://api.telegram.org/bot{self.bot_token}'

    @property
    def file_base_url(self) -> str:
        return f'https://api.telegram.org/file/bot{self.bot_token}'

    async def request(self, method: str, payload: dict[str, Any] | None = None, files: dict[str, Any] | None = None):
        client = await get_http_client()
        url = f'{self.base_url}/{method}'
        if files:
            resp = await client.post(url, data=payload or {}, files=files)
        else:
            resp = await client.post(url, json=payload or {})
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok', False):
            raise RuntimeError(data)
        return data['result']

    async def get_file(self, file_id: str):
        result = await self.request('getFile', {'file_id': file_id})
        return FileProxy(self, result['file_path'])

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None, reply_to_message_id: int | None = None):
        payload: dict[str, Any] = {'chat_id': chat_id, 'text': text}
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        if parse_mode:
            payload['parse_mode'] = parse_mode
        if reply_to_message_id is not None:
            payload['reply_to_message_id'] = reply_to_message_id
        return await self.request('sendMessage', payload)

    async def send_photo(self, chat_id: int, photo: bytes, caption: str | None = None):
        payload: dict[str, Any] = {'chat_id': str(chat_id)}
        if caption:
            payload['caption'] = caption
        return await self.request('sendPhoto', payload, files={'photo': ('image.png', photo, 'image/png')})

    async def send_document(self, chat_id: int, document: bytes, file_name: str, caption: str | None = None):
        payload: dict[str, Any] = {'chat_id': str(chat_id)}
        if caption:
            payload['caption'] = caption
        return await self.request('sendDocument', payload, files={'document': (file_name, document, 'application/octet-stream')})

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None):
        payload: dict[str, Any] = {'chat_id': chat_id, 'message_id': message_id, 'text': text}
        if reply_markup is not None:
            payload['reply_markup'] = reply_markup
        if parse_mode:
            payload['parse_mode'] = parse_mode
        return await self.request('editMessageText', payload)

    async def delete_message(self, chat_id: int, message_id: int):
        return await self.request('deleteMessage', {'chat_id': chat_id, 'message_id': message_id})

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None):
        payload: dict[str, Any] = {'callback_query_id': callback_query_id}
        if text:
            payload['text'] = text
        return await self.request('answerCallbackQuery', payload)

    async def set_webhook(self, url: str, secret_token: str | None = None):
        payload: dict[str, Any] = {
            'url': url,
            'drop_pending_updates': True,
            'allowed_updates': ['message', 'callback_query'],
            'max_connections': 40,  # مخفّضة عن 100 الأصلية لتناسب موارد الخطة المجانية المحدودة على Render
        }
        if secret_token:
            payload['secret_token'] = secret_token
        return await self.request('setWebhook', payload)

    async def delete_webhook(self):
        return await self.request('deleteWebhook', {'drop_pending_updates': True})


class FileProxy:
    def __init__(self, bot: TelegramAPI, file_path: str):
        self.bot = bot
        self.file_path = file_path

    async def download_as_bytes(self) -> bytes:
        client = await get_http_client()
        resp = await client.get(f'{self.bot.file_base_url}/{self.file_path}')
        resp.raise_for_status()
        return resp.content


class ChatProxy:
    def __init__(self, chat_id: int):
        self.id = chat_id


class SentMessageProxy:
    def __init__(self, bot: TelegramAPI, chat_id: int, message_id: int):
        self._bot = bot
        self.chat = ChatProxy(chat_id)
        self.message_id = message_id

    async def delete(self):
        await self._bot.delete_message(self.chat.id, self.message_id)

    async def edit_text(self, text: str, reply_markup: dict | None = None, parse_mode: str | None = None):
        await self._bot.edit_message_text(self.chat.id, self.message_id, text, reply_markup=reply_markup, parse_mode=parse_mode)


class DocumentProxy:
    def __init__(self, file_id: str, file_name: str):
        self.file_id = file_id
        self.file_name = file_name


class MessageProxy:
    def __init__(self, bot: TelegramAPI, message: dict[str, Any]):
        self._bot = bot
        self._raw = message
        self.chat = ChatProxy(message['chat']['id'])
        self.message_id = message['message_id']
        self.text = message.get('text')
        document = message.get('document')
        self.document = DocumentProxy(document['file_id'], document.get('file_name', 'file')) if document else None

    async def reply_text(self, text: str, reply_markup: dict | None = None, parse_mode: str | None = None):
        result = await self._bot.send_message(self.chat.id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        return SentMessageProxy(self._bot, self.chat.id, result['message_id'])

    async def reply_photo(self, photo: bytes, caption: str | None = None):
        result = await self._bot.send_photo(self.chat.id, photo, caption=caption)
        return SentMessageProxy(self._bot, self.chat.id, result['message_id'])

    async def reply_document(self, document: io.BytesIO, caption: str | None = None):
        content = document.getvalue() if hasattr(document, 'getvalue') else document.read()
        filename = getattr(document, 'name', 'document.bin')
        result = await self._bot.send_document(self.chat.id, content, filename, caption=caption)
        return SentMessageProxy(self._bot, self.chat.id, result['message_id'])

    async def delete(self):
        await self._bot.delete_message(self.chat.id, self.message_id)

    async def edit_text(self, text: str, reply_markup: dict | None = None, parse_mode: str | None = None):
        await self._bot.edit_message_text(self.chat.id, self.message_id, text, reply_markup=reply_markup, parse_mode=parse_mode)


class CallbackQueryProxy:
    def __init__(self, bot: TelegramAPI, callback_query: dict[str, Any]):
        self._bot = bot
        self._raw = callback_query
        self.id = callback_query['id']
        self.data = callback_query.get('data', '')
        # إصلاح (C3): بعض تحديثات callback_query لا تحوي 'message' إطلاقاً
        # (مثلاً عند الرد على رسالة قديمة جداً أو inline message). الكود الأصلي
        # كان يفترض وجودها دوماً ويرمي KeyError، مما يُسقط /telegram بخطأ 500
        # ويجعل Telegram يعيد إرسال نفس التحديث إلى ما لا نهاية.
        raw_message = callback_query.get('message')
        self.message = MessageProxy(bot, raw_message) if raw_message else None

    async def answer(self):
        # إصلاح (L5): استعلامات callback القديمة (>~a few seconds) قد تُرجع
        # خطأ "query is too old" من Telegram. لا داعي لإسقاط كل المعالجة بسببها.
        try:
            await self._bot.answer_callback_query(self.id)
        except Exception as e:
            orca_logger.warning(f'answer_callback_query فشل (على الأرجح استعلام منتهي الصلاحية): {e}')

    async def edit_message_text(self, text: str, reply_markup: dict | None = None, parse_mode: str | None = None):
        if self.message is None:
            orca_logger.warning('تعذّر تعديل الرسالة: الرسالة الأصلية غير متاحة في هذا التحديث.')
            return
        await self.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


class UpdateProxy:
    def __init__(self, bot: TelegramAPI, update: dict[str, Any]):
        self._raw = update
        self.message = MessageProxy(bot, update['message']) if 'message' in update else None
        self.callback_query = CallbackQueryProxy(bot, update['callback_query']) if 'callback_query' in update else None
        if self.message:
            self.effective_chat = self.message.chat
        elif self.callback_query and self.callback_query.message:
            self.effective_chat = self.callback_query.message.chat
        else:
            self.effective_chat = None


class ContextProxy:
    def __init__(self, bot: TelegramAPI, user_data: dict[str, Any]):
        self.bot = bot
        self.user_data = user_data


# ─────────────────────────────────────────────
# توليد ملف ORCA
# ─────────────────────────────────────────────
# ملاحظة مهمة جداً (بناءً على طلب صريح من المستخدم):
# تم *إلغاء* أي "تحسين هندسي" تلقائي كان مقترحاً في مرحلة التشخيص (مثل تبديل
# def2-* إلى x2c-*all تلقائياً عند اختيار X2C، أو إضافة RIJCOSX/def2-J تلقائياً
# مع MP2/CCSD، أو إضافة %geom Calc_Hess true تلقائياً مع OptTS، أو فرض
# TightSCF/UKS تلقائياً). الدالة تولّد **بالضبط** ما اختاره المستخدم عبر
# الأزرار/الأمر المخصص، دون أي تعديل صامت على الكيمياء الحاسوبية من خلف
# ظهره. إن رغبتَ لاحقاً بإضافة أي من هذه المساعدات، يفضّل أن تكون اختيارية
# وواضحة للمستخدم (مثلاً سؤال منفصل: "أضف Calc_Hess true؟ نعم/لا") بدل
# حقنها تلقائياً.
def generate_orca_6_input(d: dict):
    input_text = '# ORCA 6.1 Input generated by ORCA Bot\n'

    if 'custom_line' in d:
        input_text += f"{d['custom_line']}\n\n"
    else:
        calc_cmd = {'sp': 'SP', 'opt': 'Opt', 'opt freq': 'Opt Freq', 'optts': 'OptTS', 'tddft': 'SP'}.get(d.get('calc_type', 'sp'), 'SP')
        solv_str = f"{d['solv_model'].upper()}({d.get('solvent', 'Water')})" if d.get('solv_model', 'none') != 'none' else ''
        x2c = 'X2C' if d.get('x2c') else ''
        acc_part = f"{d['ri_type'].upper()} Autoaux" if d.get('ri_type', 'none') != 'none' else ''
        disp_part = d['disp'].upper() if d.get('disp', 'none') != 'none' else ''
        method = d.get('theory', '')
        basis = d.get('basis', '') if d.get('family') != 'f_comp' else ''

        keywords = ['!', method, basis, disp_part, acc_part, solv_str, calc_cmd, x2c]
        keywords = [k for k in keywords if str(k).strip()]
        input_text += ' '.join(keywords) + '\n\n'

    input_text += f"%pal nprocs {d.get('cores', 4)} end\n"
    input_text += f"%maxcore {d.get('ram', 6000)}\n\n"

    if d.get('calc_type') == 'tddft':
        input_text += f"%tddft\n   nroots {d.get('nroots', 10)}\nend\n\n"

    input_text += f"* xyz {d.get('charge', 0)} {d.get('mult', 1)}\n"
    input_text += f"{d.get('coords', '')}\n"
    input_text += '*\n'
    return input_text


def sanitize_filename(name: str) -> str:
    """إصلاح أمني (S3): اسم المركّب/الملف قادم من إدخال المستخدم ويُستخدم مباشرة
    كاسم ملف مُرسَل عبر Telegram. ننظّفه من أي محارف قد تسبب مشاكل (مسارات،
    اقتباسات...) ونحدّ طوله."""
    cleaned = re.sub(r'[^\w\-.]', '_', name).strip('_')
    return cleaned[:60] or 'molecule'


# ─────────────────────────────────────────────
# طلبات الشبكة — async بالكامل
# ─────────────────────────────────────────────
async def get_wikipedia_summary_async(compound_name: str) -> str:
    try:
        client = await get_http_client()
        search_name = urllib.parse.quote(compound_name.replace(' ', '_'))
        url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{search_name}'
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('type') != 'disambiguation':
                extract = data.get('extract', '')
                if extract:
                    match = re.search(r'^(.*?)\.', extract)
                    return f"📖 <b>Wikipedia:</b> {match.group(1)}." if match else f"📖 <b>Wikipedia:</b> {extract}"
    except Exception as e:
        orca_logger.error(f'Wikipedia Error: {e}')
    return ''


def _extract_solubility_recursive(node, results):
    if isinstance(node, dict):
        if 'TOCHeading' in node and ('Solubility' in node['TOCHeading'] or 'Water Solubility' in node['TOCHeading']):
            if 'Information' in node:
                for info in node['Information']:
                    if 'Value' in info and 'StringWithMarkup' in info['Value']:
                        for st in info['Value']['StringWithMarkup']:
                            if 'String' in st:
                                text = st['String']
                                if text not in results:
                                    results.append(text)
        for value in node.values():
            _extract_solubility_recursive(value, results)
    elif isinstance(node, list):
        for item in node:
            _extract_solubility_recursive(item, results)


async def get_solubility_async(cid: int) -> str:
    try:
        client = await get_http_client()
        url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON/'
        resp = await client.get(url)
        if resp.status_code == 200:
            sol_texts: list[str] = []
            _extract_solubility_recursive(resp.json(), sol_texts)
            if sol_texts:
                return '\n'.join([f'  🔹 {s}' for s in sol_texts[:3]])
    except Exception as e:
        orca_logger.error(f'Solubility Error: {e}')
    return '  🔹 (Not available)'


class PubChemUnavailableError(Exception):
    """يُرفع عندما يفشل الاتصال بـ PubChem لسبب غير "المركّب غير موجود" الحقيقي
    (حظر مؤقت للـ IP، صفحة HTML بدل JSON، خطأ خادم 5xx، ...). نفرّق بينه وبين
    "غير موجود" الحقيقية (404 أو نتيجة فارغة) حتى لا نعرض على المستخدم رسالة
    مضلِّلة توحي بأن اسم المركّب خاطئ بينما المشكلة فعلياً من جهة الخادم/الشبكة."""


# إصلاح جذري: تبيّن بالاختبار الفعلي أن طلب PubChem المُجمَّع (خصائص متعددة
# CanonicalSMILES,IUPACName,MolecularFormula,MolecularWeight,Title بصيغة
# JSON عبر compound/name/.../property/.../JSON) يفشل بثبات من هذا الخادم
# تحديداً (يردّ 200 لكن بجسم غير قابل للتحليل كـ JSON)، بينما بوت الكيمياء
# الآخر في نفس الملف ينجح بلا مشاكل في الاتصال بـ PubChem لنفس المركّب. الفرق
# الوحيد: بوت الكيمياء يطلب خاصية واحدة فقط بصيغة TXT في كل طلب
# (compound/name/.../property/X/TXT) بدل خاصية متعددة مجمّعة بصيغة JSON.
# بدل تخمين أي جزء من الطلب المُجمَّع هو المُعطِّل (قائمة الخصائص؟ صيغة
# الإخراج؟ حجم الرد؟)، استبدلنا الطلب المُجمَّع بعدة طلبات TXT بسيطة صغيرة
# تُنفَّذ بالتوازي — وهو *بالضبط* نمط الطلب المُثبَت نجاحه فعلياً على هذا
# الخادم بالذات لنفس هذا المركّب. CanonicalSMILES حُذفت لأنها لم تكن
# تُستخدَم أصلاً في أي مكان لاحق بالكود (بيانات مجلوبة دون استخدام)، وTitle
# حُذفت لأنها كانت مجرد احتياط ثانوي بعد IUPACName أصلاً.
async def _pubchem_txt_property(client: httpx.AsyncClient, encoded_name: str, prop: str) -> str | None:
    url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_name}/property/{prop}/TXT'
    resp = await client.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        orca_logger.warning(
            f'PubChem TXT property "{prop}" failed: {resp.status_code} — body[:150]={resp.text[:150]!r}'
        )
        raise PubChemUnavailableError(f'HTTP {resp.status_code} (property {prop})')
    return resp.text.strip() or None


async def _pubchem_cid_by_name(client: httpx.AsyncClient, encoded_name: str) -> int | None:
    url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{encoded_name}/cids/TXT'
    resp = await client.get(url)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        orca_logger.warning(
            f'PubChem CID lookup failed: {resp.status_code} — body[:150]={resp.text[:150]!r}'
        )
        raise PubChemUnavailableError(f'HTTP {resp.status_code} (cids)')
    text = resp.text.strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    try:
        return int(first_line)
    except ValueError:
        orca_logger.warning(f'PubChem CID lookup returned non-numeric body: {first_line!r}')
        return None


async def _fetch_pubchem_compound_payload(name: str) -> dict[str, Any] | None:
    client = await get_http_client()
    encoded = urllib.parse.quote(name)

    # الطلبات الأربعة تُنفَّذ بالتوازي بنفس أسلوب TXT المُثبَت نجاحه. أي فشل
    # مؤقت (حظر/جسم غير صالح) في أيّ منها يرفع PubChemUnavailableError، والتي
    # يلتقطها fetch_compound_async لإعادة المحاولة أو إظهار رسالة واضحة
    # للمستخدم — دون تغيير في تلك الطبقة.
    cid, iupac_name, mol_formula, mol_weight = await asyncio.gather(
        _pubchem_cid_by_name(client, encoded),
        _pubchem_txt_property(client, encoded, 'IUPACName'),
        _pubchem_txt_property(client, encoded, 'MolecularFormula'),
        _pubchem_txt_property(client, encoded, 'MolecularWeight'),
    )

    if cid is None:
        # هذه هي حالة "غير موجود" الحقيقية: PubChem لا يعرف مركّباً بهذا الاسم.
        return None

    return {
        'CID': cid,
        'IUPACName': iupac_name,
        'MolecularFormula': mol_formula,
        'MolecularWeight': mol_weight,
    }


async def _download_pubchem_png(cid: int) -> bytes | None:
    try:
        client = await get_http_client()
        url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG?record_type=2d&image_size=large'
        resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as e:
        orca_logger.error(f'PNG download error: {e}')
    return None


async def _download_pubchem_sdf(cid: int) -> str | None:
    client = await get_http_client()
    for record_type in ('3d', '2d'):
        try:
            url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/SDF?record_type={record_type}'
            resp = await client.get(url)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
        except Exception as e:
            orca_logger.error(f'SDF download error ({record_type}): {e}')
    return None


def _mol_block_to_xyz(mol_block: str) -> str | None:
    try:
        lines = mol_block.splitlines()
        if len(lines) < 4:
            return None

        count_idx = None
        atom_count = None
        for idx in range(min(6, len(lines))):
            m = re.match(r'^\s*(\d+)\s+(\d+)\s+', lines[idx])
            if m:
                atom_count = int(m.group(1))
                count_idx = idx
                break

        if atom_count is None or count_idx is None:
            return None

        atom_lines = lines[count_idx + 1: count_idx + 1 + atom_count]
        coords: list[str] = []
        for line in atom_lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            x, y, z = parts[0], parts[1], parts[2]
            symbol = parts[3]
            coords.append(f'{symbol} {x} {y} {z}')

        return '\n'.join(coords) if coords else None
    except Exception as e:
        orca_logger.error(f'MOL parse error: {e}')
        return None


async def fetch_compound_async(name: str) -> tuple:
    cached = _cache_get(name.lower())
    if cached:
        return cached

    try:
        # إصلاح: كانت أي مشكلة اتصال/حظر مؤقت من PubChem تُعرَض للمستخدم فوراً
        # كـ "not found"، مما يوحي خطأً بأن اسم المركّب غير صحيح. الآن نميّز
        # هذه الحالة (PubChemUnavailableError) ونعيد المحاولة مرة واحدة بعد
        # تأخير قصير (حظر PubChem المؤقت لبعض عناوين IP المشتركة على استضافات
        # مثل Render غالباً ما يزول خلال ثوانٍ)، قبل الاستسلام برسالة واضحة.
        try:
            payload = await _fetch_pubchem_compound_payload(name)
        except PubChemUnavailableError:
            orca_logger.info(f'Retrying PubChem lookup for "{name}" after transient failure...')
            await asyncio.sleep(1.5)
            try:
                payload = await _fetch_pubchem_compound_payload(name)
            except PubChemUnavailableError as e2:
                return (
                    None, None, None,
                    'PubChem service is temporarily unavailable or blocking this server\'s '
                    f'requests ({e2}). This is usually not an issue with the compound name — '
                    'please try again in a minute.',
                )

        if not payload:
            return (None, None, None, f"Compound '{name}' not found in PubChem.")

        cid = payload.get('CID')
        iupac_name = payload.get('IUPACName') or name
        mol_formula = payload.get('MolecularFormula', '')
        mol_weight = payload.get('MolecularWeight', '')

        sdf_text = await _download_pubchem_sdf(cid) if cid else None
        if not sdf_text:
            return (None, None, None, '3D/2D structure download failed.')

        coords = _generate_3d_coords(sdf_text)
        if coords is None:
            return (None, None, None, '3D embedding / coordinate extraction failed.')

        wiki_text, sol_text = await asyncio.gather(
            _wiki_with_fallback(name, iupac_name),
            get_solubility_async(cid) if cid else asyncio.sleep(0, result='  🔹 (Not available)'),
        )

        props_text = (
            f'🧪 <b>Compound:</b> {iupac_name or name}\n'
            f'📝 <b>Formula:</b> {mol_formula}\n'
            f'⚖️ <b>Weight:</b> {mol_weight} g/mol\n\n'
            f'💧 <b>Solubility:</b>\n{sol_text}\n\n'
            f'{wiki_text}'
        )

        image_data = await _download_pubchem_png(cid) if cid else None

        result = (coords, image_data, props_text, None)
        _cache_set(name.lower(), result)
        return result

    except Exception as e:
        orca_logger.error(f'fetch_compound_async error: {e}')
        return (None, None, None, str(e))


async def _wiki_with_fallback(name: str, iupac_name: str | None) -> str:
    wiki = await get_wikipedia_summary_async(name)
    if not wiki and iupac_name:
        wiki = await get_wikipedia_summary_async(iupac_name)
    return wiki


def _generate_3d_coords(mol_2d) -> str | None:
    try:
        if isinstance(mol_2d, str):
            text = mol_2d.strip()
            if not text:
                return None
            xyz = _normalize_xyz_text(text)
            if xyz:
                return xyz
            return _mol_block_to_xyz(text)
        return None
    except Exception as e:
        orca_logger.error(f'3D generation error: {e}')
        return None


def _normalize_xyz_text(text: str) -> str | None:
    try:
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() != '']
        if not lines:
            return None

        if lines[0].strip().isdigit() and len(lines) >= 2:
            coords = lines[2:]
        else:
            coords = lines

        parsed: list[str] = []
        for line in coords:
            parts = line.split()
            if len(parts) >= 4:
                parsed.append(f'{parts[0]} {parts[1]} {parts[2]} {parts[3]}')
        return '\n'.join(parsed) if parsed else None
    except Exception:
        return None


def get_xyz_from_file(raw: bytes | str, ext: str):
    """إصلاح (C1): كانت تُستدعى عبر asyncio.to_thread وهو ما لا يعمل على
    Pyodide/Cloudflare Workers. على Render هذا لم يعد مشكلة تقنية، لكن بما أن
    الدالة خفيفة جداً (مجرد تحليل نص)، إنشاء thread منفصل لها كان عبئاً غير
    ضروري أصلاً. تُستدعى الآن مباشرة بشكل متزامن."""
    try:
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode('utf-8', errors='ignore')
        else:
            text = str(raw)

        if ext.lower() == '.xyz':
            xyz = _normalize_xyz_text(text)
            if xyz:
                return xyz, None, None, None
            return None, None, None, 'Invalid XYZ file.'
        elif ext.lower() in ['.sdf', '.mol']:
            xyz = _mol_block_to_xyz(text)
            if xyz:
                return xyz, None, None, None
            return None, None, None, 'Invalid SDF/MOL file'
        return None, None, None, 'Unsupported extension.'
    except Exception as e:
        return None, None, None, str(e)


# ─────────────────────────────────────────────
# Markup builder
# ─────────────────────────────────────────────
# إصلاح (L7): لم تكن هذه الدالة تحتاج أن تكون async أصلاً (لا await حقيقي
# بداخلها). حُوّلت إلى دالة متزامنة عادية، وأُزيلت كل كلمات await المرافقة
# لاستدعاءاتها في كل أنحاء الملف (17 موضعاً).
def build_markup(options, prefix, row_w=1):
    keyboard = []
    current_row = []
    items = list(options.items()) if isinstance(options, dict) else options
    for item in items:
        label = item[0] if isinstance(options, dict) else item
        val = item[1] if isinstance(options, dict) else item
        current_row.append({'text': label, 'callback_data': f'{prefix}_{val}'})
        if len(current_row) == row_w:
            keyboard.append(current_row)
            current_row = []
    if current_row:
        keyboard.append(current_row)
    keyboard.append([{'text': '❌ Cancel', 'callback_data': 'cancel_op'}])
    return {'inline_keyboard': keyboard}


# ─────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────
async def start_command(update, context):
    context.user_data.clear()
    instructions = (
        'Welcome to ORCA input file generator.\n\n'
        'Instructions for use:\n'
        '1. Search by Name: Send # followed by the English name (e.g., #benzene).\n'
        '2. Upload Files: Send a coordinate file (SDF, MOL, or XYZ).\n\n'
        'You can configure step-by-step or provide a Custom Command Line.'
    )
    await update.message.reply_text(instructions)


async def handle_name(update, context):
    name = update.message.text[1:].strip()
    wait_msg = await update.message.reply_text(f'⏳ Processing `{name}`...\nFetching properties and definitions...')

    coords, image_data, props_text, err = await fetch_compound_async(name)
    await wait_msg.delete()

    if coords:
        if image_data:
            await update.message.reply_photo(photo=image_data)
        if props_text:
            await update.message.reply_text(props_text, parse_mode='HTML')

        context.user_data['coords'] = coords
        context.user_data['name'] = sanitize_filename(name)

        markup = build_markup(CALC_TYPES, 'calc')
        await update.message.reply_text('Step 1: Task:', reply_markup=markup)
    else:
        await update.message.reply_text(f'❌ Error: {err}')


async def handle_file(update, context):
    document = update.message.document
    ext = os.path.splitext(document.file_name)[1].lower()

    if ext == '.inp':
        return

    if ext in ['.sdf', '.mol', '.xyz']:
        wait_msg = await update.message.reply_text('⏳ Processing uploaded file...')
        try:
            file = await context.bot.get_file(document.file_id)
            raw_bytes = await file.download_as_bytes()
            coords, _, _, err = get_xyz_from_file(raw_bytes, ext)

            if coords:
                await wait_msg.delete()
                context.user_data['coords'] = coords
                context.user_data['name'] = sanitize_filename(os.path.splitext(document.file_name)[0])
                markup = build_markup(CALC_TYPES, 'calc')
                await update.message.reply_text('Step 1: Task:', reply_markup=markup)
            else:
                await wait_msg.edit_text(f'❌ Failed to parse file: {err}')
        except Exception as e:
            await wait_msg.edit_text(f'❌ Error processing file: {e}')
    else:
        await update.message.reply_text('Error: Unsupported format.')


async def callback_handler(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'cancel_op':
        context.user_data.clear()
        await query.edit_message_text('❌ Operation Cancelled. Send a new name or file.')
        return

    if not context.user_data.get('coords'):
        if query.message:
            await query.message.reply_text('Session expired. Please start over.')
        return

    ud = context.user_data

    try:
        if data.startswith('calc_'):
            calc_val = data.split('_', 1)[1]
            if calc_val == 'custom':
                ud['state'] = 'waiting_for_custom_line'
                await query.edit_message_text('⌨️ Please send your ORCA keywords starting with `!`\nExample: `! B3LYP def2-TZVP Opt`')
            else:
                ud['calc_type'] = calc_val
                markup = build_markup(THEORY_FAMILIES, 'fam')
                await query.edit_message_text('Step 2: Theory Family:', reply_markup=markup)

        elif data.startswith('fam_'):
            fam = data.split('_', 1)[1]
            ud['family'] = fam
            if fam == 'f_comp':
                markup = build_markup(COMPOSITE_METHODS, 'meth', 2)
                await query.edit_message_text('Select Method:', reply_markup=markup)
            elif fam == 'f_dft':
                markup = build_markup(DFT_FUNCTIONALS, 'meth', 2)
                await query.edit_message_text('Select Functional:', reply_markup=markup)
            elif fam == 'f_mp2':
                markup = build_markup(MP2_VARIANTS, 'meth', 1)
                await query.edit_message_text('Select MP2 Branch:', reply_markup=markup)
            elif fam == 'f_ccsd':
                markup = build_markup(CCSD_VARIANTS, 'meth', 1)
                await query.edit_message_text('Select CCSD Branch:', reply_markup=markup)
            elif fam == 'f_hf':
                ud['theory'] = 'HF'
                markup = build_markup(DISPERSION_MODELS, 'disp', 2)
                await query.edit_message_text('Select Dispersion:', reply_markup=markup)

        elif data.startswith('meth_'):
            ud['theory'] = data.split('_', 1)[1]
            if ud.get('family') == 'f_dft' and 'DLPNO' not in ud.get('theory', ''):
                markup = build_markup(DISPERSION_MODELS, 'disp', 2)
                await query.edit_message_text('Select Dispersion:', reply_markup=markup)
            elif 'DLPNO' in ud.get('theory', '') or ud.get('family') == 'f_comp':
                ud['ri_type'] = 'none'
                ud['disp'] = 'none'
                if ud.get('family') == 'f_comp':
                    markup = build_markup(SOLVATION_MODELS, 'solv')
                    await query.edit_message_text('Step 4: Solvation:', reply_markup=markup)
                else:
                    markup = build_markup(BASIS_FAMILIES, 'bfam')
                    await query.edit_message_text('Step 3: Basis Set Family:', reply_markup=markup)
            else:
                ud['disp'] = 'none'
                markup = build_markup(RI_OPTIONS, 'ri')
                await query.edit_message_text('Step 3: Acceleration:', reply_markup=markup)

        elif data.startswith('disp_'):
            ud['disp'] = data.split('_', 1)[1]
            markup = build_markup(RI_OPTIONS, 'ri')
            await query.edit_message_text('Step 3: Acceleration:', reply_markup=markup)

        elif data.startswith('ri_'):
            ud['ri_type'] = data.split('_', 1)[1]
            markup = build_markup(BASIS_FAMILIES, 'bfam')
            await query.edit_message_text('Step 3: Basis Set Family:', reply_markup=markup)

        elif data.startswith('bfam_'):
            bf = data.split('_', 1)[1].replace('bf_', '')
            markup = build_markup(BASIS_MAP[bf], 'basis', 2)
            await query.edit_message_text('Select Basis Set:', reply_markup=markup)

        elif data.startswith('basis_'):
            ud['basis'] = data.split('_', 1)[1]
            markup = build_markup(SOLVATION_MODELS, 'solv')
            await query.edit_message_text('Step 4: Solvation Model:', reply_markup=markup)

        elif data.startswith('solv_'):
            model = data.split('_', 1)[1]
            ud['solv_model'] = model
            if model == 'none':
                await ask_charge(query)
            else:
                markup = build_markup(SOLVENTS, 'solvent', 2)
                await query.edit_message_text('Select Solvent:', reply_markup=markup)

        elif data.startswith('solvent_'):
            ud['solvent'] = data.split('_', 1)[1]
            await ask_charge(query)

        elif data.startswith('rel_'):
            ud['x2c'] = (data.split('_', 1)[1] == 'yes')
            await query.edit_message_text('Configuration saved. Proceeding to parameters.')
            ud['state'] = 'waiting_for_charge'
            await context.bot.send_message(query.message.chat.id, 'Step 5: Enter Molecule Charge (e.g. 0):')

    except Exception as e:
        orca_logger.error(f'Callback error: {e}')


async def ask_charge(query):
    keyboard = [
        [
            {'text': 'Yes (X2C)', 'callback_data': 'rel_yes'},
            {'text': 'No', 'callback_data': 'rel_no'},
        ],
        [{'text': '❌ Cancel', 'callback_data': 'cancel_op'}],
    ]
    await query.edit_message_text('Enable X2C?', reply_markup={'inline_keyboard': keyboard})


def _parse_int_in_range(text: str, min_value: int, max_value: int) -> int | None:
    """إصلاح (L2): الكود الأصلي كان يستخدم text.lstrip('-').isdigit() الذي
    يقبل مدخلات غير سليمة (مثل '--5' أو '5-') ولا يفرض حدوداً عليا منطقية.
    هذه دالة تحقق موحّدة تُستخدم لكل حقول الأرقام (الشحنة، التعددية، الأنوية،
    الذاكرة، nroots)."""
    try:
        value = int(text)
    except ValueError:
        return None
    if value < min_value or value > max_value:
        return None
    return value


async def handle_text_input(update, context):
    ud = context.user_data
    if 'state' not in ud:
        return

    text = update.message.text.strip()
    state = ud['state']

    if state == 'waiting_for_custom_line':
        if not text.startswith('!'):
            await update.message.reply_text('⚠️ Custom line must start with `!`. Try again or type /start to cancel.')
            return
        ud['custom_line'] = text
        ud['state'] = 'waiting_for_charge'
        await update.message.reply_text('Step 5: Enter Molecule Charge (e.g. 0):')

    elif state == 'waiting_for_charge':
        charge = _parse_int_in_range(text, -10, 10)
        if charge is None:
            await update.message.reply_text('⚠️ Please enter a valid integer for Charge between -10 and 10 (e.g. 0, 1, -1):')
            return
        ud['charge'] = charge
        ud['state'] = 'waiting_for_multiplicity'
        await update.message.reply_text('Step 6: Enter Multiplicity (e.g. 1):')

    elif state == 'waiting_for_multiplicity':
        mult = _parse_int_in_range(text, 1, 20)
        if mult is None:
            await update.message.reply_text('⚠️ Please enter a valid positive integer for Multiplicity between 1 and 20 (e.g. 1, 2, 3):')
            return
        ud['mult'] = mult
        # إصلاح (L3): كان الكود يكتب nroots في ملف TD-DFT (generate_orca_6_input)
        # لكن لا توجد أي خطوة تسأل المستخدم عنها فعلياً — كانت تُستخدم دائماً
        # القيمة الافتراضية 10 بصمت. أضفنا خطوة صريحة فقط عندما تكون المهمة TD-DFT.
        if ud.get('calc_type') == 'tddft':
            ud['state'] = 'waiting_for_nroots'
            await update.message.reply_text('Step 6b: Enter number of excited states (nroots, e.g. 10):')
        else:
            ud['state'] = 'waiting_for_cores'
            await update.message.reply_text('Step 7: Enter number of processor cores (e.g. 4):')

    elif state == 'waiting_for_nroots':
        nroots = _parse_int_in_range(text, 1, 200)
        if nroots is None:
            await update.message.reply_text('⚠️ Please enter a valid integer for nroots between 1 and 200 (e.g. 10):')
            return
        ud['nroots'] = nroots
        ud['state'] = 'waiting_for_cores'
        await update.message.reply_text('Step 7: Enter number of processor cores (e.g. 4):')

    elif state == 'waiting_for_cores':
        # حد أقصى 128 نواة — حماية منطقية بسيطة، لا علاقة لها بموارد Render نفسها
        # (ملف .inp يُنتَج فقط ولا يُشغَّل هنا).
        cores = _parse_int_in_range(text, 1, 128)
        if cores is None:
            await update.message.reply_text('⚠️ Please enter a valid positive integer for Cores between 1 and 128 (e.g. 4):')
            return
        ud['cores'] = cores
        ud['state'] = 'waiting_for_ram'
        await update.message.reply_text('Step 8: Enter memory per core in MB (e.g. 6000):')

    elif state == 'waiting_for_ram':
        ram = _parse_int_in_range(text, 100, 64000)
        if ram is None:
            await update.message.reply_text('⚠️ Please enter a valid integer for RAM in MB between 100 and 64000 (e.g. 6000):')
            return
        ud['ram'] = ram

        inp = generate_orca_6_input(ud)
        file_name = f"{ud.get('name', 'molecule')}_6.inp"

        bio = io.BytesIO(inp.encode('utf-8'))
        bio.name = file_name
        await update.message.reply_document(document=bio, caption='✅ ORCA input file generated successfully.')

        ud.clear()


# ─────────────────────────────────────────────
# توجيه التحديثات
# ─────────────────────────────────────────────
async def on_shutdown():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        orca_logger.info('HTTP client closed.')


def hmac_compare(a: str, b: str) -> bool:
    try:
        import hmac
        return hmac.compare_digest(a, b)
    except Exception:
        return a == b


async def _register_webhook(webhook_url: str, bot: TelegramAPI) -> dict:
    secret = ORCA_WEBHOOK_SECRET or None
    result = await bot.set_webhook(webhook_url, secret_token=secret)
    return {'ok': True, 'result': result}


async def _delete_webhook(bot: TelegramAPI) -> dict:
    result = await bot.delete_webhook()
    return {'ok': True, 'result': result}


async def _dispatch_update(bot: TelegramAPI, raw_update: dict[str, Any]):
    """إصلاح (C3): كل هذه الدالة أصبحت الآن محمية بـ try/except من الطرف
    الخارجي (انظر مسار /telegram أسفل الملف) حتى لا يؤدي أي تحديث غير متوقع
    (مثل callback_query بلا message) إلى انهيار المعالجة بالكامل أو ترك
    مستخدم آخر بلا رد."""
    try:
        update = UpdateProxy(bot, raw_update)
    except Exception as e:
        orca_logger.error(f'فشل تفسير التحديث الوارد من Telegram: {e}\nRaw update: {raw_update}')
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        orca_logger.warning('تحديث بلا chat_id قابل للتحديد — تم تجاهله.')
        return

    user_data = await _load_user_data(chat_id)
    context = ContextProxy(bot, user_data)

    try:
        if update.message and update.message.text == '/start':
            await start_command(update, context)
        # إصلاح (L1): كان الشرط '== /start' حرفياً فقط، فلا يعمل عند إضافة
        # اسم البوت تلقائياً في الدردشات الجماعية مثل '/start@MyOrcaBot'.
        elif update.message and update.message.text and re.match(r'^/start(@\w+)?$', update.message.text):
            await start_command(update, context)
        elif update.message and re.match(r'^\s*#', update.message.text or ''):
            await handle_name(update, context)
        elif update.message and update.message.document:
            await handle_file(update, context)
        elif update.callback_query:
            await callback_handler(update, context)
        elif update.message and update.message.text:
            await handle_text_input(update, context)
    except Exception as e:
        orca_logger.error(f'خطأ غير متوقع أثناء معالجة التحديث: {e}')
    finally:
        try:
            await _save_user_data(chat_id, context.user_data)
        except Exception as e:
            orca_logger.error(f'فشل حفظ حالة المستخدم {chat_id}: {e}')


# ─────────────────────────────────────────────
# مسارات Flask (نقطة الدخول الحقيقية على Render)
# ─────────────────────────────────────────────
def _make_bot() -> TelegramAPI:
    return TelegramAPI(ORCA_BOT_TOKEN)


def _check_secret(req) -> bool:
    """يتحقق من ?secret=... في الرابط لحماية /registerWebhook و/deleteWebhook (S1)."""
    if not ORCA_WEBHOOK_SECRET:
        # لا يوجد سر مضبوط أصلاً؛ نُبقي المسار مفتوحاً لكن مع تحذير مسبق في
        # السجلات عند إقلاع التطبيق (انظر أعلى الملف). يُفضّل بشدة ضبط السر.
        return True
    provided = req.args.get('secret', '')
    return hmac_compare(provided, ORCA_WEBHOOK_SECRET)


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def catch_all(path):
    # يلتقط أي مسار غير معرّف صراحة أدناه ويعرض صفحة الحالة، ما عدا المسارات
    # المسجّلة بشكل صريح (Flask يعطي الأولوية للمسارات الأكثر تحديداً).
    return home_html()


@app.route('/health')
def orca_health():
    return home_html()


@app.route('/registerWebhook')
def register_webhook_route():
    if not _check_secret(flask_request):
        return 'Forbidden', 403
    if not ORCA_BOT_TOKEN:
        return 'ORCA_BOT_TOKEN is not configured on the server.', 500

    base_url = RENDER_EXTERNAL_URL or flask_request.host_url.rstrip('/')
    webhook_url = f'{base_url}/telegram'
    bot = _make_bot()
    future = run_coro_in_background(_register_webhook(webhook_url, bot))
    try:
        result = future.result(timeout=15)
    except Exception as e:
        return json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), 500, {'content-type': 'application/json; charset=utf-8'}
    return json.dumps(result, ensure_ascii=False), 200, {'content-type': 'application/json; charset=utf-8'}


@app.route('/deleteWebhook')
def delete_webhook_route():
    if not _check_secret(flask_request):
        return 'Forbidden', 403
    bot = _make_bot()
    future = run_coro_in_background(_delete_webhook(bot))
    try:
        result = future.result(timeout=15)
    except Exception as e:
        return json.dumps({'ok': False, 'error': str(e)}, ensure_ascii=False), 500, {'content-type': 'application/json; charset=utf-8'}
    return json.dumps(result, ensure_ascii=False), 200, {'content-type': 'application/json; charset=utf-8'}


@app.route('/telegram', methods=['POST'])
def telegram_webhook_route():
    if ORCA_WEBHOOK_SECRET:
        header = flask_request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not header or not hmac_compare(header, ORCA_WEBHOOK_SECRET):
            return 'Forbidden', 403

    try:
        update = flask_request.get_json(force=True, silent=False)
    except Exception:
        return 'Bad Request', 400

    if not isinstance(update, dict):
        return 'Bad Request', 400

    bot = _make_bot()
    # نُجدوِل المعالجة على الحلقة الخلفية ونعيد 200 فوراً لـ Telegram بدل
    # انتظار انتهاء المعالجة الكاملة (التي قد تشمل عدة طلبات شبكة بطيئة إلى
    # PubChem/Wikipedia). هذا يمنع Telegram من اعتبار الطلب "بطيئاً" وإعادة
    # إرسال نفس التحديث.
    run_coro_in_background(_dispatch_update(bot, update))
    return 'OK', 200


# ─────────────────────────────────────────────
# التشغيل على Render
# ─────────────────────────────────────────────
# Render يمرّر رقم المنفذ المطلوب فتح الاستماع عليه عبر متغير البيئة PORT.
# أمر البدء الموصى به في إعدادات Render (Start Command):
#     python app.py
#
# ملاحظة عن "النوم" (Sleep) على الخطة المجانية:
# خدمات Render المجانية (Web Service) "تنام" تلقائياً بعد ~15 دقيقة من عدم
# وصول أي طلب HTTP. وصول رسالة Webhook من Telegram يُعتبر طلب HTTP فيوقظ
# الخدمة، لكن أول رسالة بعد النوم ستأخذ تأخيراً إضافياً (Cold Start قد يصل
# لعشرات الثواني) قبل أن يرد البوت. لتقليل هذا التأخير (اختياري) يمكن ضبط
# خدمة مراقبة خارجية مجانية (مثل UptimeRobot) لتستدعي المسار /health كل
# 10-14 دقيقة فتُبقي الخدمة مستيقظة باستمرار.
def run_orca_flask_server():
    """يشغّل خادم Flask الخاص ببوت ORCA (Webhook) في الـ Thread الرئيسي.
    هذا هو الخادم الذي يفتح المنفذ الذي يطلبه Render، لذا لا حاجة لخادم Flask
    منفصل لبوت الكيمياء (انظر نهاية الملف المدمج)."""
    port = int(os.environ.get('PORT', 10000))
    orca_logger.info(f'بدء تشغيل خادم Flask (بوت ORCA) على المنفذ {port} (وضع Render).')
    orca_logger.info(
        'بعد أول نشر ناجح، افتح مرة واحدة فقط: '
        f'{RENDER_EXTERNAL_URL or "https://<اسم-خدمتك>.onrender.com"}/registerWebhook'
        f'{"?secret=<ORCA_WEBHOOK_SECRET>" if ORCA_WEBHOOK_SECRET else ""} '
        'لتسجيل الـ Webhook لدى Telegram (بوت ORCA فقط).'
    )
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ══════════════════════════════════════════════════════════════════════════
# ██  القسم الثاني: بوت الكيمياء - Chemistry Drawing Bot (Polling)  ██
# ══════════════════════════════════════════════════════════════════════════
#
# هذا القسم يستخدم محرك الرسم الكيميائي المدمج من chem_core.py.
# لا توجد حاجة لملف chem_core.py منفصل: منطق parsing، الرسم، الموازنة،
# وإنشاء/فحص ملفات RXN موجود هنا داخل app.py، بينما بقي PubChem async
# كما كان في النسخة الأصلية حتى لا نغيّر معمارية Render/Telegram.

CHEM_BOT_TOKEN: Optional[str] = os.environ.get("CHEM_BOT_TOKEN")
ADMIN_ID: int = int(os.environ.get("ADMIN_ID", "7495822836"))

PUBCHEM_BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUBCHEM_TIMEOUT_SECONDS = 10.0
PUBCHEM_MAX_CONCURRENT_REQUESTS = 4
PUBCHEM_CACHE_MAX_ENTRIES = 512

MOL_IMAGE_SIZE = (560, 420)
REACTION_SUBIMAGE_SIZE = (420, 340)
DRAWING_BOND_LINE_WIDTH = 2
DRAWING_FONT_SCALE = 0.85

logging.getLogger("telegram").setLevel(logging.WARNING)
chem_logger = logging.getLogger("chem_bot")


# ============================================================================
# 1. Reaction/input parsing — integrated from chem_core.py
# ============================================================================
_COEFFICIENT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s+(.+)$")
_BARE_FORMULA_RE = re.compile(r"^(?:\([A-Za-z0-9]+\)\d*|[A-Z][a-z]?\d*)+$")
_SMILES_CHAR_RE = re.compile(r"^[A-Za-z0-9@+\-\[\]\(\)=#$:/\\%.]+$")
_SMILES_BOND_HINT_RE = re.compile(r"[=#@\[\]\(\)]")


class InputKind(Enum):
    NAME = auto()
    SMILES = auto()
    INCHI = auto()


@dataclass(frozen=True)
class ParsedCompound:
    raw: str
    kind: InputKind


@dataclass(frozen=True)
class ReactionTerm:
    coefficient: Fraction
    name: str

    @property
    def pretty_coefficient(self) -> str:
        if self.coefficient == 1:
            return ""
        if self.coefficient.denominator == 1:
            return str(self.coefficient.numerator)
        return f"{self.coefficient.numerator}/{self.coefficient.denominator}"


def parse_coefficient(text: str) -> tuple[Fraction, str]:
    """Parse 2 H2O, 2H2O, 0.5 O2 and 1/2 O2 without corrupting names."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not raw:
        return Fraction(1), ""

    m = re.match(r"^(\d+)\s*/\s*(\d+)\s+(.+)$", raw)
    if m and int(m.group(2)) != 0:
        return Fraction(int(m.group(1)), int(m.group(2))), m.group(3).strip()

    m = re.match(r"^(\d+(?:\.\d+)?)\s+(.+)$", raw)
    if m:
        return Fraction(m.group(1)), m.group(2).strip()

    m = re.match(r"^(\d+)([A-Z(].*)$", raw)
    if m and _BARE_FORMULA_RE.match(m.group(2)):
        return Fraction(int(m.group(1))), m.group(2).strip()

    return Fraction(1), raw


def split_compound_terms(compounds_str: str) -> list[ReactionTerm]:
    """Split a '+'-joined side while preserving stoichiometric coefficients."""
    terms: list[ReactionTerm] = []
    for part in (compounds_str or "").split("+"):
        coefficient, name = parse_coefficient(part)
        if name:
            terms.append(ReactionTerm(coefficient, name))
    return terms


def split_compound_list(compounds_str: str) -> List[str]:
    """Backward-compatible helper: return only compound names."""
    return [term.name for term in split_compound_terms(compounds_str)]


def format_equation(reactant_terms: list[ReactionTerm], product_terms: list[ReactionTerm]) -> str:
    def side(terms):
        return " + ".join((f"{t.pretty_coefficient} {t.name}").strip() for t in terms)
    return f"{side(reactant_terms)} -> {side(product_terms)}"


def scale_terms_to_integers(*sides: list[ReactionTerm]) -> tuple[int, list[list[ReactionTerm]]]:
    """Scale fractional stoichiometry to integers for MDL RXN representation."""
    denominators = [t.coefficient.denominator for side in sides for t in side] or [1]
    factor = 1
    for d in denominators:
        factor = factor * d // __import__('math').gcd(factor, d)
    scaled = [[ReactionTerm(t.coefficient * factor, t.name) for t in side] for side in sides]
    return factor, scaled


def detect_input_kind(token: str) -> InputKind:
    stripped = token.strip()
    if stripped.upper().startswith("INCHI="):
        return InputKind.INCHI
    if " " not in stripped and _SMILES_CHAR_RE.match(stripped):
        has_digit = any(ch.isdigit() for ch in stripped)
        if _SMILES_BOND_HINT_RE.search(stripped) or has_digit:
            return InputKind.SMILES
    return InputKind.NAME


def parse_compound(token: str) -> ParsedCompound:
    return ParsedCompound(raw=token, kind=detect_input_kind(token))


def safe_filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
    return cleaned[:60] or "molecule"


# ============================================================================
# 2. PubChem client — original async client retained
# ============================================================================
class _LRUCache:
    def __init__(self, max_entries: int) -> None:
        self._max_entries = max_entries
        self._data: "OrderedDict[str, Optional[str]]" = OrderedDict()

    def get(self, key: str) -> Optional[str]:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: str, value: Optional[str]) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self._max_entries:
            self._data.popitem(last=False)


class PubChemClient:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(PUBCHEM_MAX_CONCURRENT_REQUESTS)
        self._cache = _LRUCache(PUBCHEM_CACHE_MAX_ENTRIES)

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=PUBCHEM_TIMEOUT_SECONDS,
                headers={"User-Agent": "ChemistryTelegramBot/2.1"},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_canonical_smiles_by_name(self, name: str) -> Optional[str]:
        return await self._resolve("name", name)

    async def get_canonical_smiles_by_inchi(self, inchi: str) -> Optional[str]:
        return await self._resolve("inchi", inchi)

    async def _resolve(self, namespace: str, value: str) -> Optional[str]:
        cache_key = f"{namespace}:{value.lower().strip()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._client is None:
            await self.start()
        encoded = urllib.parse.quote(value.strip(), safe="")
        url = f"{PUBCHEM_BASE_URL}/compound/{namespace}/{encoded}/property/CanonicalSMILES/TXT"
        async with self._semaphore:
            try:
                assert self._client is not None
                response = await self._client.get(url)
                response.raise_for_status()
                smiles = response.text.strip().splitlines()[0].strip()
                self._cache.set(cache_key, smiles)
                return smiles
            except httpx.HTTPStatusError as exc:
                chem_logger.info("PubChem could not resolve '%s' (%s): %s", value, namespace, exc.response.status_code)
            except (httpx.HTTPError, IndexError) as exc:
                chem_logger.warning("PubChem request failed for '%s' (%s): %s", value, namespace, exc)
        self._cache.set(cache_key, None)
        return None


pubchem_client = PubChemClient()


# ============================================================================
# 3. Local formula/ambiguity handling — integrated from chem_core.py
# ============================================================================
AMBIGUOUS_TOKENS = {
    "CO": "carbon monoxide", "NO": "nitric oxide", "CS": "carbon monosulfide",
    "NC": "cyanide", "OS": "osmium", "SN": "tin", "PS": "phosphorus sulfide",
    "CN": "cyanide", "NS": "nitrogen sulfide", "SO": "sulfur monoxide",
    "BN": "boron nitride", "SI": "silicon", "NI": "nickel", "SC": "scandium",
}

_CONVENTIONAL_FORMULAS = {
    "O=O": "O2", "[H][H]": "H2", "N#N": "N2", "ClCl": "Cl2", "BrBr": "Br2",
    "II": "I2", "FF": "F2", "[O][O]": "O2", "O=[O+][O-]": "O3",
    "O=C=O": "CO2", "[C-]#[O+]": "CO", "O=S=O": "SO2", "O=S(=O)=O": "SO3",
    "[N]=O": "NO", "[O-][N+]=O": "NO2", "[N-]=[N+]=O": "N2O",
    "O": "H2O", "OO": "H2O2", "N": "NH3", "S": "H2S", "C": "CH4",
    "Cl": "HCl", "Br": "HBr", "I": "HI", "F": "HF", "P": "PH3", "[SiH4]": "SiH4",
    "OS(=O)(=O)O": "H2SO4", "O[N+](=O)[O-]": "HNO3", "OP(=O)(O)O": "H3PO4",
    "OC(=O)O": "H2CO3", "OS(=O)O": "H2SO3", "N#C": "HCN",
    "[OH-]": "OH-", "[H+]": "H+", "[NH4+]": "NH4+",
    "[Na+].[Cl-]": "NaCl", "[Na+].[OH-]": "NaOH", "[K+].[OH-]": "KOH",
    "[Ca+2].[O-2]": "CaO", "[Na+].[Na+].[O-]C([O-])=O": "Na2CO3",
    "ClC(Cl)Cl": "CHCl3", "ClC(Cl)(Cl)Cl": "CCl4",
}


def _formula_lookup_table():
    table = {}
    for smiles, formula in _CONVENTIONAL_FORMULAS.items():
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            table.setdefault(formula.upper(), Chem.MolToSmiles(mol))
    for alias, formula in (("H2O2", "H2O2"), ("HOH", "H2O"), ("NH3", "NH3"),
                           ("CH3OH", "CH4O"), ("HO-", "OH-"), ("OH", "OH-"),
                           ("NAOH", "NAOH"), ("NACL", "NACL")):
        if formula.upper() in table:
            table.setdefault(alias.upper(), table[formula.upper()])
    return table


_SMILES_BY_COMMON_FORMULA = _formula_lookup_table()


def smiles_from_common_formula(token: str) -> str | None:
    return _SMILES_BY_COMMON_FORMULA.get((token or "").strip().upper())


async def resolve_species(token: str) -> tuple[str | None, str]:
    raw = (token or "").strip()
    if not raw:
        return None, ""

    if raw.upper() in AMBIGUOUS_TOKENS and raw == raw.upper():
        meaning = AMBIGUOUS_TOKENS[raw.upper()]
        smiles = await pubchem_client.get_canonical_smiles_by_name(meaning)
        if smiles:
            return smiles, (
                f"'{raw}' was read as the formula for {meaning}. "
                f"Write it as SMILES if you meant a different structure."
            )

    formula_smiles = smiles_from_common_formula(raw)
    if formula_smiles:
        return formula_smiles, ""

    parsed = parse_compound(raw)
    if parsed.kind is InputKind.SMILES:
        mol = Chem.MolFromSmiles(parsed.raw)
        if mol is not None:
            return Chem.MolToSmiles(mol), ""
        smiles = await pubchem_client.get_canonical_smiles_by_name(parsed.raw)
        return smiles, ""

    if parsed.kind is InputKind.INCHI:
        smiles = await pubchem_client.get_canonical_smiles_by_inchi(parsed.raw)
        return smiles, ""

    # For names, PubChem remains the primary resolver.
    smiles = await pubchem_client.get_canonical_smiles_by_name(parsed.raw)
    if smiles:
        return smiles, ""

    # Final local fallback catches simple SMILES such as CCO/CCC that the old
    # detector intentionally classified as names.
    mol = Chem.MolFromSmiles(raw)
    if mol is not None:
        return Chem.MolToSmiles(mol), f"'{raw}' was interpreted as a SMILES string."
    return None, ""


async def resolve_compound_to_smiles(token: str) -> Optional[str]:
    smiles, _ = await resolve_species(token)
    return smiles


# ============================================================================
# 4. 2D drawing — integrated from chem_core.py
# ============================================================================
def _apply_common_draw_options(drawer) -> None:
    opts = drawer.drawOptions()
    opts.addStereoAnnotation = True
    opts.bondLineWidth = DRAWING_BOND_LINE_WIDTH
    opts.baseFontSize = DRAWING_FONT_SCALE * (opts.baseFontSize or 0.6)
    opts.padding = 0.12
    opts.legendFontSize = 18


def render_molecule(smiles: str, legend: str = "", size=MOL_IMAGE_SIZE, as_svg: bool = False) -> Optional[bytes]:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        AllChem.Compute2DCoords(mol)
        Chem.rdDepictor.StraightenDepiction(mol)
    except Exception as exc:
        chem_logger.debug("2D coordinate refinement skipped: %s", exc)
    drawer_cls = rdMolDraw2D.MolDraw2DSVG if as_svg else rdMolDraw2D.MolDraw2DCairo
    drawer = drawer_cls(size[0], size[1])
    _apply_common_draw_options(drawer)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol, legend=legend)
    drawer.FinishDrawing()
    return drawer.GetDrawingText().encode("utf-8") if as_svg else drawer.GetDrawingText()


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
_FORMULA_TOKEN_RE = re.compile(r"([A-Za-z]+|\d+|[+-]\d*)")


def _layout_font(size: int):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _formula_tokens(formula: str):
    tokens = []
    parts = [p for p in _FORMULA_TOKEN_RE.split(formula or "") if p]
    for i, part in enumerate(parts):
        if part[0] in "+-":
            tokens.append((part[1:] + part[0] if len(part) > 1 else part, "sup"))
        elif part.isdigit():
            tokens.append((part, "sub" if i and parts[i - 1][0].isalpha() else "normal"))
        else:
            tokens.append((part, "normal"))
    return tokens


def _render_formula_image(formula: str, size: int):
    from PIL import Image, ImageDraw
    base_font = _layout_font(size)
    small_font = _layout_font(max(10, int(size * 0.62)))
    tokens = _formula_tokens(formula)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widths, ascent = [], 0
    for text, style in tokens:
        font = small_font if style in ("sub", "sup") else base_font
        box = probe.textbbox((0, 0), text, font=font)
        widths.append(box[2] - box[0])
        ascent = max(ascent, box[3] - box[1])
    shift = max(3, int(size * 0.20))
    pad = max(4, size // 5)
    canvas = Image.new("RGB", (sum(widths) + pad * 2, ascent + shift * 2 + pad * 2), (255, 255, 255))
    pen = ImageDraw.Draw(canvas)
    x, baseline = pad, pad + shift
    for (text, style), width in zip(tokens, widths):
        font = small_font if style in ("sub", "sup") else base_font
        box = pen.textbbox((0, 0), text, font=font)
        y = baseline - box[1]
        if style == "sub":
            y += ascent - (box[3] - box[1]) + shift
        elif style == "sup":
            y -= shift
        pen.text((x - box[0], y), text, fill=(20, 20, 20), font=font)
        x += width
    return canvas


_FORMULA_BY_CANONICAL_SMILES = {}
for _smiles, _formula in _CONVENTIONAL_FORMULAS.items():
    _mol = Chem.MolFromSmiles(_smiles)
    if _mol is not None:
        _FORMULA_BY_CANONICAL_SMILES[Chem.MolToSmiles(_mol)] = _formula


def formula_for_display(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return None
    canonical = Chem.MolToSmiles(mol)
    if canonical in _FORMULA_BY_CANONICAL_SMILES:
        return _FORMULA_BY_CANONICAL_SMILES[canonical]
    carbons = sum(1 for a in mol.GetAtoms() if a.GetSymbol() == "C")
    if mol.GetRingInfo().NumRings() or carbons > 1 or mol.GetNumHeavyAtoms() > 4:
        return None
    if carbons and any(a.GetSymbol() in ("O", "N", "S") and a.GetTotalNumHs() for a in mol.GetAtoms()):
        return None
    from rdkit.Chem import rdMolDescriptors
    hill = rdMolDescriptors.CalcMolFormula(mol)
    if not carbons and hill.startswith("H"):
        return None
    return hill


def _trim_white(image, pad: int = 6):
    from PIL import Image, ImageChops
    background = Image.new(image.mode, image.size, (255, 255, 255))
    box = ImageChops.difference(image, background).getbbox()
    if not box:
        return image
    left, top, right, bottom = box
    return image.crop((max(0, left - pad), max(0, top - pad), min(image.width, right + pad), min(image.height, bottom + pad)))


def render_reaction_scheme(reactant_pairs, product_pairs, sub_size=REACTION_SUBIMAGE_SIZE, small_as_formula: bool = True) -> bytes | None:
    """Publication-style reaction image with coefficients, formulas, structures and a clean arrow."""
    from PIL import Image, ImageDraw

    def draw_one(smiles):
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        AllChem.Compute2DCoords(mol)
        drawer = rdMolDraw2D.MolDraw2DCairo(sub_size[0], sub_size[1])
        _apply_common_draw_options(drawer)
        try:
            drawer.drawOptions().fixedBondLength = max(24, sub_size[1] // 7)
        except Exception:
            pass
        rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
        drawer.FinishDrawing()
        return _trim_white(Image.open(io.BytesIO(drawer.GetDrawingText())).convert("RGB"))

    formula_size = max(30, sub_size[1] // 6)
    arrow_length = max(60, sub_size[0] // 5)
    arrow_head = max(14, sub_size[1] // 20)
    coefficient_font = _layout_font(formula_size)
    operator_font = _layout_font(formula_size)

    tokens: list[tuple[str, object]] = []
    for side_index, pairs in enumerate((reactant_pairs, product_pairs)):
        if side_index:
            tokens.append(("arrow", None))
        for i, (coefficient, smiles) in enumerate(pairs):
            if i:
                tokens.append(("op", "+"))
            formula = formula_for_display(smiles) if small_as_formula else None
            image = _render_formula_image(formula, formula_size) if formula else draw_one(smiles)
            if image is None:
                return None
            label = ReactionTerm(Fraction(coefficient), "").pretty_coefficient
            if label:
                tokens.append(("coefficient", label))
            tokens.append(("mol", image))

    if not tokens:
        return None

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    gap = max(10, sub_size[0] // 28)
    widths, heights = [], []
    for kind, payload in tokens:
        if kind == "mol":
            widths.append(payload.width); heights.append(payload.height)
        elif kind == "arrow":
            widths.append(arrow_length); heights.append(arrow_head)
        else:
            font = coefficient_font if kind == "coefficient" else operator_font
            box = probe.textbbox((0, 0), payload, font=font)
            widths.append(box[2] - box[0]); heights.append(box[3] - box[1])

    margin = gap * 2
    total_width = sum(widths) + gap * (len(tokens) - 1) + margin * 2
    total_height = max(heights) + margin * 2
    canvas = Image.new("RGB", (total_width, total_height), (255, 255, 255))
    pen = ImageDraw.Draw(canvas)
    x = margin
    for (kind, payload), width in zip(tokens, widths):
        if kind == "mol":
            canvas.paste(payload, (x, (total_height - payload.height) // 2))
        elif kind == "arrow":
            mid = total_height // 2
            pen.line([(x, mid), (x + arrow_length - arrow_head, mid)], fill=(20, 20, 20), width=max(2, arrow_head // 7))
            pen.polygon([(x + arrow_length, mid), (x + arrow_length - arrow_head, mid - arrow_head // 2),
                         (x + arrow_length - arrow_head, mid + arrow_head // 2)], fill=(20, 20, 20))
        else:
            font = coefficient_font if kind == "coefficient" else operator_font
            box = pen.textbbox((0, 0), payload, font=font)
            pen.text((x - box[0], (total_height - (box[3] - box[1])) // 2 - box[1]), payload, fill=(20, 20, 20), font=font)
        x += width + (gap // 2 if kind == "coefficient" else gap)

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def bytes_to_named_bytesio(data: bytes, filename: str) -> io.BytesIO:
    bio = io.BytesIO(data)
    bio.name = filename
    bio.seek(0)
    return bio


# ============================================================================
# 5. Chemical validation + editable RXN — integrated from chem_core.py
# ============================================================================
def _element_counts(smiles: str) -> tuple[dict[str, int], int] | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    counts: dict[str, int] = {}
    charge = 0
    for atom in mol.GetAtoms():
        counts[atom.GetSymbol()] = counts.get(atom.GetSymbol(), 0) + 1
        h = atom.GetTotalNumHs()
        if h:
            counts["H"] = counts.get("H", 0) + h
        charge += atom.GetFormalCharge()
    return counts, charge


def check_reaction_balance(reactant_pairs, product_pairs) -> dict:
    def side_totals(pairs):
        totals: dict[str, Fraction] = {}
        charge = Fraction(0)
        for coefficient, smiles in pairs:
            parsed = _element_counts(smiles)
            if parsed is None:
                return None, None
            counts, q = parsed
            for element, n in counts.items():
                totals[element] = totals.get(element, Fraction(0)) + Fraction(coefficient) * n
            charge += Fraction(coefficient) * q
        return totals, charge

    left, left_charge = side_totals(reactant_pairs)
    right, right_charge = side_totals(product_pairs)
    if left is None or right is None:
        return {"checked": False, "balanced": False, "message": "The structures could not be read, so balance was not checked."}

    diffs = {}
    for element in sorted(set(left) | set(right)):
        delta = right.get(element, Fraction(0)) - left.get(element, Fraction(0))
        if delta != 0:
            diffs[element] = delta
    charge_delta = right_charge - left_charge

    def fmt(value):
        value = Fraction(value)
        return str(value.numerator) if value.denominator == 1 else str(value)

    if not diffs and charge_delta == 0:
        return {"checked": True, "balanced": True,
                "message": "✓ The equation is balanced: every element and the total charge match on both sides."}

    parts = []
    for element, delta in diffs.items():
        side = "product" if delta > 0 else "reactant"
        parts.append(f"{fmt(abs(delta))} {element} on the {side} side")
    if charge_delta != 0:
        parts.append(f"a net charge difference of {fmt(charge_delta)}")
    return {"checked": True, "balanced": False,
            "element_difference": {e: fmt(d) for e, d in diffs.items()},
            "excess_side": {e: ("product" if d > 0 else "reactant") for e, d in diffs.items()},
            "excess_amount": {e: fmt(abs(d)) for e, d in diffs.items()},
            "charge_difference": fmt(charge_delta),
            "message": "⚠ The equation is NOT balanced — there is an excess of " + "; ".join(parts) + ". The scheme was still drawn."}


def validate_rxn_block(text: str) -> dict:
    problems: list[str] = []
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "$RXN":
        return {"valid": False, "problems": ["the file does not begin with the $RXN header"]}
    if len(lines) < 5:
        return {"valid": False, "problems": ["the $RXN header is incomplete"]}
    try:
        n_reactants = int(lines[4][0:3])
        n_products = int(lines[4][3:6])
    except (ValueError, IndexError):
        return {"valid": False, "problems": ["the reactant/product counts line is not in MDL fixed-column form"]}

    blocks, current = [], None
    for line in lines[5:]:
        if line.strip() == "$MOL":
            if current is not None:
                blocks.append(current)
            current = []
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)

    if len(blocks) != n_reactants + n_products:
        problems.append(f"the header declares {n_reactants + n_products} components but the file contains {len(blocks)}")
    for i, block in enumerate(blocks, 1):
        if not any(l.rstrip().endswith(("V2000", "V3000")) for l in block[:6]):
            problems.append(f"component {i} has no V2000/V3000 counts line")
        if not any(l.strip() == "M  END" for l in block):
            problems.append(f"component {i} is not terminated with 'M  END'")
    return {"valid": not problems, "problems": problems,
            "format": "MDL RXN V2000", "reactants": n_reactants, "products": n_products,
            "opens_in": ["ChemDraw", "ChemDraw JS", "MarvinSketch", "ISIS/Draw", "Avogadro", "RDKit", "Open Babel"]}


def generate_rxn_file(reactant_pairs, product_pairs, filename: str, title: str = "") -> Optional[io.BytesIO]:
    """Generate a validated MDL RXN file; fractional coefficients are scaled exactly."""
    _, scaled = scale_terms_to_integers(reactant_pairs, product_pairs)
    reactant_pairs_i, product_pairs_i = scaled

    def expand(pairs):
        out = []
        for term in pairs:
            n = Fraction(term.coefficient)
            if n.denominator != 1 or n <= 0:
                return None
            smiles = term.name
            out.extend([smiles] * int(n))
        return out

    left, right = expand(reactant_pairs_i), expand(product_pairs_i)
    if not left or not right:
        return None
    reaction_smiles = f"{'.'.join(left)}>>{'.'.join(right)}"
    try:
        rxn = rdChemReactions.ReactionFromSmarts(reaction_smiles, useSmiles=True)
        if rxn is None:
            return None
        rdChemReactions.Compute2DCoordsForReaction(rxn)
        block = rdChemReactions.ReactionToRxnBlock(rxn)
        if title:
            lines = block.split("\n")
            if len(lines) > 2:
                lines[1] = title[:80]
                block = "\n".join(lines)
        validation = validate_rxn_block(block)
        if not validation["valid"]:
            chem_logger.warning("Generated RXN failed structural validation: %s", validation["problems"])
            return None
    except Exception as exc:
        chem_logger.warning("RXN file generation failed for '%s': %s", reaction_smiles, exc)
        return None

    rxn_file = io.BytesIO(block.encode("utf-8"))
    rxn_file.name = filename
    rxn_file.seek(0)
    return rxn_file



def generate_single_rxn_file(smiles: str, filename: str) -> Optional[io.BytesIO]:
    """Backward-compatible single-species RXN output used by the original bot."""
    try:
        rxn = rdChemReactions.ReactionFromSmarts(f"{smiles}>>", useSmiles=True)
        if rxn is None:
            return None
        rdChemReactions.Compute2DCoordsForReaction(rxn)
        block = rdChemReactions.ReactionToRxnBlock(rxn)
        validation = validate_rxn_block(block)
        if not validation["valid"]:
            return None
    except Exception as exc:
        chem_logger.warning("Single RXN generation failed for '%s': %s", smiles, exc)
        return None
    rxn_file = io.BytesIO(block.encode("utf-8"))
    rxn_file.name = filename
    rxn_file.seek(0)
    return rxn_file

def generate_mol_file(smiles: str, filename: str) -> Optional[io.BytesIO]:
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        AllChem.Compute2DCoords(mol)
        mol_block = Chem.MolToMolBlock(mol)
    except Exception as exc:
        chem_logger.warning("MOL file generation failed for '%s': %s", smiles, exc)
        return None
    mol_file = io.BytesIO(mol_block.encode("utf-8"))
    mol_file.name = filename
    mol_file.seek(0)
    return mol_file


# ============================================================================
# 6. Telegram handlers
# ============================================================================
WELCOME_TEXT = (
    "Welcome to the Chemistry Drawing Bot! 🧪\n\n"
    "I turn compound names, SMILES, or InChI strings into clean 2D structures "
    "and editable files you can open in ChemDraw.\n\n"
    "<b>Single compound</b>\n"
    "Send a name, SMILES, or InChI directly, e.g. <code>Aspirin</code>\n\n"
    "<b>Reaction scheme</b>\n"
    "Separate reactants and products with '=' and join multiple compounds with '+'.\n"
    "Examples:\n"
    "<code>2 H2 + O2 = 2 H2O</code>\n"
    "<code>Methane + Oxygen = Carbon Dioxide + Water</code>\n\n"
    "The bot preserves coefficients, draws the scheme, checks balance, and sends an editable .rxn file."
)
NOT_FOUND_TEXT = (
    "⚠️ I couldn't resolve '<code>{name}</code>'.\n"
    "Double-check the spelling (English names work best), or try a SMILES / InChI string instead."
)
GENERIC_ERROR_TEXT = "❌ A technical error occurred while processing your request. Please try again."
PROCESSING_TEXT = "⚙️ Processing your request..."


async def notify_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return
        username = f"@{user.username}" if user.username else "No username"
        text = (
            "Notification: new user activity\n\n"
            f"Name: {html.escape(user.full_name)}\n"
            f"Username: {html.escape(username)}\n"
            f"ID: <code>{user.id}</code>\n"
            f"Message: {html.escape(update.message.text or '')}"
        )
        await context.bot.send_message(chat_id=ADMIN_ID, text=text, parse_mode="HTML")
    except Exception as exc:
        chem_logger.debug("Admin notification skipped: %s", exc)


async def chem_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chem_logger.info("/start from user %s", update.effective_user.id if update.effective_user else "unknown")
    asyncio.create_task(notify_admin(update, context))
    await update.message.reply_text(WELCOME_TEXT, parse_mode="HTML")


async def _resolve_terms(terms: list[ReactionTerm]):
    results = await asyncio.gather(*(resolve_species(term.name) for term in terms))
    resolved, notes = [], []
    for term, (smiles, note) in zip(terms, results):
        if smiles:
            resolved.append((term.coefficient, smiles))
            if note:
                notes.append(note)
        else:
            resolved.append((term.coefficient, None))
    return resolved, notes


async def _handle_single_compound(update: Update, status_msg, text: str) -> None:
    compounds = split_compound_list(text)
    if not compounds:
        await status_msg.delete()
        return
    name = compounds[0]
    smiles = await resolve_compound_to_smiles(name)
    if not smiles:
        await status_msg.edit_text(NOT_FOUND_TEXT.format(name=html.escape(name)), parse_mode="HTML")
        return

    image_bytes, rxn_file, mol_file = await asyncio.gather(
        asyncio.to_thread(render_molecule, smiles, name),
        asyncio.to_thread(generate_single_rxn_file, smiles, f"{safe_filename(name)}.rxn"),
        asyncio.to_thread(generate_mol_file, smiles, f"{safe_filename(name)}.mol"),
    )
    if image_bytes:
        photo = bytes_to_named_bytesio(image_bytes, f"{safe_filename(name)}.png")
        await update.message.reply_photo(
            photo=photo,
            caption=f"<b>{html.escape(name)}</b>\n<code>{html.escape(smiles)}</code>",
            parse_mode="HTML",
        )
    if rxn_file:
        await update.message.reply_document(document=rxn_file, caption="Editable reaction file (.rxn)")
    if mol_file:
        await update.message.reply_document(document=mol_file, caption="Editable molecule file (.mol)")
    await status_msg.delete()


async def _handle_reaction(update: Update, status_msg, text: str) -> None:
    parts = text.split("=")
    if len(parts) != 2:
        await status_msg.edit_text("⚠️ Please use exactly one '=' sign to separate reactants and products.")
        return

    reactant_terms = split_compound_terms(parts[0])
    product_terms = split_compound_terms(parts[1])
    if not reactant_terms or not product_terms:
        await status_msg.edit_text("⚠️ Please provide at least one compound on each side of the reaction.")
        return

    reactant_resolved, reactant_notes = await _resolve_terms(reactant_terms)
    product_resolved, product_notes = await _resolve_terms(product_terms)
    unresolved = []
    for term, (coefficient, smiles) in zip(reactant_terms, reactant_resolved):
        if smiles is None:
            unresolved.append(term.name)
    for term, (coefficient, smiles) in zip(product_terms, product_resolved):
        if smiles is None:
            unresolved.append(term.name)
    if unresolved:
        joined = ", ".join(html.escape(n) for n in unresolved)
        await status_msg.edit_text(NOT_FOUND_TEXT.format(name=joined), parse_mode="HTML")
        return

    reactant_pairs = [(coef, smiles) for coef, smiles in reactant_resolved]
    product_pairs = [(coef, smiles) for coef, smiles in product_resolved]

    balance = check_reaction_balance(reactant_pairs, product_pairs)
    equation = format_equation(reactant_terms, product_terms)
    rxn_title = equation

    scheme_bytes, rxn_file = await asyncio.gather(
        asyncio.to_thread(render_reaction_scheme, reactant_pairs, product_pairs),
        # generate_rxn_file expects ReactionTerm objects so it can preserve
        # and exactly scale stoichiometric coefficients (including fractions).
        asyncio.to_thread(
            generate_rxn_file,
            [ReactionTerm(coef, smiles) for coef, smiles in reactant_pairs],
            [ReactionTerm(coef, smiles) for coef, smiles in product_pairs],
            "Reaction.rxn",
            rxn_title,
        ),
    )

    caption_lines = ["Reaction scheme:", f"<code>{html.escape(equation)}</code>"]
    if balance.get("checked"):
        caption_lines.append(html.escape(balance["message"]))
    if reactant_notes or product_notes:
        # Keep ambiguity explanations short and visible without blocking the drawing.
        for note in (reactant_notes + product_notes)[:3]:
            caption_lines.append("ℹ️ " + html.escape(note))
    caption = "\n".join(caption_lines)

    if scheme_bytes:
        photo = bytes_to_named_bytesio(scheme_bytes, "Reaction.png")
        await update.message.reply_photo(photo=photo, caption=caption, parse_mode="HTML")
    else:
        chem_logger.warning("Reaction PNG rendering failed for %s", equation)

    if rxn_file:
        await update.message.reply_document(document=rxn_file, caption="Editable reaction file (.rxn)")
    else:
        chem_logger.warning("Reaction RXN generation failed for %s", equation)

    await status_msg.delete()


async def handle_chemical_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text:
        return
    chem_logger.info("Message from %s: %s", update.effective_user.id if update.effective_user else "unknown", text)
    asyncio.create_task(notify_admin(update, context))
    status_msg = await update.message.reply_text(PROCESSING_TEXT)
    try:
        if "=" in text:
            await _handle_reaction(update, status_msg, text)
        else:
            await _handle_single_compound(update, status_msg, text)
    except Exception as exc:
        chem_logger.error("Error while processing message '%s': %s", text, exc, exc_info=True)
        try:
            await status_msg.edit_text(GENERIC_ERROR_TEXT)
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, NetworkError):
        chem_logger.warning("Temporary network error (will auto-retry): %s", context.error)
        return
    chem_logger.error("Unhandled exception while processing an update", exc_info=context.error)


# ============================================================================
# 7. Application wiring & entrypoint
# ============================================================================
async def _on_startup(application: Application) -> None:
    await pubchem_client.start()
    chem_logger.info("PubChem HTTP client started.")


async def _on_shutdown(application: Application) -> None:
    await pubchem_client.close()
    chem_logger.info("PubChem HTTP client closed.")


def run_chem_bot() -> None:
    if not CHEM_BOT_TOKEN:
        chem_logger.error("CRITICAL: CHEM_BOT_TOKEN is not set. Chemistry bot will NOT start.")
        return

    application = (
        Application.builder()
        .token(CHEM_BOT_TOKEN)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    application.add_handler(CommandHandler(["start", "help"], chem_start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chemical_message))
    application.add_error_handler(error_handler)

    chem_logger.info("Bot is polling and ready to receive messages...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        stop_signals=None,
        close_loop=False,
    )


# ══════════════════════════════════════════════════════════════════════════
# ██  نقطة الدخول المدمجة: تشغيل البوتين معاً في عملية واحدة  ██
# ══════════════════════════════════════════════════════════════════════════
def main():
    if CHEM_BOT_TOKEN:
        chem_thread = threading.Thread(target=run_chem_bot, name='chem-bot-polling', daemon=True)
        chem_thread.start()
        orca_logger.info('تم بدء بوت الكيمياء (Chemistry Bot) في Thread خلفي (Polling).')
    else:
        orca_logger.warning('CHEM_BOT_TOKEN غير مضبوط — بوت الكيمياء لن يعمل. بوت ORCA سيستمر بالعمل بشكل طبيعي.')
    run_orca_flask_server()


if __name__ == '__main__':
    main()
