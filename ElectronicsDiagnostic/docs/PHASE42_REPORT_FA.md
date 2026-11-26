# گزارش Phase 4.2 — انتقال دانش مهندسی و یکپارچه‌سازی یادگیری دنیای واقعی

هدف این مرحله: تبدیل سناریوهای مهندسی اعتبارسنجی‌شده به تمرین‌های واقعی
قابل استفاده در Learning System و توسعه انتقال دانش در تمام ۱۶ دسته
مهارت بود. ابتدا وضعیت فعلی بررسی شد تا از بازنویسی غیرضروری جلوگیری
شود.

---

## ۱) وضعیت فعلی (قبل از Phase 4.2)

- ۱۶ مهارت در `SKILL_CATEGORIES` موجود بود (از Phase 4).
- ۲۴ سناریوی مهندسی معتبر (از Phase 4.1) به‌صورت فایل داده بود، اما به
  exercise generator متصل نبود.
- فقط ۴ مهارت شکل انتقال داشتند (ohm_kirchhoff, electrical_math,
  voltage_divider, series_parallel).
- ارزیاب استدلال چندمرحله‌ای و تمایز correlation/causation را تشخیص
  نمی‌داد.
- تمرین دیتاشیت‌محور با شماره قطعه/revision/صفحه/منبع وجود نداشت.

---

## ۲) Item 1 — اتصال ۲۴ سناریو به مولد تمرین

هر سناریو اکنون به‌عنوان تمرین قابل‌درجه‌بندی متصل شد:

- `scenario_integration.py`: نگاشت هر سناریو به skill_key دقیق (نه فقط
  category).
- `ExerciseGenerator.generate_scenarios()`: ذخیره و بازگرداند ۲۴ تمرین.
- هر سناریو: reference solution (مخفی), reasoning, source provenance.
- synthetic (۲۰) و confirmed (۴) متمایز (پیشوند exercise_key متفاوت).
- پاسخ تا درخواست صریح کاربر مخفی (`solution_hidden=True`).
- ۹ مهارت پوشش داده شد: measurement_interp (۶), mosfet (۳),
  datasheet_interp (۳), battery_charging (۲), buck_boost (۳),
  diodes_bjt (۲), fault_isolation (۳), opamps_comparators (۱),
  power_sequencing (۱).

---

## ۳) Item 2 — گسترش انتقال دانش به هر ۱۶ مهارت

۱۲ شکل انتقال جدید اضافه شد (۴ مورد موجود + ۱۲ = ۱۶):

| مهارت | شکل انتقال (متفاوت از base) |
|---|---|
| electrical_math | P = V×I (توان، نه مقاومت) |
| ohm_kirchhoff | I = V/R (معکوس R=V/I) |
| series_parallel | جریان شاخه I1 = V/R1 (نه Req) |
| voltage_divider | divider با بار R2‖R_L (نه open) |
| rc_rl | C = τ/R (معکوس τ=R·C) |
| mosfet | تلفات هدایتی P = Id²·Rds (نه Vgs) |
| diodes_bjt | Ib = Ic/beta(forced) (نه Vf) |
| opamps_comparators | بهره invert -Rf/Rin (نه non-invert) |
| buck_boost | duty boost D = 1-Vin/Vout (نه buck) |
| pwm_controllers | زمان soft-start t=Vref/(dV/dt) (نه feedback) |
| battery_charging | زمان CC t=Capacity/I (نه فاز CC/CV) |
| power_sequencing | تأخیر RC t=-RC·ln(1-Vth/Vfinal) (نه rail origin) |
| datasheet_interp | worst-case (max نه typ) (نه استخراج مقدار) |
| schematic_topology | ترتیب وابستگی rail (نه ردیابی net) |
| measurement_interp | تأیید ground reference (نه معنی خوانش) |
| fault_isolation | symptom vs root cause (نه رتبه‌بندی فرض) |

هر شکل: فرمول، واحد و عبارت prompt متفاوت از base (تأیید‌شده). مرجع با
engine قطعی یا منبع فنی معتبر. اعتبارسنجی فیزیکی.

---

## ۴) Item 3 — بهبود ارزیابی استدلال

سه بررسی جدید + اصلاحات:

- `_check_multi_step_reasoning`: تمرین‌های چندمرحله‌ای علامت‌گذاری می‌شوند؛
  پاسخ با کمتر از ۲ مرحله متمایز مردود.
- `_check_correlation_causation`: تشخیص «symptom as cause»؛ نیاز به
  ذکر هم symptom هم root cause یا negation.
- `_check_datasheet_value_class`: «yes safe» به abs-max مردود؛ «use
  typical» به worst-case مردود.
- critical-gate: «no reasoning chain» و «no evidence cited» اضافه شد
  (یک bare number هرگز correct نیست — توسط independent benchmark
  کشف شد).
- principle check: P=V×I برای electrical_math و I=V/R برای series_parallel
  پذیرفته شد.
- تمرین‌های کیفی (reference متنی): answer_in_range=True، استدلال gate است.

### محدودیت‌های Rule-based (صادقانه، مستند)

1. ارزیاب rule-based است (regex heuristic). ۱۲۸ تست انتقال + ۵۶ تست
   استدلال مستقل ۱۰۰٪ عبور می‌کنند، ولی یک استدلال غلط به‌اندازه کافی
   ظریف با کلیدواژه‌های درست ممکن است عبور کند. مجموعه‌های مستقل سیگنال
   قوی هستند، نه اثبات کمال.
2. تشخیص چندمرحله‌ای نیاز به flag دارد (prompt/assumptions حاوی
   «two hypotheses»/«step 1»). مسئله چندمرحله‌ای بدون این markers تشخیص
   داده نمی‌شود.
3. تشخیص correlation/causation نیاز به flag دارد (assumptions حاوی
   «correlation»/«symptom»/«root cause»).
4. تشخیص کلاس مقدار دیتاشیت نیاز به flag دارد (assumptions نام کلاس).
5. **بدون درک معنایی**: ارزیاب نمی‌تواند بفهمد «ولتاژ تقسیم بر جریان»
   یعنی «قانون اهم» مگر کلمات مطابقت داشته باشند. درجه‌بندی بر اساس شکل
   سطحی است، نه معنا. **ادعای درک معنایی کامل بدون اعتبارسنجی مستقل
   ممنوع است و اینجا مطرح نشده.**
6. DATASHEET_REGISTRY دارای ۱۱ مقدار تأییدشده در ۳ قطعه است. تمرین
   دیتاشیت به این مقادیر محدود است؛ مولد هرگز مقدار دیتاشیت اختراع
   نمی‌کند.

---

## ۵) Item 4 — تمرین‌های مبتنی بر دیتاشیت

`datasheet_exercises.py` + `generate_datasheet()`:

- ۱۱ تمرین در ۴ کلاس مقدار: abs_max, recommended, typical, guaranteed.
- قطعات: AO3400A (MOSFET), LM317 (regulator), TP4056 (charger).
- هر تمرین: شماره قطعه، revision، صفحه، source URL در prompt و source_ref.
- DATASHEET_REGISTRY تنها منبع؛ مقدار بدون منبع معتبر تولید نمی‌شود.
- تمرین دیتاشیت بدون دیتاشیت معتبر معرفی نمی‌شود (registry محدود).

---

## ۶) Item 5 — رشد mastery و مقاومت در برابر حفظ

تست‌های `test_phase42_mastery_growth.py`:

- حل همان تمرین ۵ بار → distinct=1 (افزایش نمی‌یابد).
- حل دو تمرین متمایز (base + transfer) → distinct≥۲ (رشد از طریق
  تمرین مستقل).
- پاسخ حفظ‌شده (عدد base) در تمرین transfer → مردود (mastery رشد
  نمی‌کند).
- کلیدهای scenario و transfer متمایز (بدون overlap).
- هر تمرین دیتاشیت: part, revision, page, source URL ذکر می‌شود.

---

## ۷) Item 6 — مرزهای معماری

تست‌های `test_phase42_boundaries.py`:

- تولید تمرین دیتاشیت → ۰ fact به KB عمومی.
- تولید تمرین scenario → ۰ fact به KB.
- تولید تمرین transfer (هر ۱۶ مهارت) → ۰ fact به KB.
- import یک سند → ۰ fact به KB (تأیید مجدد از 4.1).
- دستورات Phase 3 (orchestrate/analyze/learn record) تغییر نکرده.
- کلیدهای دیتاشیت idempotent (بدون تصادف).

---

## ۸) Item 7 — Benchmark مستقل

### Benchmark انتقال دانش (۱۲۸ آزمون، مستقل از dev set)

۱۶ مهارت × ۸ seed = ۱۲۸ مورد (هر مورد: پاسخ صحیح + پاسخ غلط).

| مهارت | پاسخ صحیح | پاسخ غلط رد |
|---|---|---|
| electrical_math | 8/8 | 8/8 |
| ohm_kirchhoff | 8/8 | 8/8 |
| series_parallel | 8/8 | 8/8 |
| voltage_divider | 8/8 | 8/8 |
| rc_rl | 8/8 | 8/8 |
| mosfet | 8/8 | 8/8 |
| diodes_bjt | 8/8 | 8/8 |
| opamps_comparators | 8/8 | 8/8 |
| buck_boost | 8/8 | 8/8 |
| pwm_controllers | 8/8 | 8/8 |
| battery_charging | 8/8 | 8/8 |
| power_sequencing | 8/8 | 8/8 |
| datasheet_interp | 8/8 | 8/8 |
| schematic_topology | 8/8 | 8/8 |
| measurement_interp | 8/8 | 8/8 |
| fault_isolation | 8/8 | 8/8 |
| **کل** | **۱۲۸/۱۲۸ (۱۰۰٪)** | **۱۲۸/۱۲۸ (۱۰۰٪)** |

- ۰ تمرین نامعتبر شناسایی شد.
- ۱ مرجع اشتباه توسط benchmark کشف و اصلاح شد (دیتاشیت prompt
  مقدار گردشده rds_max را نشان می‌داد که با مرجع محاسباتی مغایر بود؛
  دقت نمایش اصلاح شد).

### Benchmark ارزیابی استدلال (۵۶ آزمون)

| دسته | n | نتیجه |
|---|---|---|
| چندمرحله‌ای | 13 | 13/13 ✓ |
| correlation/causation | 13 | 13/13 ✓ |
| datasheet value-class | 23 | 23/23 ✓ |
| فرض پنهان | 3 | 3/3 ✓ |
| misinterpretation اندازه‌گیری | 2 | 2/2 ✓ |
| واحد غلط | 1 | 1/1 ✓ |
| بدون استدلال | 1 | 1/1 ✓ |
| **کل** | **۵۶** | **۵۶/۵۶ (۱۰۰٪)** |

قاعده مرکزی تأیید: عدد درست با منطق غلط/ناقص → not correct.

---

## ۹) Item 8 — اجرای مجدد تست‌های قبلی

| | |
|---|---|
| تست‌های کل پس از Phase 4.2 | **۶۷۱** |
| تست‌های Phase 4.1 (قبل) | ۵۸۲ |
| تست‌های جدید Phase 4.2 | **۸۹** |
| regressions | **۰** |
| همگی offline | بله |

Benchmark Phase 4: هر ۱۰ معیار همچنان PASS (بدون regression).

---

## ۱۰) فایل‌های تغییرکرده / اضافه‌شده (Phase 4.2)

### فایل‌های جدید
| فایل | نقش |
|---|---|
| `src/eda/learning_system/scenario_integration.py` | اتصال ۲۴ سناریو به تمرین |
| `src/eda/learning_system/datasheet_exercises.py` | مولد تمرین دیتاشیت + registry |
| `tests/phase42_transfer_benchmark.py` | داده benchmark انتقال مستقل (۱۲۸) |
| `tests/phase42_reasoning_benchmark.py` | داده benchmark استدلال مستقل (۵۶) |
| `tests/test_phase42_mastery_growth.py` | تست رشد mastery (۵) |
| `tests/test_phase42_boundaries.py` | تست مرزهای معماری (۶) |
| `tests/test_phase42_transfer_benchmark.py` | اجرای benchmark انتقال |
| `tests/test_phase42_reasoning_benchmark.py` | اجرای benchmark استدلال |
| `docs/PHASE42_BENCHMARK_RESULTS.json` | نتایج واقعی benchmark |
| `docs/PHASE42_REPORT_FA.md` | این گزارش |

### فایل‌های تغییرکرده
| فایل | تغییر |
|---|---|
| `src/eda/learning_system/exercise_generator.py` | +generate_scenarios, +generate_datasheet, +۱۲ متد transfer جدید، دیسپچر transfer به ۱۶ مهارت |
| `src/eda/learning_system/reasoning_evaluator.py` | +multi-step, +correlation/causation, +datasheet value-class, +bare-number/evidence در gate, +power/branch در principle, +qualitative answer_in_range |

(بدون migration نیاز — فیلدهای تمرین موجود کافی بود؛ assumptions برای flagging استفاده شد.)

---

## ۱۱) مقایسه با Phase 4.1

| بعد | Phase 4.1 | Phase 4.2 |
|---|---|---|
| مهارت‌های دارای transfer | ۴ | **۱۶** |
| سناریو به‌عنوان تمرین | داده فقط | **۲۴ تمرین متصل** |
| تمرین دیتاشیت | ۰ | **۱۱ (با part/revision/page/source)** |
| تشخیص چندمرحله‌ای | نداشت | **دارد** |
| تشخیص correlation/causation | نداشت | **دارد** |
| bare-number gate | ضعیف | **critical** |
| تست انتقال مستقل | ۰ | **۱۲۸ (۱۰۰٪)** |
| تست استدلال مستقل | ۵۳ | **۵۶ (+۵۶ جدید Phase 4.2)** |
| کل تست | ۵۸۲ | **۶۷۱** |

---

## ۱۲) خلاصه

- هر ۱۶ مهارت اکنون شکل انتقال متمایز دارند (نه number-swap).
- ۱۲۸ آزمون انتقال مستقل: ۱۰۰٪ پاسخ صحیح قبول، ۱۰۰٪ پاسخ غلط رد، ۰
  نامعتبر.
- ۵۶ آزمون استدلال مستقل: ۱۰۰٪ در همه دسته‌ها.
- یک مرجع اشتباه توسط benchmark کشف و اصلاح شد.
- ۲۴ سناریو به تمرین متصل؛ synthetic و confirmed متمایز؛ پاسخ مخفی.
- تمرین دیتاشیت با part/revision/page/source؛ بدون منبع معتبر مقدار
  تولید نمی‌شود.
- mastery فقط از تمرین مستقل رشد می‌کند؛ حفظ‌کردن mastery را افزایش
  نمی‌دهد.
- مرزهای معماری حفظ شد؛ Phase 3 تغییر نکرد؛ ۰ regression.
- **۶۷۱ تست عبور** (+۸۹ جدید)، همگی offline.
- محدودیت‌های rule-based صادقانه مستند شد؛ ادعای درک معنایی کامل نشده.

این مرحله قابلیت واقعی قابل‌آزمایش اضافه کرد، نه صرفاً تعداد تست بیشتر.
