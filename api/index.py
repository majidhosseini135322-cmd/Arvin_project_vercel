"""
نقطه‌ی ورود Vercel.

Vercel هر فایل داخل api/ را یک Serverless Function می‌شناسد و اگر متغیری به نام
`app` پیدا کند که اپلیکیشن ASGI باشد، مستقیم اجرا می‌کند. vercel.json تمام
مسیرها را به همین فایل rewrite می‌کند تا روتینگ خودِ FastAPI کار کند.
"""

import os
import sys

# ریشه پروژه (جایی که main.py است)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from main import app
except ImportError as e:
    print(f"ERROR importing main: {e}", file=sys.stderr)
    raise
