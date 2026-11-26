# نصب نسخه اصلاح‌شده 1.5.1 در پروژه مادر

1. از پوشه فعلی `ElectronicsDiagnostic` یک نسخه پشتیبان بگیر.
2. ZIP یکپارچه را در مسیر موقت Extract کن.
3. محتویات پروژه `ElectronicsDiagnostic` بسته را روی سورس فعلی کپی کن؛ پوشه‌های دیتابیس، مدل‌ها و داده‌های شخصی فعلی را با نمونه‌های بسته جایگزین نکن.
4. بررسی کن پوشه `ExpansionPack` و فایل `src/eda/engines/expansion_pack.py` وجود داشته باشند و در `src/eda/engines/base.py` ثبت `expansion_pack` باشد.
5. در PowerShell از ریشه پروژه اجرا کن:

```powershell
python -m pip install -e .
python -m eda
```

در REPL:

```text
engine expansion_pack capabilities
engine expansion_pack dispatch --engine_name power_on_sequence --platform desktop --payload_json '{"platform":"desktop","measurements":[]}'
engine expansion_pack ingest_file --path "D:\RepairArchive\thread.html" --category forum_post --source_url "https://example.invalid/thread/123"
```

`ingest_file` فقط یک فایل محلی مشخص را در هر فراخوانی می‌گیرد؛ پوشه را crawl نمی‌کند. PDF فقط وقتی متن قابل انتخاب داشته باشد و `pypdf` یا `PyMuPDF` نصب باشد استخراج می‌شود. OCR انجام نمی‌شود. BIOS/Firmware dump، شماتیک و BoardView وارد KB اصلی نمی‌شوند.

**توجه:** ZIP یکپارچه سورس برنامه و Expansion Pack را دارد، نه دیتابیس زنده، مدل‌های محلی یا آرشیو HTTrack تو را. آن‌ها را جایگزین نکن.
