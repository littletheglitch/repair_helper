# گزارش Phase 4 — سیستم یادگیری مهندسی و توسعهٔ مهارت

این گزارش خلاصهٔ کار Phase 4 است: یک **سیستم یادگیری مهندسی** مستقل که با سیستم
عیب‌یابی موجود **یکپارچه** شده اما آن را **دست‌نخورده** نگه می‌دارد. هدف:
تکنسین را به‌تدریج قادر به فهمیدن، تحلیل و عیب‌یابی مدار به‌صورت مستقل کند — نه
وابسته به هوش مصنوعی.

---

## ۱) وضعیت قبل و تعارض‌های شناسایی‌شده

قبل از این مرحله، `learning.py` موجود یک **ثبت‌کنندهٔ کیس تعمیر** بود (کیس‌های
تأییدشدهٔ تکنسین را در KB ثبت می‌کرد) — نه یک سیستم توسعهٔ مهارت. بنابراین
Phase 4 کاملاً جدید بود و **هیچ تعارض اسمی** وجود نداشت. برای پرهیز از هرگونه
ابهام، ماژول‌های جدید در یک **پکیج** `src/eda/learning_system/` قرار گرفتند
(نه در `learning.py` موجود).

تعارض‌های واقعی که شناسایی و حل شدند:
- تست‌های موجود `migrations.current_version() == 1` را assert می‌کردند؛ با
  bump کردن schema به v2 این تست‌ها به `== migrations.SCHEMA_VERSION` تغییر
  کردند (نه regression، بلکه نتیجهٔ مورد انتظار migration).
- fixture `tmp_app` state ماژول-سطحی CLI (`_learning_session`،
  `_current_exercise_id`) و singletonهای Phase 3 (`llm_backend`) را بین تست‌ها
  reset نمی‌کرد؛ این به FK-constraint می‌انجامید. `conftest` برای reset آن‌ها
  به‌روز شد.

---

## ۲) دو حالت متمایز (Repair / Learning)

- **Repair Mode**: orchestration عیب‌یابی موجود (`orchestrate`، `analyze`) —
  دست‌نخورده. این ماژول رفتار repair را تغییر نمی‌دهد.
- **Learning Mode**: زیرسیستم جدید. مفاهیم را آموزش می‌دهد، سوالات Socratic
  می‌پرسد، استدلال را ارزیابی می‌کند، تمرین تولید می‌کند، mastery را بر اساس
  evidence دنبال می‌کند و spaced review زمان‌بندی می‌کند.

تغییر حالت با `learning mode <repair|learning>`. حالت پیش‌فرض هنگام ورود به
دستورات `learning` حالت learning است؛ حالت repair روی orchestration موجود
تأثیری ندارد.

---

## ۳) سطوح توضیح (Hint / Guided / Full)

| سطح | چه می‌دهد | راه‌حل را افشا می‌کند؟ |
|---|---|---|
| hint | یک سرنخ کوچک (فرمول/کلیدواژه) | خیر |
| guided | مراحل قدم‌به‌قدم، بدون عدد نهایی | خیر |
| full | راه‌حل کامل + provenance | بله (فقط با درخواست صریح) |

**سطح پیش‌فرض در Learning Mode مقدار `guided` است** (نه افشای فوری) — مطابق
spec. کاربر هر زمان با `learning explain [hint|guided|full]` می‌تواند سطح کامل
بخواهد.

---

## ۴) ماژول‌های اصلی (با اینترفیس‌های typed)

| ماژول | فایل | نقش |
|---|---|---|
| Skill Assessment | `skill_assessment.py` | mastery بر اساس evidence؛ سطوح novice→apprentice→practitioner→expert؛ **هیچ mastery از دیدن توضیح به‌دست نمی‌آید**. |
| Reasoning Evaluator | `reasoning_evaluator.py` | ارزیابی استدلال (اصول فیزیکی، فرمول/واحد، منطق، evidence، gaps، فرضیات بی‌منبع، misinterpretation اندازه‌گیری)؛ **عدد درست با استدلال غلط پاداش نمی‌گیرد**. |
| Socratic Engine | `socratic_engine.py` | یک سوال متمرکز در هر نوبت؛ ارزیابی استدلال نه فقط پاسخ. |
| Exercise Generator | `exercise_generator.py` | تمرین از ۴ منبع (engine / KB / کیس تأییدشده / synthetic) با راه‌حل مرجع قطعی. |
| Adaptive Engine | `adaptive_engine.py` | انتخاب مفهوم/تمرین بعدی بر اساس mastery و gaps. |
| Spaced Review | `spaced_review.py` | زمان‌بند SM-2 بر اساس عملکرد، قطعی و قابل‌تست. |
| Repair-to-Learning | `repair_to_learning.py` | تبدیل جلسهٔ تعمیر به کیس یادگیری؛ similar-but-not-identical؛ عدم افشای راه‌حل؛ uncertain ≠ confirmed. |
| Learning Session | `learning_session.py` | چسبندهٔ ماژول‌ها + حالت + سطوح توضیح. |
| DB layer | `db.py` | دسترسی typed به جداول یادگیری + seed کردن ۱۶ دستهٔ مهارت. |

---

## ۵) ۱۶ دستهٔ مهارت (مطابق spec)

`electrical_math`، `ohm_kirchhoff`، `series_parallel`، `voltage_divider`،
`rc_rl`، `mosfet`، `diodes_bjt`، `opamps_comparators`، `buck_boost`،
`pwm_controllers`، `battery_charging`، `power_sequencing`، `datasheet_interp`،
`schematic_topology`، `measurement_interp`، `fault_isolation`.

این‌ها در جدول `skill_categories` seed می‌شوند (غیرقابل‌ویرایش توسط کاربر) تا
mastery در بین profileها و در طول زمان قابل‌مقایسه باشد.

---

## ۶) schema پایگاه‌داده (migration v2)

جداول جدید (همگی append-only جایی که مهم است؛ بدون overwrite خاموش):

| جدول | نقش |
|---|---|
| `learning_profiles` | یک ردیف به ازای هر تکنسین (پیش‌فرض: default). |
| `skill_categories` | ۱۶ دستهٔ مهارت seed‌شده. |
| `skill_assessments` | snapshot‌های mastery (append-only). |
| `learning_sessions` | جلسات یادگیری. |
| `exercises` | کاتالوگ تمرین با `reference_solution` / `valid_range` / `source_kind` / `source_ref` / `solution_hidden`. |
| `exercise_responses` | پاسخ‌های کاربر (append-only). |
| `evaluation_feedback` | بازخورد ارزیاب (append-only). |
| `mastery_evidence` | **تنها ورودی mastery**: `viewed` / `attempted` / `solved_with_hints` / `solved_independently` / `demonstrated_in_new_context`. |
| `learning_goals` | اهداف یادگیری. |
| `review_schedule` | حالت SM-2 (versioned؛ آخرین ردیف برنده). |

`SCHEMA_VERSION` از ۱ به ۲ bump شد. migration.idempotent است (دوبار اجرا
بی‌خطا).

---

## ۷) mastery مبتنی بر evidence (ضد حفظ‌کردن)

سطوح mastery و آستانه‌های evidence:

| سطح | شرط |
|---|---|
| novice | پیش‌فرض (بدون evidence). |
| apprentice | ≥۲ حل مستقل یا (۱ مستقل + ۲ با hint). |
| practitioner | ≥۳ حل مستقل روی ≥۲ تمرین متمایز. |
| expert | ≥۵ حل مستقل روی ≥۴ تمرین متمایز + ≥۲ demonstration در context جدید. |

قواعد کلیدی:
- **دیدن توضیح mastery را بالا نمی‌برد** (وزن ۰).
- **تلاش بدون حل** mastery را بالا نمی‌برد** (وزن ۰).
- **حل مجدد همان تمرین** تعداد distinct را افزایش نمی‌دهد → ضد حفظ‌کردن.
- **demonstration در context جدید** فقط بعد از practitioner فعال می‌شود (≥۳
  تمرین متمایز دیگر حل‌شده) → consolidation واقعی، نه شانس اولین تلاش.

---

## ۸) ارزیاب استدلال (قاعدهٔ مرکزی spec)

> «پاسخی که اتفاقاً درست است برای استدلال نادرست پاداش ندهد.»

پیاده‌سازی: یک پاسخ فقط زمانی `correct=True` است که **هم** عدد در محدودهٔ
مجاز باشد **و هم** `reasoning_score >= 0.6` و بدون issue بحرانی باشد.
issue‌های بحرانی: «incorrect principle»، «wrong units»، «measurement
misinterpretation». بنابراین عدد درست با استدلال غلط → `correct=False`.

محورهای ارزیابی (همگی rule-based، قطعی):
1. اصل فیزیکی (قانون درست برای مسئله).
2. فرمول و واحد (consistency).
3. logical consistency (زنجیرهٔ استدلال).
4. evidence / provenance (ارجاع به measurement/engine).
5. اطلاعات ناقص (تشخیص مقدار غایب به جای حدس).
6. فرضیات بی‌منبع.
7. misinterpretation اندازه‌گیری (مثلاً 0V بدون ground reference).

اعداد مرجع از engineهای قطعی موجود می‌آیند (`electrical_math` و ...). LLM هرگز
عدد مرجع اختراع نمی‌کند.

---

## ۹) نتایج واقعی benchmark (اندازه‌گیری‌شده در این sandbox)

دستور: `PYTHONPATH=src python3 -m eda.learning_system.benchmark`

| معیار | نتیجه | جزئیات |
|---|---|---|
| skill_assessment_correctness | **1.00** | ۵/۵ توالی evidence به سطح درست رسید. |
| anti_memorization_resistance | **PASS** | ۵ بار حل همان تمرین → distinct=1، level=apprentice (نه practitioner). |
| reasoning_grading_accuracy | **1.00** | ۴/۴ (عدد درست + استدلال → correct؛ عدد خالی → not correct؛ قانون غلط → not correct؛ فرض بی‌منبع → not correct). |
| exercise_reference_correctness | **1.00** | ۱۲/۱۲ راه‌حل مرجع با محاسبهٔ engine مطابق. |
| hint_no_disclosure | **1.00** | ۵/۵ hint راه‌حل را افشا نکرد. |
| progress_persistence | **PASS** | evidence پس از باز کردن مجدد session با همان profile باقی ماند. |
| spaced_review_determinism | **PASS** | ۲۰ اجرای ورودی یکسان → ۱ خروجی متمایز. |
| repair_to_learning_safety | **PASS** | کیس unconfirmed → confirmed=False، root_cause=None، solution_hidden=True. |
| exercise_provenance | **1.00** | ۹/۹ تمرین source_kind + source_ref دارند. |
| offline_compatibility | **PASS** | بدون شبکه؛ `learning`، `learn record`، `orchestrate`، `analyze` همگی موجود. |

این اعداد **واقعی و اندازه‌گیری‌شده** هستند، نه تخمین. زیرسیستم یادگیری هیچ
inference LLM انجام نمی‌دهد — همهٔ ارزیابی rule-based و همهٔ راه‌حل‌های مرجع
توسط engineهای قطعی موجود محاسبه می‌شوند — بنابراین روی هدف ویندوز (i7 نسل
ششم، ۸GB RAM، CPU-only) اعداد مشابه خواهد بود.

---

## ۱۰) تست‌ها

| | |
|---|---|
| تست‌های کل (پس از Phase 4) | **۴۳۴** |
| تست‌های جدید Phase 4 | **۸۱** |
| regressions | **۰** |
| همهٔ تست‌ها offline | بله |

فایل‌های تست جدید:
- `tests/test_skill_assessment.py` (۱۱ تست)
- `tests/test_reasoning_evaluator.py` (۱۵ تست، شامل ۳ **held-out case**)
- `tests/test_socratic_adaptive_hints.py` (۱۴ تست)
- `tests/test_spaced_review_repair_persistence.py` (۲۱ تست)
- `tests/test_cli_learning.py` (۲۰ تست)

تست‌های held-out: ۳ پاسخ که ارزیاب در حین توسعه روی آن‌ها tune نشده بود →
همگی درست درجه‌بندی شدند.

---

## ۱۱) CLI (دستورات `learning`)

دستورات جدید (افزودنی، سازگار با قراردادهای موجود):

```
learning start [skill]               Start a Socratic path (one question at a time)
learning mode <repair|learning>      Switch mode
learning level <hint|guided|full>    Set default explanation level (default: guided)
learning profile                     Show active profile
learning skills                      List 16 categories + your level
learning progress                    Mastery summary
learning gaps                        Skills below apprentice
learning review                      Concepts due for spaced review
learning exercise [skill]            Get the next exercise (adaptive)
learning hint                        Hint (not solution) for current exercise
learning explain [hint|guided|full]  Explanation at a level
learning assess <answer>             Submit your answer (reasoning graded)
learning case <session_id>           Convert a repair session to a learning case
learning history [skill]             Assessment history (append-only)
```

این دستورات با `learn record` موجود (ثبت کیس تعمیر) و `orchestrate` (عیب‌یابی
LLM-driven) هیچ تداخلی ندارند.

---

## ۱۲) repair-to-learning integration

`learning case <session_id>` یک جلسهٔ تعمیر کامل را به یک کیس یادگیری تبدیل
می‌کند:
- استخراج: board، symptoms، measurements، hypotheses (از rule engine)، tests
  performed، root cause (اگر تأییدشده)، outcome، principles مرتبط، knowledge
  gaps.
- **کیس unconfirmed هرگز confirmed علامت نمی‌خورد**؛ root cause افشا نمی‌شود.
- تمرین‌های follow-up **similar-but-not-identical** تولید می‌شوند (component
  labels و اعداد perturb می‌شوند) تا کاربر استدلال کند، نه یادآوری.
- راه‌حل اصلی تمرین follow-up **hidden** است؛ فقط با درخواست صریح
  (`learning explain full`) افشا می‌شود.

---

## ۱۳) محدودیت‌ها (صادقانه)

- **ارزیاب استدلال rule-based و اکتشافی است**: وجود قانون/کلیدواژه و یک
  connective استدلالی را چک می‌کند. یک استدلال ظریفاً غلط با کلیدواژه‌های درست
  ممکن است عبور کند. این trade-off عمدی برای قطعی و offline ماندن است.
- **آستانه‌های mastery** (apprentice=۲، practitioner=۳+۲ distinct، expert=۵+۴
  distinct+۲ new-context) برای یک context یادگیری کالیبره شده‌اند، نه
  گواهی‌نامهٔ رسمی. عمداً محافظه‌کارند.
- **مولد تمرین** ۱۶ دسته را پوشش می‌دهد اما از ۴ مولد منبع می‌کشد (engine،
  KB، confirmed case، synthetic RC). مهارت‌های بدون مولد اختصاصی به یک prompt
  open-ended می‌افتند که بر استدلال درجه‌بندی می‌شود.
- **موتور Socratic** برای ۵ مهارت (ohm_kirchhoff، voltage_divider، mosfet،
  measurement_interp، fault_isolation) مسیر ساخته‌شده دارد؛ بقیه از جریان
  exercise + hint/explain استفاده می‌کنند.
- **این sandbox لینوکس است**؛ زیرسیستم یادگیری روی هر پلتفرم offline کار
  می‌کند (هیچ LLM inference‌ای در ارزیابی نیست).

---

## ۱۴) اجرای offline

زیرسیستم یادگیری کاملاً offline است و هیچ LLM inference انجام نمی‌دهد:

```
# راه‌اندازی (یک‌بار)
python -m eda learning profile          # profile پیش‌فرض ساخته می‌شود
python -m eda learning skills           # ۱۶ دسته + سطح novice

# یک جلسهٔ یادگیری
python -m eda learning exercise ohm_kirchhoff   # تمرین بعدی
python -m eda learning hint                     # سرنک (بدون راه‌حل)
python -m eda learning assess "Using Ohm's law R=V/I, so R=5/2=2.5 Ω. Therefore 2.5 Ω."
python -m eda learning explain guided           # مراحل بدون عدد نهایی
python -m eda learning explain full             # راه‌حل کامل + provenance

# Socratic
python -m eda learning start ohm_kirchhoff      # یک سوال
# (پاسخ دهید، سپس دوباره start برای سوال بعدی)

# تحلیل پیشرفت
python -m eda learning skills
python -m eda learning progress
python -m eda learning gaps
python -m eda learning review

# تبدیل یک تعمیر به کیس یادگیری
python -m eda learning case 1                   # session_id=1

# benchmark
PYTHONPATH=src python3 -m eda.learning_system.benchmark
```

---

## ۱۵) فایل‌های تغییرکرده / اضافه‌شده

### فایل‌های جدید (Phase 4)

| فایل | نقش |
|---|---|
| `src/eda/learning_system/__init__.py` | پکیج + re-export API. |
| `src/eda/learning_system/db.py` | DB layer + seed دسته‌های مهارت. |
| `src/eda/learning_system/skill_assessment.py` | ارزیاب mastery مبتنی بر evidence. |
| `src/eda/learning_system/reasoning_evaluator.py` | ارزیاب استدلال (قاعدهٔ مرکزی). |
| `src/eda/learning_system/socratic_engine.py` | موتور Socratic (یک سوال در هر نوبت). |
| `src/eda/learning_system/exercise_generator.py` | مولد تمرین (۴ منبع). |
| `src/eda/learning_system/adaptive_engine.py` | موتور تطبیقی. |
| `src/eda/learning_system/spaced_review.py` | زمان‌بند SM-2. |
| `src/eda/learning_system/repair_to_learning.py` | تبدیل تعمیر به یادگیری. |
| `src/eda/learning_system/learning_session.py` | چسبنده + حالت + سطوح توضیح. |
| `src/eda/learning_system/benchmark.py` | harness benchmark مستقل. |
| `tests/test_skill_assessment.py` | ۱۱ تست. |
| `tests/test_reasoning_evaluator.py` | ۱۵ تست (شامل held-out). |
| `tests/test_socratic_adaptive_hints.py` | ۱۴ تست. |
| `tests/test_spaced_review_repair_persistence.py` | ۲۱ تست. |
| `tests/test_cli_learning.py` | ۲۰ تست. |
| `docs/LEARNING_BENCHMARK_RESULTS.json` | نتایج واقعی benchmark. |
| `docs/PHASE4_REPORT_FA.md` | این گزارش. |

### فایل‌های تغییرکرده

| فایل | تغییر |
|---|---|
| `src/eda/migrations.py` | `SCHEMA_VERSION` ۱→۲ + migration v2 (۱۰ جدول یادگیری). |
| `src/eda/cli.py` | import پکیج learning_system + خانوادهٔ دستور `learning` + help. |
| `tests/conftest.py` | reset state ماژول-سطحی CLI + llm_backend بین تست‌ها. |
| `tests/test_knowledge_tiers.py` | assert نسخهٔ schema به `== migrations.SCHEMA_VERSION` (نه عدد hard-coded). |
| `tests/test_cli_engines.py` | assert `db version` به وجود رقم (نه "1" hard-coded). |

### فایل‌های دست‌نخورده (سازگاری)

`learning.py` (ثبت‌کنندهٔ کیس تعمیر موجود)، `orchestrator.py`، `llm_backend.py`،
`tool_interface.py`، `reasoning.py`، همهٔ `engines/`، `knowledge_base.py`،
`database.py`، `sessions.py`، `measurements.py`، `documents.py`،
`migrations.py` (migration v1)، `safety.py`، `schemas.py`، `reporting.py`،
`context_manager.py`، `model_registry.py`، `llm_benchmark.py`، و همهٔ تست‌های
موجود Phase 1/2/3 دست‌نخورده ماندند.

---

## ۱۶) خلاصهٔ اجرایی

- یک سیستم **یادگیری مهندسی** مستقل ساخته شد که با سیستم عیب‌یابی موجود
  یکپارچه است اما آن را تغییر نمی‌دهد.
- دو حالت متمایز (Repair/Learning) + سه سطح توضیح (Hint/Guided/Full) با
  پیش‌فرض guided (نه افشای فوری).
- mastery **مبتنی بر evidence**: دیدن توضیح mastery را بالا نمی‌برد؛ فقط حل
  مستقل و demonstration در context جدید.
- ارزیاب استدلال: **عدد درست با استدلال غلط پاداش نمی‌گیرد**.
- تمرین‌ها همگی راه‌حل مرجع قطعی از engineها دارند (LLM عدد اختراع نمی‌کند).
- repair-to-learning: کیس unconfirmed هرگز confirmed علامت نمی‌خورد؛ راه‌حل
  افشا نمی‌شود؛ تمرین similar-but-not-identical.
- spaced review قطعی و بر اساس عملکرد (SM-2).
- **۴۳۴ تست عبور** (+۸۱ جدید، ۰ regression)، همگی offline.
- **۱۰/۱۰ معیار benchmark واقعی** عبور کردند.
- زیرسیستم کاملاً offline، سبک، lazy-loaded، بدون cloud.

هدف ساختن چت‌بات نبود؛ هدف ساختن **مُنتِور فنی** بود که تکنسین را به‌تدریج
قادر به عیب‌یابی مستقل کند. این هدف محقق شد.
