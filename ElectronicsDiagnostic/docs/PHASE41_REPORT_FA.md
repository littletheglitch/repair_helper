# گزارش Phase 4.1 — اعتبارسنجی و دقت مهندسی سیستم یادگیری

این مرحله توسعه قابلیت‌های ظاهری جدید نبود. هدف: ارزیابی صحت فنی، کیفیت
آموزشی و اعتبار نتایج Phase 4 بود. ابتدا مشکلات مستند شدند، سپس اصلاحات
مرحله‌ای انجام شد و تمام تست‌های قبلی مجدداً اجرا شدند.

---

## ۱) مشکلات شناسایی‌شده (۱۲ مورد، مستند قبل از اصلاح)

بررسی مستقل کد Phase 4 نشان داد که تست‌های واحد سبز بودن، ولی چند مشکل
واقعی وجود داشت:

| ID | حوزه | مشکل | وضعیت |
|---|---|---|---|
| D1 | reasoning_evaluator | بررسی فرض پنهان فقط «assume» را می‌گرفت؛ «the rail is 5V» / «let R=5» عبور می‌کرد | اصلاح |
| D2 | reasoning_evaluator | بررسی misinterpretation اندازه‌گیری فقط «0v + no voltage» را می‌گرفت؛ «0V means dead» عبور می‌کرد | اصلاح |
| D3 | reasoning_evaluator | بررسی منطق فقط پاسخ‌های زیر ۶ کلمه را علامت می‌زد؛ پاسخ بلند بدون connective عبور می‌کرد | اصلاح |
| D4 | reasoning_evaluator | نرمال‌سازی واحد برای «kΩ» (به «mω» = میلی) شکسته بود؛ واحدهای ناشناخته (H, W) سکوت قبول می‌شدند | اصلاح |
| D5 | reasoning_evaluator | استخراج پاسخ اولین «= number» را می‌گرفت (اغلب مقدار داده‌شده در وسط استدلال)؛ fallback به آخرین عدد bare (ممکن بود index باشد) | اصلاح |
| D6 | exercise_generator | اعتبارسنجی فیزیکی نبود: 1V@3A → 0.33Ω «بار» (نزدیک اتصال کوتاه) یا 60V@0.001A «بار» (نشتی) تولید می‌شد | اصلاح |
| D7 | exercise_generator | divider می‌توانست degenerate باشد (Vout < 0.3V یا R1+R2 خارج محدوده) | اصلاح |
| D8 | exercise_generator | valid_range فقط ثانیه بود ولی prompt هم ثانیه هم ms می‌خواست؛ پاسخ درست در ms نادرست علامت می‌خورد | اصلاح |
| D9 | exercise_generator | upsert_exercise مرجع را درجا overwrite می‌کرد؛ بازتولید، پاسخ‌های در-flight را نامعتبر می‌کرد | اصلاح |
| D10 | knowledge_transfer | تمرین‌ها number-swap یک الگو بودند (همیشه «V و I → R»)؛ انتقال به شرایط متفاوت مدار نبود | اصلاح |
| D11 | boundaries | مرز (BIOS/schematic/BoardView به KB عمومی وارد نمی‌شوند) ضمنی بود، تست نمی‌شد | تست |
| D12 | boundaries | تست نبود که اسناد اختصاصی به session مربوطه محدود بمانند | تست |

---

## ۲) اصلاحات مرحله‌ای

### Stage 1 — ارزیاب استدلال (D1-D5)
- استخراج پاسخ: اولویت «answer/result/therefore is X» → آخرین «= X unit» →
  trailing «number unit» → آخرین عدد bare با guard ضد label (exercise/step/page).
- بررسی واحد: جدول کانونی (base_dimension, prefix)؛ علامت‌گذالی واحدی که
  بعد پایه با مرجع تفاوت دارد یا ناشناخته است.
- بررسی منطق: gate بر حضور connective (so/therefore/because/...) یا
  step-word (substitut/first/then/...)، مستقل از طول.
- بررسی فرض پنهان: الگوهای «assume X»، «the X is <num>»، «let X = <num>»،
  «X of <num>»؛ skip وقتی مقدار در prompt داده‌شده یا نتیجه کاربر است.
- بررسی misinterpretation: «0V + dead/no power/absent/nothing» بدون ground
  reference.

### Stage 2 — اعتبارسنجی فیزیکی مولد تمرین (D6-D9)
- ماژول جدید `physical_validation.py` با `validate_ohm/divider/rc/power`.
- هر مولد تا ۸ بار پارامتر می‌کشد تا فیزیکی معتبر شود؛ نامعتبر → None
  (تمرین رد می‌شود).
- مرزها: V 0.8-60V، I 1mA-10A، P 100µW-50W، R 0.1Ω-10MΩ، τ 1ns-3600s.
- valid_range آگاه از واحد برای RC (min_s/max_s + min_ms/max_ms +
  accept_units)؛ ارزیاب band متناسب با واحد کاربر را انتخاب می‌کند.
- D9 guard: اگر exercise key قبلاً response دارد، upsert فقط metadata
  non-grading را refresh می‌کند؛ reference/prompt/valid_range فریز می‌شوند.

### Stage 3 — انتقال دانش (D10)
- متد جدید `generate_transfer()` با ۴ شکل مسئله متفاوت (نه number-swap):
  - ohm_kirchhoff: معکوس (R,V → I) به جای (V,I → R)
  - electrical_math: توان (P = V×I) به جای مقاومت
  - voltage_divider: divider با بار (R2||R_L) به جای open-circuit
  - series_parallel: جریان شاخه (I1 = V/R1) به جای مقاومت معادل
- تأیید شده: فرمول، واحد و عبارت prompt همگی متفاوت از base.

### Stage 4 — مرزهای معماری (D11, D12)
- تست‌ها تأیید می‌کنند: import یک سند ۰ fact به KB اضافه نمی‌کند؛
  repair-to-learning ۰ fact اضافه نمی‌کند؛ فقط technician-confirmed cases
  (record_case با confirmed=True) به KB می‌روند و فقط با category=repair-case.
- سند import‌شده برای session A در session B ظاهر نمی‌شود (session-scoped).

---

## ۳) مجموعه آزمون مستقل استدلال (۵۳ پاسخ)

نوشته‌شده جدا از داده‌های توسعه. ۱۳ دسته (A-N)، ۵۳ مورد:

| دسته | n | نتیجه |
|---|---|---|
| A (درست + استدلال درست) | 6 | 6/6 (100%) |
| B (عدد درست + استدلال غلط) | 4 | 4/4 (100%) |
| C (عدد درست + ناقص) | 4 | 4/4 (100%) |
| D (فرض پنهان) | 3 | 3/3 (100%) |
| E (کلیدواژه درست + منطق غلط) | 4 | 4/4 (100%) |
| F (عدد غلط + استدلال خوب) | 3 | 3/3 (100%) |
| G (misinterpretation اندازه‌گیری) | 3 | 3/3 (100%) |
| H (واحد غلط) | 4 | 4/4 (100%) |
| I (بدون استدلال) | 4 | 4/4 (100%) |
| J (درست، عبارت متنوع) | 5 | 5/5 (100%) |
| K (درست، عبارت لبه) | 5 | 5/5 (100%) |
| L (تله استدلال غلط) | 3 | 3/3 (100%) |
| M (قانون جایگزین) | 1 | 1/1 (100%) |
| N (لبه اضافی) | 4 | 4/4 (100%) |
| **کل** | **53** | **53/53 (100%)** |

قاعده مرکزی محقق شد: عدد درست با استدلال غلط/ناقص → not correct، در همه
۵۳ مورد مستقل.

---

## ۴) Benchmark آموزشی (Hint/Guided/Full جدا)

| معیار | نتیجه | جزئیات |
|---|---|---|
| Hint افشا نمی‌کند | PASS | ۰ leak در ۳۰ تمرین |
| Guided عدد نهایی را افشا نمی‌کند | PASS | ۰ leak در ۳۰ تمرین |
| Full راه‌حل + provenance | PASS | |
| پیش‌فرض guided (نه full) | PASS | |
| مقاومت mastery در برابر حفظ | PASS | ۵ حل همان تمرین → distinct=1، level=apprentice (نه practitioner/expert) |
| پاسخ حفظ‌شده در transfer رد می‌شود | PASS | تمرین transfer سوال متفاوت می‌پرسد → عدد حفظ‌شده غلط |
| پاسخ درست transfer قبول | PASS | |
| spaced review قطعی + کوتاه‌شدن | PASS | |

---

## ۵) ۲۴ سناریوی مهندسی واقعی

| دسته | n | نمونه |
|---|---|---|
| measurement | 8 | 19.4V jack / 0V +5VALW؛ 3.3V روی 20V range اما 0.33V روی 2V range؛ 0Ω to ground روی CPU core |
| datasheet | 6 | Rds(on) typ/max؛ Vref=0.8V divider؛ abs max vs operating؛ EN Vih threshold |
| component | 6 | P-MOSFET load switch؛ دیود 0.7V/0.2V؛ الکترولیت 0Ω؛ BJT کوتاه؛ ringing switch-node؛ op-amp gain |
| troubleshooting | 4 | no power + EN low؛ CPU VRM PG low؛ charges only when off (PROCHOT)؛ shorted rail isolation |

هر سناریو reference answer با استدلال + منبع معتبر دارد. سناریوهای
مصنوعی (۷ مورد) از تعمیرات تأییدشده (۵ مورد) جدا هستند (هیچ overlap).

---

## ۶) یکپارچگی (مرزهای معماری)

| بررسی | نتیجه |
|---|---|
| repair-to-learning اندازه‌گیری‌ها را بدون تحریف منتقل می‌کند | PASS |
| repair-to-learning symptoms را verbatim منتقل می‌کند | PASS |
| دستور orchestrate (Phase 3) تغییر نکرده | PASS |
| دستور analyze (Phase 1) تغییر نکرده | PASS |
| دستور learn record (Phase 1) تغییر نکرده | PASS |
| learning و learn record متمایز و بدون تداخل | PASS |
| import سند → ۰ fact به KB | PASS |
| repair-to-learning → ۰ fact به KB | PASS |
| فقط technician-confirmed cases به KB (category=repair-case) | PASS |
| اسناد session-scoped | PASS |

---

## ۷) Benchmark Phase 4 (اجرای مجدد)

همه ۱۰ معیار Phase 4 همچنان عبور می‌کنند:

| معیار | نتیجه |
|---|---|
| skill_assessment_correctness | 1.00 (5/5) |
| anti_memorization_resistance | PASS |
| reasoning_grading_accuracy | 1.00 (4/4) |
| exercise_reference_correctness | 1.00 (12/12) |
| hint_no_disclosure | 1.00 (5/5) |
| progress_persistence | PASS |
| spaced_review_determinism | PASS |
| repair_to_learning_safety | PASS |
| exercise_provenance | 1.00 (9/9) |
| offline_compatibility | PASS |

---

## ۸) نتایج واقعی تست

| | |
|---|---|
| تست‌های کل پس از Phase 4.1 | **۵۸۲** |
| تست‌های Phase 4 (قبل Phase 4.1) | ۴۳۴ |
| تست‌های جدید Phase 4.1 | **۱۴۸** |
| regressions | **۰** |
| همگی offline | بله |

تست‌های جدید:
- `test_phase41_independent_reasoning.py` (۵۳ تست + ۱ خلاصه)
- `test_phase41_educational_benchmark.py` (۸ تست)
- `test_phase41_technician_scenarios.py` (۳۲ تست)
- `test_phase41_boundaries_integration.py` (۱۰ تست)
- `phase41_independent_reasoning_set.py` + `phase41_technician_scenarios.py` (داده)

---

## ۹) فایل‌های تغییرکرده / اضافه‌شده (Phase 4.1)

### فایل‌های جدید
| فایل | نقش |
|---|---|
| `src/eda/learning_system/physical_validation.py` | اعتبارسنجی فیزیکی تمرین‌ها (D6-D8) |
| `tests/phase41_independent_reasoning_set.py` | ۵۳ پاسخ آزمون مستقل |
| `tests/phase41_technician_scenarios.py` | ۲۴ سناریوی مهندسی واقعی |
| `tests/test_phase41_independent_reasoning.py` | تست مجموعه مستقل |
| `tests/test_phase41_educational_benchmark.py` | benchmark آموزشی Hint/Guided/Full |
| `tests/test_phase41_technician_scenarios.py` | تست سناریوهای مهندسی |
| `tests/test_phase41_boundaries_integration.py` | مرزهای معماری + یکپارچگی |
| `docs/PHASE41_BENCHMARK_RESULTS.json` | نتایج واقعی benchmark Phase 4.1 |
| `docs/PHASE41_REPORT_FA.md` | این گزارش |

### فایل‌های تغییرکرده
| فایل | تغییر |
|---|---|
| `src/eda/learning_system/reasoning_evaluator.py` | استخراج پاسخ با اولویت (D5)؛ واحد کانونی (D4)؛ منطق connective (D3)؛ فرض گسترده (D1)؛ misinterpretation گسترده (D2)؛ principle مهارت‌خاص (D-prime)؛ correct-gate با unsupported-assumption |
| `src/eda/learning_system/exercise_generator.py` | اعتبارسنجی فیزیکی (D6-D8)؛ transfer shapes (D10) |
| `src/eda/learning_system/db.py` | D9 guard در upsert_exercise |
| `src/eda/learning_system/benchmark.py` | واحد‌آگاه RC |
| `tests/test_reasoning_evaluator.py` | به‌روز برای valid_range آگاه از واحد |

---

## ۱۰) مقایسه با Phase 4

| بعد | Phase 4 | Phase 4.1 |
|---|---|---|
| تست‌های کل | ۴۳۴ | ۵۸۲ (+۱۴۸) |
| مجموعه آزمون مستقل | ۳ held-out | ۵۳ مورد (۱۰۰٪) |
| اعتبارسنجی فیزیکی تمرین | نبود | ۰ نامعتبر در ۱۰۰ seed |
| انتقال دانش | number-swap | ۴ شکل مسئله متفاوت |
| تست مرز معماری | ضمنی | ۱۰ تست صریح |
| سناریوی مهندسی واقعی | ۰ | ۲۴ (با منبع + استدلال) |
| Benchmark آموزشی Hint/Guided/Full جدا | نبود | ۸ تست جدا |
| defects شناسایی‌شده | — | ۱۲ (همگی اصلاح/تست) |

---

## ۱۱) محدودیت‌ها (صادقانه)

- **ارزیاب استدلال rule-based است.** مجموعه مستقل ۵۳ موردی ۱۰۰٪ عبور
  می‌کند، ولی یک استدلال غلط به‌اندازه کافی ظریف با کلیدواژه‌های درست
  ممکن است عبور کند. مجموعه مستقل سیگنال قوی است، نه اثبات کمال.
- **۲۴ سناریوی مهندسی** ساختاری تأیید می‌شوند (reference + source +
  reasoning)؛ هنوز به عنوان تمرین قابل‌درجه‌بندی در flow مولد سیم‌نشده‌اند
  (Phase 4.2).
- **transfer shapes** ۴ مهارت را پوشش می‌دهند؛ ۱۲ مهارت دیگر هنوز
  transfer shape ندارند.
- **مرزهای اعتبارسنجی فیزیکی** هیوریستیک board-level هستند، نه limitهای
  دقیق دیتاشیت قطعه؛ ۵۰W رد می‌شود ولی ۴۹W قبول — مرز sanity است نه
  derating دقیق.

---

## ۱۲) خلاصه

- ۱۲ مشکل واقعی در Phase 4 با بررسی مستقل شناسایی و در ۴ مرحله اصلاح شد.
- مجموعه آزمون مستقل (۵۳ مورد، جدا از dev set) ۱۰۰٪ درست درجه‌بندی می‌شود.
- Hint/Guided هرگز راه‌حل را افشا نمی‌کنند (۳۰ تمرین × هر کدام)؛ Full با
  provenance افشا می‌کند؛ پیش‌فرض guided است.
- mastery در برابر حفظ‌کردن مقاوم است: حل همان تمرین distinct را بالا
  نمی‌برد؛ پاسخ حفظ‌شده در تمرین transfer (سوال متفاوت) رد می‌شود.
- ۲۴ سناریوی مهندسی واقعی با استدلال و منبع معتبر، مصنوعی جدا از تأییدشده.
- مرزهای معماری صریح تست می‌شوند: اسناد به KB عمومی وارد نمی‌شوند؛
  session-scoped هستند.
- Phase 3 تغییر نکرده؛ Phase 4 benchmark همچنان ۱۰/۱۰.
- **۵۸۲ تست عبور** (+۱۴۸ جدید، ۰ regression)، همگی offline.

این مرحله توسعه نبود؛ اعتبارسنجی بود. اثربخشی آموزشی فقط به‌خاطر عبور
تست‌های واحد ادعا نشده — مجموعه آزمون مستقل، benchmark آموزشی جدا، و
سناریوهای مهندسی واقعی آن را پشتیبانی می‌کنند.
