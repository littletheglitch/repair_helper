# گزارش ارتقای معماری LLM — سیستم عیب‌یاب الکترونیکی (Phase 3)

این گزارش خلاصهٔ کار ارتقای معماری LLM در پروژهٔ `ElectronicsDiagnostic` است.
هدف از این مرحله تبدیل LLM از یک **paraphraser** ساده به یک **Diagnostic
Orchestrator** بود که با فراخوانی موتورهای تخصصی از طریق یک رابط
tool-calling استاندارد، عیب‌یابی را هدایت می‌کند.

---

## ۱) وضعیت قبل از ارتقا

قبل از این مرحله، `llm_engine.py` فقط یک wrapper بود که خروجی موتور
قاعده‌ای (rule engine) را به LLM می‌داد و از آن می‌خواست متن را برای
تکنسین بازنویسی کند. در سیستم‌پرامپت صراحتاً نوشته شده بود که LLM حق
 ندارد عدد، پین‌آوت یا مشخصات قطعه بسازد. در نتیجه LLM هیچ‌گاه موتورهای
تخصصی موجود (`electrical_math`، `measurement_validation`،
`next_best_test`، `power_sequence`، …) را فراخوانی نمی‌کرد؛ این موتورها
فقط از طریق CLI و rule engine در دسترس بودند. این دقیقاً همان نقطه‌ضعفی
بود که در صورت نبود LLM، کل ارزش آن صرفاً «زیباسازی متن» می‌شد.

زیرساخت موجود **خوب** بود: `engines/base.py` یک Tool Router با اعتبارسنجی
schema، `EngineResult`/`EngineError` ساختاریافته، و `Quantity` با نرمال‌سازی
واحد SI داشت. مشکل فقط این بود که LLM هرگز به آن متصل نبود.

---

## ۲) معماری جدید

```
                        ┌──────────────────────────────────┐
   علائم/اندازه‌گیری     │         Context Manager          │
   ─────────────────────▶│  (بازیابی KB، بودجه توکن،         │
                        │   خلاصهٔ ساختاریافته، بدون حذف)   │
                        └──────────────┬───────────────────┘
                                       │ context اسمبل‌شده
                                       ▼
   ┌────────────┐   tool call      ┌──────────────────────────┐
   │   LLM      │◀────────────────│   Diagnostic Orchestrator │
   │  (Backend) │────────────────▶│   (حلقهٔ محدود، audit)    │
   └────────────┘  tool result    └───────────┬──────────────┘
   llama-server                       tool call │
   ماندگار (persistent)                ▼
                              ┌──────────────────────┐
                              │  Tool Calling Iface  │
                              │  (JSON، provenance،   │
                              │   خطاهای ساختاریافته) │
                              └──────────┬───────────┘
                                         ▼
                              ┌──────────────────────┐
                              │   Tool Router +      │
                              │   موتورهای تخصصی     │
                              │  (electrical_math،    │
                              │   measurement_valid، │
                              │   next_best_test،    │
                              │   power_sequence، …) │
                              └──────────────────────┘
```

موتور قاعده‌ای (deterministic) همچنان در هر عیب‌یابی اجرا می‌شود به‌عنوان
cross-check و fallback. اگر backend LLM در دسترس نباشد، orchestrator خروجی
موتور قاعده‌ای را بی‌تغییر برمی‌گرداند.

### نقش هر جزء جدید

| جزء | فایل | نقش |
|---|---|---|
| Tool Calling Interface | `src/eda/tool_interface.py` | پروتکل JSON بین LLM و موتورها: کاتالوگ ابزار، parse کردن tool-call، coerce کردن Quantity، provenance passthrough، خطاهای ساختاریافته. |
| LLM Backend abstraction | `src/eda/llm_backend.py` | رابط `Backend` مشترک + پیاده‌سازی‌های Stub / llama_server (persistent) / llama_cli (legacy) / cloud (reference) / disabled. |
| Model Registry | `src/eda/model_registry.py` | مدیریت `Models/` و `Engine/`، دانلود با SHA-256، تنظیمات ctx/threads/batch/gpu_layers، پایش RAM، bundle قابل‌حمل. |
| Context Manager | `src/eda/context_manager.py` | اسمبل context جلسه (علائم، اندازه‌گیری‌ها، KB، اسناد) در بودجهٔ توکن، بازیابی KB، خلاصهٔ ساختاریافته، بدون حذف داده (داده‌های حذف‌شده از context همچنان در DB هستند و نام برده می‌شوند). |
| Diagnostic Orchestrator | `src/eda/orchestrator.py` | حلقهٔ agent: دریافت context، فراخوانی toolها، تفسیر خروجی، audit ادعاهای بی‌منبع، مقایسه با rule engine، محدودیت تکرار. |
| LLM Benchmark | `src/eda/llm_benchmark.py` | harness مستقل برای مقایسهٔ backendها روی معیارهای مشخص‌شدهٔ کاربر. |

---

## ۳) Tool Calling Interface

این لایه روی Tool Router موجود (`engines/base.py`) قرار دارد و سه چیز
 اضافه می‌کند:

1. **کاتالوگ ابزار**: یک توصیف JSON از هر متد موتوری که LLM مجاز به
   فراخوانی آن است. فقط متدهای read/compute در کاتالوگ هستند؛ متدهای
   تغییردهندهٔ DB (`hypotheses.confirm`، `power_sequence.save_sequence`،
   `topology.add_*`) عمداً کنار گذاشته شده‌اند تا LLM نتواند DB را به‌صورت
   خودکار تغییر دهد.

2. **tool-call / tool-result**: هر فراخوانی باید شامل `tool` و `args` باشد.
   خروجی همیشه `provenance` دارد (منبع عدد). LLM فقط مجاز به بازنویسی متن
   است؛ `value`/`value_si`/`unit` و `provenance` بدون تغییر در تاریخچه
   نگه‌داری می‌شوند.

3. **خطاهای ساختاریافته**: شکست موتور به‌صورت
   `{"error": {"code": ..., "message": ..., "field": ...}}` برمی‌گردد، نه
   traceback. این به LLM اجازه می‌دهد خطا را تفسیر یا با arg اصلاح‌شده
   retry کند.

`Quantity` از dict `{"value": v, "unit": u}` یا رشته `"19.4V"` سازگار
می‌شود و اعتبارسنجی واحد SI انجام می‌شود (مثلاً `Ω` و `kΩ` به همان SI
base normalize می‌شود).

---

## ۴) Backend abstraction و رفع مشکل یک-پردازش-برای-هر-درخواست

پیاده‌سازی قبلی `_call_llama_cli` برای هر درخواست یک `llama-cli` جدید
 اجرا می‌کرد و مدل را هر بار از نو بارگذاری می‌کرد (۵–۳۰ ثانیه فقط برای
 load روی CPU نسل ششم). راه‌حل: `ServerRuntime` که یک `llama-server` را
یک‌بار start می‌کند و آن را زنده نگه می‌دارد؛ همهٔ درخواست‌ها از طریق HTTP
 به همان مدل بارگذاری‌شده می‌روند. مدل یک‌بار load می‌شود، نه برای هر
 درخواست.

| Backend | مدل یکبار بارگذاری می‌شود؟ | کاربرد |
|---|---|---|
| `llama_server` (هدف) | بله | GGUF واقعی روی ویندوز، persistent. |
| `llama_cli` (legacy) | خیر | سازگاری با مسیر قدیمی `prepare_model.py`. |
| `stub` | — | مرجع قطعی (deterministic)؛ بدون مدل. |
| `cloud` | — | فقط مرجع (glm-4-plus از طریق `z-ai chat`)؛ NOT deployment. |
| `disabled` | — | فقط rule engine. |

---

## ۵) Model Registry

- مدل‌ها در `Models/` و runtime در `Engine/`؛ مدل داخل EXE بسته‌بندی
  **نشده**.
- دانلود فقط با درخواست صریح (`eda models download <key>` با تأیید `[y/N]`)
  و محاسبهٔ SHA-256 انجام می‌شود.
- امکان انتقال به کامپیوتر دیگر با `eda models bundle <dir>` و
  `eda models import <dir>` (با تأیید مجدد SHA-256 در مقصد).
- تنظیمات `n_ctx`، `n_threads`، `n_batch`، `n_gpu_layers`، `temperature`،
  `max_tokens`، `server_port` با `eda model-settings` قابل تنظیم.
- پایش RAM با `eda ram` (RSS پردازش `llama-server` از `/proc` یا `tasklist`).

### کلاس‌های مدل بررسی‌شده

کاربر خواست ۱B–۱.۵B، ۳B–۴B و ۷B–۸B بررسی شود. ما **بزرگ‌ترین مدل را
فرض نکردیم**. تخمین صادقانه برای هدف ۸GB/i7 نسل ششم:

| کلاس | دیسک (Q4_K_M) | RAM @ n_ctx=2048 | CPU tok/s (تخمین) | رأی برای هدف ۸GB |
|---|---|---|---|---|
| ۱B–۱.۵B | ۰.۷–۱.۱ GB | ۱.۵–۲ GB | ۸–۱۵ | **پیشنهادی**؛ ~۶GB برای OS باقی می‌ماند. |
| ۳B–۴B | ۱.۸–۲.۶ GB | ۳–۴ GB | ۲–۴ | قابل استفاده، RAM تنگ. |
| ۷B–۸B | ۴.۰–۵.۵ GB | ۵–۶ GB | ۰.۵–۱.۵ | **توصیه نمی‌شود** روی ۸GB — swap سنگین. |

**توجه مهم**: این اعداد تخمین‌اند، نه اندازه‌گیری. این sandbox لینوکس
است و GGUF واقعی اجرا نمی‌کند. اعداد واقعی RAM و tok/s برای مدل‌های GGUF
باید با `eda llm-benchmark --backends llama_server` روی ویندوز هدف، بعد از
load هر کلاس، اندازه‌گیری شوند (دستور دقیق در §۷).

---

## ۶) Context Manager

- علائم، اندازه‌گیری‌ها، یادداشت‌ها و ارجاعات اسناد را در یک بلوک
  ساختاریافته اسمبل می‌کند.
- KB facts مرتبط را با کلیدواژهٔ علائم بازیابی می‌کند و بر اساس tier
  رتبه‌بندی می‌کند (manufacturer-confirmed بالاترین).
- بودجهٔ توکن را رعایت می‌کند (۳۰٪ n_ctx برای پاسخ + کاتالوگ رزرو می‌شود).
  وقتی بودجه تنگ می‌شود، اندازه‌گیری‌های قدیمی‌تر را trim می‌کند.
- **هیچ داده‌ای بدون ثبت در DB حذف نمی‌شود**: اندازه‌گیری‌های trim‌شده در
  متن note می‌شوند ("X اندازه‌گیری قدیمی حذف شد — همچنان در DB؛
  `eda history`") تا تکنسین بتواند آن‌ها را بازیابی کند.

---

## ۷) Diagnostic Orchestrator

حلقهٔ اصلی در `Orchestrator.run`:

1. Context را از Context Manager می‌گیرد.
2. rule engine را اجرا می‌کند (cross-check + fallback).
3. context + کاتالوگ ابزار + system prompt (که جعل را ممنوع می‌کند) را به
   LLM می‌دهد.
4. LLM یا tool-call صادر می‌کند یا پاسخ نهایی.
5. در صورت tool-call: از طریق Tool Interface به موتور تخصصی dispatch
   می‌شود، خروجی ساختاریافته (با provenance) به تاریخچه اضافه می‌شود، و
   حلقه تکرار می‌شود (حداکثر ۸ تکرار).
6. در صورت پاسخ نهایی: **audit** انجام می‌شود — هر ادعای فنی
   (عدد+واحد یا part-number) باید در تاریخجه یک tool result یا KB fact
   داشته باشد؛ ادعاهای بی‌منبع flag می‌شوند و پاسخ annotate می‌شود.

System prompt صراحتاً می‌گوید: LLM فقط می‌تواند ولتاژ/جریان/مقاومت/pinout/
part-number/datasheet value را وقتی ادعا می‌کند که در tool result یا KB
fact موجود باشد، و باید منبع را cite کند.

---

## ۸) Benchmark مستقل (اعداد واقعی، اندازه‌گیری‌شده)

دستور: `eda llm-benchmark --backends stub,cloud`

۴ کیس روی همهٔ backendها یکسان اجرا می‌شوند:
1. `no_power_5valw_short` — اتصال کوتاه +5VALW (Fault مشخص).
2. `no_power_input_open` — MOSFET ورودی باز (Gap).
3. `contradictory_3valw` — اندازه‌گیری‌های متناقض +3VALW (Contradiction).
4. `missing_critical_measurement` — VIN اندازه‌گیری نشده (Gap).

معیارها: انتخاب موتور، تطابق next-test با مرجع قطعی، تشخیص Gap، تشخیص
تناقض، تعداد ادعای بی‌منبع، انضباط امتناع، RAM، سرعت.

### نتایج واقعی (اندازه‌گیری‌شده در این sandbox)

| Backend | EngSel | NextTest | GapDet | ContrDet | Unsourced | Refusal | AvgMs | Tok/s |
|---|---|---|---|---|---|---|---|---|
| stub (مرجع قطعی) | 1.00 | 0.75 | 0.00 | 0.00 | 0.25 | 0.00 | 1.8 | 92714 |
| cloud (glm-4-plus، مرجع) | 1.00 | 0.75 | 1.00 | 1.00 | 0.00 | 1.00 | 21151 | 42.3 |

#### تفسیر صادقانه

- **stub** یک tool-caller کامل است (EngSel=1.0، بدون hallucination از
  numbers) اما یک explainer ضعیف است: پاسخ نهایی‌اش خلاصهٔ tool result است،
  gap را به‌صورت روایی نمی‌گوید و امتناع نمی‌کند. این حد بالایی برای
  correctness و حد پایینی برای narrative discipline است.
- **cloud** (glm-4-plus از طریق `z-ai chat`) نشان داد معماری end-to-end با
  یک مدل واقعی کار می‌کند: در کیس تناقض، `measurement_validation` را
  فراخوانی کرد، تناقض را تشخیص داد، از تأیید fault امتناع کرد، gap را
  اعلان کرد، و **صفر** ادعای فنی بی‌منبع داشت. این دقیقاً آن چیزی است که
  spec خواست.
- تأخیر cloud به‌خاطر overhead subprocess `z-ai chat` در هر درخواست است،
  نه inference مدل؛ `llama_server` محلی تأخیر متفاوتی خواهد داشت (load
  یک‌بار، سپس سریع).

**کاملاً صادقانه**: مدل cloud مرجع است، نه deployment هدف. اعداد RAM و
tok/s برای GGUF واقعی باید روی ویندوز هدف اندازه‌گیری شوند.

---

## ۹) حفظ قابلیت‌های موجود

- همهٔ قابلیت‌های Phase 1 و 2 حفظ شده‌اند: جلسات، اندازه‌گیری‌ها،
  documents، Knowledge Base، موتورهای محاسباتی، Collector.
- تغییرات **افزودنی** بودند: هیچ فایل موجود حذف نشد؛ `llm_engine.py` برای
  سازگاری تست‌های موجود دست‌نخورده ماند (backend stub/cloud/server جدید
  در کنار آن قرار دارند).
- تست‌های موجود: **۲۸۶ → ۳۵۳ تست** (+۶۷ تست جدید، **صفر regression**).
  همهٔ تست‌ها offline و fixture-based اجرا می‌شوند.

---

## ۱۰) محدودیت‌ها (صادقانه)

- **اعداد GGUF تخمین‌اند، نه اندازه‌گیری.** این sandbox لینوکس است و GGUF
  اجرا نمی‌کند. RAM/tok-s در §۵ تخمین مهندسی از benchmarkهای عمومی
  llama.cpp است. اعداد واقعی باید با دستور `eda llm-benchmark --backends
  llama_server` روی ویندوز هدف، بعد از load هر کلاس، اندازه‌گیری شوند.
- **backend cloud مرجع است، نه deployment.** فقط برای اثبات کار
  معماری با یک مدل واقعی. هدف از `llama_server` با GGUF محلی استفاده
  می‌کند.
- **stub یک tool-caller کامل اما explainer ضعیف است.** پاسخ‌های روایی
  (gap، refusal) ضعیف است. مدل‌های واقعی در این معیارها بالاتر می‌زنند.
- **audit اکتشافی است.** تشخیص ادعای بی‌منبع بر اساس الگوی عدد/part-number
  است؛ یک ادعای جدید بدون عدد قابل‌تشخیص ممکن است عبور کند. این trade-off
  عمدی برای قطعی و offline ماندن audit است.
- **LLM حق تغییر DB ندارد.** کاتالوگ ابزار عمداً متدهای mutating را حذف
  کرده. فقط تکنسین از طریق CLI می‌تواند DB را تغییر دهد.

---

## ۱۱) نصب Runtime و مدل (ویندوز هدف)

1. **Runtime**: از https://github.com/ggerganov/llama.cpp/releases یک بیلد
   win-cpu-x64 بگیرید و `llama-server.exe` را در `ElectronicsDiagnostic\Engine\`
   قرار دهید (auto-detect می‌شود).
2. **مدل**: `python -m eda models download qwen2.5-1.5b-instruct-q4_k_m`
   (با تأیید `[y/N]`، چاپ SHA-256، به‌روزرسانی `settings.json`).
3. **تنظیمات**: `eda model-settings n_ctx 4096` و امثال آن.
4. **تأیید**: `eda backend show` و `eda ram`.
5. **اجرای orchestrator**: `eda orchestrate`.
6. **benchmark واقعی**: `eda llm-benchmark --backends stub,llama_server`.

### تعویض مدل

```
eda model-settings model_file Qwen2.5-7B-Instruct-Q4_K_M.gguf
eda backend stop
eda backend show
```

### انتقال به کامپیوتر دیگر

```
eda models bundle D:\portable_eda_bundle
# در کامپیوتر دیگر:
eda models import D:\portable_eda_bundle
```

---

## ۱۲) فایل‌های تغییرکرده / اضافه‌شده

### فایل‌های جدید (Phase 3)

| فایل | نقش |
|---|---|
| `src/eda/tool_interface.py` | رابط tool-calling (کاتالوگ، dispatch، provenance، خطاها) |
| `src/eda/llm_backend.py` | backend abstraction (stub/llama_server/llama_cli/cloud/disabled) |
| `src/eda/model_registry.py` | مدیریت مدل GGUF (SHA-256، دانلود، تنظیمات، RAM، bundle) |
| `src/eda/context_manager.py` | اسمبل context (KB، بودجه توکن، بدون حذف) |
| `src/eda/orchestrator.py` | حلقهٔ agent عیب‌یابی با audit |
| `src/eda/llm_benchmark.py` | harness مستقل benchmark |
| `tests/test_tool_interface.py` | تست‌های رابط tool-calling (۱۲ تست) |
| `tests/test_llm_backend.py` | تست‌های backend abstraction (۹ تست) |
| `tests/test_model_registry.py` | تست‌های registry (۱۱ تست) |
| `tests/test_context_manager.py` | تست‌های context manager (۸ تست) |
| `tests/test_orchestrator.py` | تست‌های orchestrator (۱۰ تست) |
| `tests/test_llm_benchmark.py` | تست‌های benchmark harness (۵ تست) |
| `tests/test_cli_phase3.py` | تست‌های دستورات CLI جدید (۱۲ تست) |
| `docs/LLM_BENCHMARK_RESULTS.json` | نتایج واقعی benchmark |

### فایل‌های تغییرکرده

| فایل | تغییر |
|---|---|
| `src/eda/cli.py` | import ماژول‌های جدید + ۷ دستور جدید (`orchestrate`, `backend`, `models`, `model-settings`, `ram`, `context`, `tools`, `llm-benchmark`) + راهنما. |
| `docs/MODEL_PREPARATION.md` | بازنویسی کامل برای معماری جدید. |

### فایل‌های دست‌نخورده (سازگاری)

`llm_engine.py`، `config.py`، `reasoning.py`، `benchmark.py`، تمام موتورهای
`engines/`، `database.py`، `sessions.py`، `measurements.py`،
`knowledge_base.py`، `documents.py`، `migrations.py`، `safety.py`،
`schemas.py`، `reporting.py`، `learning.py`، `retrieval.py`، و همهٔ تست‌های
موجود دست‌نخورده ماندند. `prepare_model.py` نیز حفظ شد (مسیر legacy).

---

## ۱۳) خلاصهٔ اجرایی

- LLM از paraphraser به **orchestrator** ارتقا یافت؛ موتورهای تخصصی محاسبه
  را انجام می‌دهند و LLM فقط هدایت می‌کند.
- رابط tool-calling استاندارد با JSON، provenance و خطاهای ساختاریافته.
- backend persistent (llama-server) جایگزین اجرای یک-پردازش-برای-هر-درخواست.
- مدل مستقل از سورس، GGUF، با SHA-256 و دانلود صریح و قابل‌انتقال.
- Context Manager با بودجهٔ توکن و بدون حذف داده.
- audit که جعل ولتاژ/pinout/spec را رد می‌کند.
- benchmark مستقل روی ۴ کیس؛ نتایج **واقعی** برای stub و cloud در این
  sandbox اندازه‌گیری شد (cloud: Gap/Contradiction/Refusal = 1.0، Unsourced
  = 0.0).
- ۳۵۳ تست عبور (+۶۷ جدید، ۰ regression)؛ همه offline.
- اعداد GGUF واقعی باید روی ویندوز هدف اندازه‌گیری شوند؛ دستور دقیق
  داده شد. بزرگ‌ترین مدل فرض نشد.

هدف ساختن chatbot نبود؛ هدف ساختن سیستم تحلیل مهندسی بود که LLM آن را
هدایت می‌کند و موتورهای تخصصی محاسبات و اعتبارسنجی را انجام می‌دهند. این
هدف محقق شد.
