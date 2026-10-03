# ChemDraw Bots

Two Telegram bots in one Python process, built for Render's free tier:
a **chemistry drawing bot** (names / SMILES / InChI / CAS → structures and reaction
schemes, exported as PNG, CDXML, SVG, TIFF) and a **quantum-input generator**
(ORCA 6.1, Psi4, Gaussian 16 input files). Full documentation below, in Arabic.

<div dir="rtl">

# ChemDraw Bots — بوتان لتيلغرام

مستودع يشغّل **بوتَي تيلغرام في عملية Python واحدة**، ومُعدّ للعمل على خطة Render المجانية:

| البوت | وظيفته | أسلوب العمل | متغير التوكن |
|---|---|---|---|
| **بوت الرسم** (Chemistry Drawing Bot) | رسم المركبات والتفاعلات الكيميائية من الاسم أو SMILES أو InChI أو رقم CAS | Polling في خيط خلفي | `CHEM_BOT_TOKEN` |
| **بوت المدخلات الكمية** (Quantum Input Generator) | توليد ملفات إدخال ORCA 6.1 وPsi4 وGaussian 16 | Webhook فوق Flask | `ORCA_BOT_TOKEN` |

> البوتان **يجهّزان الملفات فقط**؛ لا يشغّلان ORCA ولا Psi4 ولا Gaussian، ولا يتحققان من صحة النتيجة الكيميائية للحساب. راجع كل ملف مُولَّد على البرنامج الذي ستستخدمه قبل الاعتماد عليه.

## 1) بوت الرسم

أرسل اسماً أو SMILES أو InChI أو رقم CAS فيعود إليك رسم للمركب، أو أرسل معادلة فيرسم التفاعل ويوازنه.

**أمثلة**

```text
H2 + O2 = H2O
Ethene + H2 = Ethane || Ni
Ethene + H2 = Ethane || Ni | 150 °C
```

- ما بعد `||` شروط تُكتب **فوق** السهم، وما بعد `|` شروط تُكتب **تحته**.
- الفاصل بين المتفاعلات والنواتج هو `=` أو `>>`.

**المزايا**

- موازنة تلقائية بأصغر معاملات صحيحة، مع تحقق من توازن العناصر والشحنة.
- أسهم أمامية ومتزنة (equilibrium) وعكسية.
- رسم مكثّف/هيكلي ذكي للجزيئات الصغيرة، وشروط التفاعل فوق السهم وتحته.
- مخرجات: **PNG** و**CDXML** افتراضياً (ملف CDXML مخصّص للفتح والتعديل في ChemDraw)، و**SVG** و**TIFF** و**RXN** اختيارياً، مع خلفية شفافة اختيارية. تُضبط من زر "Drawing settings".
- للمركب المفرد: إن وفّر PubChem بنية ثلاثية الأبعاد محسوبة، يُرسل البوت أيضاً ملف MOL ثلاثي الأبعاد.
- أدوات متقدمة (زر "Advanced chemistry") تعمل على آخر تفاعل رُسم: موازنة الأكسدة والاختزال في وسط حمضي أو قاعدي، والمعادلة الأيونية الصافية، وترقيم الذرات (atom mapping) عند وجود تطابق فريد فقط، ورسم آلية بأسهم منحنية يحدّدها المستخدم:

  ```text
  /mechanism CCO || 1->2
  ```

  السهم `->` لزوج إلكترونات، و`~>` لإلكترون مفرد.
- طابور رسم محدود الحجم (عامل واحد افتراضياً) كي لا يُرهق الخادم المجاني عند تعدد المستخدمين.

## 2) بوت المدخلات الكمية

1. أدخل الجزيء بإحدى الطرق: `#benzene` (جلب من PubChem بالاسم)، أو ارفع ملف `.xyz` / `.mol` / `.sdf`، أو أرسل `/xyz` ثم الإحداثيات الديكارتية.
2. اختر المحرك: **ORCA 6.1** (`.inp`) أو **Psi4** (`.dat`) أو **Gaussian 16 لـ Linux** (`.gjf`)، ثم اتبع الأزرار: الشحنة، والتعددية، والطريقة، ومجموعة الأساس، والمذيب، والموارد.
3. أنواع الحساب في معالج ORCA: Single Point، وOpt، وOpt Freq، وOptTS، وTD-DFT، أو أمر مخصص. ويضيف Gaussian إعدادات مسبقة للترددات وNMR وغيرها.
4. للإدخال الخبير أرسل `/manual` والصق ملف Gaussian أو Psi4 كاملاً، أو ارفع `.gjf` / `.com` (يشمل تصدير GaussView) أو `.dat`. الحد الأقصى **128 كيلوبايت**، والبوت لا ينفّذ أي كود Psithon يكتبه المستخدم.
5. `/help` يعيد رسالة البداية، و`/cancel` يمسح المعالج الحالي. لكل مرسل جلسة مستقلة حتى داخل المجموعات.

التفاصيل الكاملة (حدود MaxDisk في Gaussian، وPCM وD4 في Psi4، وملاحظات التحقق) في [`QUANTUM_INPUT_BOT.md`](QUANTUM_INPUT_BOT.md).

## 3) كيف يعمل الكود

```text
python app.py
   └─ legacy_app.py      ← البوتان الأساسيان وخادم Flask
        + geometry_normalizer       توحيد إحداثيات MOL/SDF/XYZ
        + publication_entry_advanced  الرسم بجودة النشر
        + psi4_extension_v4         مدخلات Psi4
        + quantum_input_extension   معالج ORCA/Psi4/Gaussian والإدخال اليدوي
```

يفتح خادم Flask المنفذ الوحيد الذي تسمح به Render، وفيه المسارات:

| المسار | الاستخدام |
|---|---|
| `/health` | فحص الحالة (يصلح لمراقبة خارجية) |
| `/registerWebhook?secret=…` | تسجيل Webhook بوت ORCA عند تيلغرام (مرة واحدة) |
| `/deleteWebhook?secret=…` | حذفه |
| `/telegram` (POST) | استقبال تحديثات تيلغرام |

أهم الملفات: `cdxml_export.py` (تصدير CDXML)، `rxn_export.py` (RXN)، `reaction_balancer.py` (الموازنة)، `advanced_chemistry*.py` (الأدوات المتقدمة)، `publication_*.py` (الرسم)، `single_compound_pipeline.py` (المركب المفرد وبنية PubChem الثلاثية)، `chemistry_ui.py` (أزرار الإعدادات)، `render_capacity.py` (طابور الرسم)، `quantum_validation.py` (فحص الإدخال اليدوي)، `bounded_state_cache.py` (سقف ذاكرة الجلسات).

## 4) التشغيل

**المتطلبات:** Python 3.12 (محدد في `.python-version`) وتوكنان من [@BotFather](https://t.me/BotFather) لبوتين **مختلفين**.

```bash
pip install -r requirements.txt
export ORCA_BOT_TOKEN="..."
export CHEM_BOT_TOKEN="..."
export ORCA_WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python app.py
```

بوت الرسم يعمل مباشرة بالـ Polling. أما بوت ORCA فيحتاج رابط HTTPS عاماً لتسجيل الـ Webhook (على Render يتوفر تلقائياً).

> لا تستخدم توكناً واحداً للبوتين: تيلغرام لا يسمح بالـ Polling على بوت له Webhook نشط.

### متغيرات البيئة

| المتغير | الحالة | الوصف |
|---|---|---|
| `ORCA_BOT_TOKEN` | مطلوب | توكن بوت المدخلات الكمية (يُقبل أيضاً `TELEGRAM_BOT_TOKEN` أو `TG_BOT_TOKEN`) |
| `CHEM_BOT_TOKEN` | مطلوب | توكن بوت الرسم؛ إن غاب يعمل بوت ORCA وحده |
| `ORCA_WEBHOOK_SECRET` | موصى به بشدة | سر الـ Webhook. يلزم لمساري التسجيل والحذف. القيم المقبولة عند تيلغرام: `A-Z a-z 0-9 _ -` فقط، فاستخدم `secrets.token_urlsafe(32)`. بعد تغييره أعد تسجيل الـ Webhook |
| `ADMIN_ID` | اختياري | رقم تيلغرام الذي يصله إشعار بنشاط مستخدمي بوت الرسم (الاسم والمعرّف ونص الرسالة). إن ضُبط فيجب أن يكون رقماً، ولا تتركه فارغاً |
| `STATE_CACHE_MAX_ITEMS` | اختياري، `500` | أقصى عدد جلسات لمعالج ORCA في الذاكرة |
| `STATE_CACHE_TTL_SECONDS` | اختياري، `21600` | مدة بقاء الجلسة بلا نشاط (6 ساعات) |
| `CHEM_RENDER_WORKERS` | اختياري، `1` | عدد عمال الرسم المتوازيين |
| `CHEM_MAX_PENDING` | اختياري، `48` | أقصى عدد طلبات رسم منتظرة |
| `CHEM_RENDER_CACHE_MB` / `CHEM_RENDER_CACHE_ITEMS` | اختياري، `12` / `48` | حجم ذاكرة التخزين المؤقت للصور |
| `PORT` و`RENDER_EXTERNAL_URL` | تلقائي | توفّرهما Render، لا تضبطهما |

## 5) النشر على Render

1. أنشئ **Web Service** من هذا المستودع (أو استخدم ملف [`render.yaml`](render.yaml) كـ Blueprint).
2. الأوامر: البناء `pip install -r requirements.txt`، والتشغيل `python app.py`، ومسار الفحص `/health`.
3. أضف متغيرات البيئة من الجدول أعلاه (لا تضع التوكنات أو السر في Git).
4. بعد أول نشر ناجح افتح **مرة واحدة**:

   ```text
   https://<اسم-خدمتك>.onrender.com/registerWebhook?secret=<ORCA_WEBHOOK_SECRET>
   ```

   بوت الرسم لا يحتاج تسجيلاً.

**تنبيهات الخطة المجانية**

- تنام الخدمة بعد نحو 15 دقيقة بلا طلبات HTTP. عند النوم يتوقف الـ Polling فلا يرد بوت الرسم، وعند الاستيقاظ تُحذف الرسائل التي وصلته أثناء النوم (`drop_pending_updates`). يخفف ذلك استدعاء `/health` كل 10–14 دقيقة من خدمة مراقبة خارجية مثل UptimeRobot، أو استخدام خطة مدفوعة.
- جلسات معالج ORCA في الذاكرة فقط، فتضيع عند النوم أو إعادة النشر.
- الذاكرة 512MB، لذلك صُمّم الرسم بطابور محدود وذاكرة مؤقتة صغيرة.

## 6) الاختبارات

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

الاختبارات تستخدم رسائل تلغرام وهمية، ولا تحتاج توكنات ولا ORCA ولا Psi4 ولا ترخيص Gaussian. تفحص النص المُولَّد وانتقالات المعالج، لا تنفيذ المحركات. وتعمل تلقائياً عبر GitHub Actions عند كل دفع.

## الترخيص

[MIT](LICENSE) © 2026 abdulsalam salih

</div>
