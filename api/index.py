"""
نقطه‌ی ورود Vercel.

Vercel هر فایل داخل api/ را یک Serverless Function می‌شناسد و اگر متغیری به نام
`app` پیدا کند که اپلیکیشن ASGI باشد، مستقیم اجرا می‌کند. vercel.json تمام
مسیرها را به همین فایل rewrite می‌کند تا روتینگ خودِ FastAPI کار کند.
"""

import os
import sys

# main.py و ماژول‌های protocol/ pages/ central/ updater/ runtime در ریشه‌ی ریپو
# هستند، ولی این فایل داخل api/ اجرا می‌شود؛ پس ریشه را به sys.path اضافه می‌کنیم.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from main import app  # noqa: E402,F401
