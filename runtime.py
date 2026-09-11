"""
runtime.py — تشخیص محیط اجرا + لایه‌ی ذخیره‌سازی مشترک.

چرا این فایل اضافه شد؟
    قبلاً هر ماژول خودش `DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))` رو
    تعریف می‌کرد و مستقیم روی دیسک می‌نوشت. روی Railway مشکلی نداشت (دیسک
    نوشتنی + پروسه‌ی همیشه‌روشن)، ولی روی Vercel:
        • فایل‌سیستم read-only است (فقط /tmp نوشتنی و موقتی است)
        • پروسه بین درخواست‌ها فریز/کشته می‌شود → تسک پس‌زمینه اجرا نمی‌شود
        • چند instance موازی بالا می‌آید → حافظه بین‌شان مشترک نیست
    نتیجه: رمز عبور ذخیره نمی‌شد و کانفیگ‌های ساخته‌شده ناپدید می‌شدند.

    اینجا یک منبع حقیقت واحد داریم: Upstash Redis (از طریق REST API، که برای
    serverless درست‌ترین انتخاب است چون stateless است و کانکشن TCP باز
    نگه نمی‌دارد). سازگاری با REDIS_URL معمولی (TCP) و فایل محلی هم حفظ شده،
    پس همین کد بدون تغییر روی Railway هم کار می‌کند.

ترتیب اولویت backend ذخیره‌سازی:
    ۱) Upstash Redis REST  (UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN)
    ۲) Redis معمولی TCP    (REDIS_URL)
    ۳) فایل محلی JSON      (فقط وقتی دیسک پایدار باشد — یعنی غیر serverless)
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("RVG-Gateway")

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None


# ── تشخیص محیط ────────────────────────────────────────────────────────────────
IS_SERVERLESS = bool(
    os.environ.get("VERCEL")
    or os.environ.get("VERCEL_ENV")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    or os.environ.get("FUNCTION_TARGET")
)

# روی serverless فقط /tmp نوشتنی است. پایدار نیست، ولی برای فایل‌های موقتی
# (کش، باینری دانلودی) از کرش‌کردن جلوگیری می‌کند.
DATA_DIR = Path(
    os.environ.get("DATA_DIR") or ("/tmp/rvg-data" if IS_SERVERLESS else "/data")
)

# آیا نوشتن روی DATA_DIR بین دیپلوی‌ها/درخواست‌ها باقی می‌ماند؟
DISK_PERSISTENT = not IS_SERVERLESS


def ensure_data_dir() -> bool:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.warning(f"DATA_DIR قابل ساخت/نوشتن نیست ({DATA_DIR}): {e}")
        return False


# ── Upstash Redis REST ────────────────────────────────────────────────────────
UPSTASH_URL = (
    os.environ.get("UPSTASH_REDIS_REST_URL")
    or os.environ.get("KV_REST_API_URL")
    or ""
).rstrip("/")
UPSTASH_TOKEN = (
    os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    or os.environ.get("KV_REST_API_TOKEN")
    or ""
)
UPSTASH_ENABLED = bool(UPSTASH_URL and UPSTASH_TOKEN)

# ── Redis TCP (سازگاری با نسخه‌ی قبلی) ────────────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "").strip()

# کلیدها — با REDIS_PREFIX می‌توانید چند پنل را روی یک Redis اجرا کنید
PREFIX = os.environ.get("REDIS_PREFIX", "rvg")
K_STATE = f"{PREFIX}:state"
K_SECRET = f"{PREFIX}:secret"
K_SESSION = f"{PREFIX}:sess:"
K_RAILWAY_TOKEN = f"{PREFIX}:railway_token"

_rest_client: Optional[httpx.AsyncClient] = None
_rest_lock = asyncio.Lock()
_tcp_client = None
TCP_CONNECTED = False

if IS_SERVERLESS and not UPSTASH_ENABLED and not REDIS_URL:
    logger.error(
        "⛔ محیط serverless تشخیص داده شد ولی هیچ Redis‌ای تنظیم نشده. "
        "UPSTASH_REDIS_REST_URL و UPSTASH_REDIS_REST_TOKEN را ست کنید، "
        "وگرنه رمز عبور و کانفیگ‌ها ذخیره نمی‌شوند."
    )


async def _rest_conn() -> httpx.AsyncClient:
    global _rest_client
    if _rest_client is None:
        async with _rest_lock:
            if _rest_client is None:
                _rest_client = httpx.AsyncClient(
                    base_url=UPSTASH_URL,
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
    return _rest_client


async def redis_cmd(*args, quiet: bool = False):
    """یک دستور Redis اجرا می‌کند: redis_cmd("SET", k, v, "EX", 60).
    اول Upstash REST، بعد Redis TCP. اگر هیچ‌کدام نبود None برمی‌گرداند."""
    if UPSTASH_ENABLED:
        try:
            client = await _rest_conn()
            r = await client.post("/", json=[str(a) for a in args])
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict):
                if payload.get("error"):
                    raise RuntimeError(payload["error"])
                return payload.get("result")
            return payload
        except Exception as e:
            if not quiet:
                logger.warning(f"Upstash {args[0] if args else '?'} ناموفق: {e}")
            return None
    if TCP_CONNECTED and _tcp_client is not None:
        try:
            return await _tcp_client.execute_command(*[str(a) for a in args])
        except Exception as e:
            if not quiet:
                logger.warning(f"Redis {args[0] if args else '?'} ناموفق: {e}")
            return None
    return None


def redis_enabled() -> bool:
    return UPSTASH_ENABLED or TCP_CONNECTED


def backend_name() -> str:
    if UPSTASH_ENABLED:
        return "upstash"
    if TCP_CONNECTED:
        return "redis"
    return "file" if DISK_PERSISTENT else "none"


async def init_redis():
    """اتصال اولیه. Upstash REST نیازی به اتصال ندارد (stateless است)؛ فقط یک
    PING می‌زنیم تا مطمئن شویم توکن/آدرس درست است."""
    global _tcp_client, TCP_CONNECTED
    if UPSTASH_ENABLED:
        ok = await redis_cmd("PING") == "PONG"
        if ok:
            logger.info("Upstash Redis (REST) متصل شد — state روی Upstash ذخیره می‌شود.")
        else:
            logger.error("⛔ اتصال به Upstash ناموفق بود — URL/TOKEN را بررسی کنید.")
        return
    if not REDIS_URL:
        TCP_CONNECTED = False
        return
    if aioredis is None:
        logger.warning("REDIS_URL ست شده ولی پکیج redis نصب نیست — از فایل محلی استفاده می‌شود.")
        TCP_CONNECTED = False
        return
    try:
        client = aioredis.from_url(
            REDIS_URL, decode_responses=True, socket_connect_timeout=5, socket_timeout=5,
        )
        await client.ping()
        _tcp_client = client
        TCP_CONNECTED = True
        logger.info("Redis متصل شد — ذخیره‌سازی state از این به بعد روی Redis انجام می‌شود.")
    except Exception as e:
        _tcp_client = None
        TCP_CONNECTED = False
        logger.warning(f"اتصال به Redis ناموفق بود ({e}) — از فایل محلی استفاده می‌شود.")


async def redis_watchdog():
    """هر ۱۵ ثانیه اتصال Redis TCP را چک/بازسازی می‌کند تا وضعیت نمایش‌داده‌شده
    در پنل واقعی باشد. روی serverless معنایی ندارد (پروسه زنده نمی‌ماند) و
    Upstash REST هم چیزی برای نگه‌داشتن ندارد، پس در آن حالت‌ها اجرا نمی‌شود."""
    global _tcp_client, TCP_CONNECTED
    if IS_SERVERLESS or UPSTASH_ENABLED or not REDIS_URL or aioredis is None:
        return
    while True:
        await asyncio.sleep(15)
        try:
            if _tcp_client is None:
                _tcp_client = aioredis.from_url(
                    REDIS_URL, decode_responses=True, socket_connect_timeout=5, socket_timeout=5,
                )
            await _tcp_client.ping()
            if not TCP_CONNECTED:
                logger.info("اتصال به Redis دوباره برقرار شد.")
            TCP_CONNECTED = True
        except Exception:
            if TCP_CONNECTED:
                logger.warning("اتصال به Redis قطع شد — موقتاً از فایل محلی استفاده می‌شود.")
            TCP_CONNECTED = False


async def close_redis():
    global _rest_client, _tcp_client
    if _rest_client is not None:
        try:
            await _rest_client.aclose()
        except Exception:
            pass
        _rest_client = None
    if _tcp_client is not None:
        try:
            await _tcp_client.aclose()
        except Exception:
            pass
        _tcp_client = None


# ── کلید/مقدارهای کوچک پایدار (توکن Railway و مشابه آن) ───────────────────────
async def kv_get(key: str) -> Optional[str]:
    if redis_enabled():
        val = await redis_cmd("GET", key)
        if val:
            return val
    return None


async def kv_set(key: str, value: str) -> bool:
    if redis_enabled():
        return await redis_cmd("SET", key, value) is not None
    return False


async def kv_del(key: str) -> bool:
    if redis_enabled():
        return await redis_cmd("DEL", key) is not None
    return False
