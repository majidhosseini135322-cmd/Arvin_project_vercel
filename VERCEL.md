# اجرای پنل روی Vercel (serverless) + Upstash Redis

## چرا قبلاً کار نمی‌کرد؟

روی Railway پروسه همیشه روشن است، دیسک نوشتنی است و فقط یک instance وجود دارد.
روی Vercel هیچ‌کدام از این سه فرض درست نیست:

| فرض کد قبلی | واقعیت Vercel | نتیجه |
|---|---|---|
| `/data` نوشتنی و پایدار است | فایل‌سیستم read-only، فقط `/tmp` نوشتنی و موقتی | `rvg_state.json` و `.rvg_secret` ذخیره نمی‌شدند |
| `SECRET_KEY` روی دیسک می‌ماند | هر کولد استارت یک secret تازه | salt عوض می‌شد → **«رمز عبور اشتباه است»** |
| `asyncio.create_task(save_state())` بعداً اجرا می‌شود | بعد از ریسپانس، فانکشن فریز می‌شود | **کانفیگ ساخته می‌شد ولی ذخیره نمی‌شد** |
| سشن در حافظه کافی است | چند instance موازی، حافظه مشترک نیست | بعد از لاگین برگشت به صفحه‌ی لاگین |
| `RAILWAY_PUBLIC_DOMAIN` دامنه را می‌دهد | وجود ندارد | لینک‌ها با `localhost` ساخته می‌شدند |

## چه چیزی عوض شد؟

- **`runtime.py` (فایل جدید)** — منبع حقیقت واحد برای تشخیص محیط و ذخیره‌سازی.
  ترتیب backend: **Upstash REST → Redis TCP (`REDIS_URL`) → فایل محلی**.
  از REST استفاده می‌کنیم نه کلاینت TCP، چون در serverless کانکشن TCP بین
  درخواست‌ها زنده نمی‌ماند و باعث نشتی کانکشن می‌شود.
- **`SECRET_KEY`** — اولویت env، بعد دیسک پایدار، بعد Redis با `SET NX` (اتمیک،
  پس همه‌ی instanceها یک مقدار می‌گیرند).
- **نوشتن write-through** — همه‌ی `create_task(save_state())` ها شدند `await _persist()`
  که روی serverless قبل از برگشتن ریسپانس می‌نویسد و روی Railway همان debounce قبلی را نگه می‌دارد.
- **سشن‌ها در Redis** با TTL + کش محلی ۵ دقیقه‌ای (تا هر درخواست یک round-trip نخورد).
- **`_apply_state` جایگزین می‌کند، merge نمی‌کند** — وگرنه کانفیگ حذف‌شده روی instance دیگر زنده می‌ماند.
- **`STATE_REFRESH_SECONDS`** — state هر ۱.۵ ثانیه از Redis تازه می‌شود تا instanceها هم‌گام باشند.
- **راه‌اندازی lazy** — رویداد `startup` روی Vercel تضمینی نیست، پس `ensure_ready()` از middleware روی اولین درخواست اجرا می‌شود.
- **توکن Railway** به Redis منتقل شد (یا از `RAILWAY_API_TOKEN` خوانده می‌شود) تا بین دیپلوی‌ها گم نشود.
- **گاردها** — قابلیت‌هایی که TCP/پروسه‌ی همیشه‌روشن می‌خواهند، به‌جای ۵۰۰ مبهم، خطای ۵۰۱ با پیام فارسی روشن می‌دهند.
- **`/api/storage`** — برای دیباگ سریع دیپلوی.

## متغیرهای محیطی

اجباری:

| نام | توضیح |
|---|---|
| `UPSTASH_REDIS_REST_URL` | از داشبورد Upstash (بخش REST API) |
| `UPSTASH_REDIS_REST_TOKEN` | همان‌جا |
| `SECRET_KEY` | یک رشته‌ی ثابت تصادفی. **اکیداً ست کنید** (`openssl rand -base64 32`) |
| `ADMIN_PASSWORD` | رمز اولیه‌ی پنل |

اختیاری: `PUBLIC_DOMAIN` (اگر ست نشود از هدر `Host`/`X-Forwarded-Host` خوانده می‌شود)،
`REDIS_PREFIX` (چند پنل روی یک Redis)، `STATE_REFRESH_SECONDS`، `COOKIE_SECURE`،
`RAILWAY_API_TOKEN`.

> اگر از یکپارچه‌سازی Vercel KV / Upstash استفاده کنید، متغیرهای
> `KV_REST_API_URL` و `KV_REST_API_TOKEN` هم به‌صورت خودکار پشتیبانی می‌شوند.

## دیپلوی

```bash
git add .
git commit -m "serverless support via Upstash Redis"
git push
```

بعد از دیپلوی، `https<دامنه>/api/storage` را باز کنید. باید ببینید:

```json
{ "backend": "upstash", "redis_reachable": true, "secret_source": "env" }
```

اگر `secret_source` شد `temporary ⚠️`، یعنی `SECRET_KEY` ست نشده و لاگین به‌صورت
تصادفی می‌شکند.

## ⚠️ محدودیت مهم و غیرقابل‌دورزدن

**Vercel نه WebSocket دارد و نه TCP خام.** پس این‌ها روی Vercel اجرا نمی‌شوند:

- تونل‌های `vless-ws`، `trojan-ws`، `shadowsocks-ws`
- انواع `xhttp` (به استریم بلندمدت نیاز دارند)
- `mtproto` (پروسه‌ی جدا + پورت TCP)
- Bot TCP Proxy، Zeus SOCKS5، دامنه‌ساز
- بروزرسانی از داخل پنل (فایل‌سیستم read-only)

آنچه **کار می‌کند**: پنل، لاگین، تغییر رمز، ساخت/ویرایش/حذف کانفیگ، گروه‌های
Subscription، صفحه‌ی عمومی، بکاپ/ریستور، و اتصال نودها.

**معماری درست:** پنل روی Vercel، ترافیک روی نودهای Railway/VPS، و اتصال‌شان با
همان قابلیت «اتصال نودها» که در پنل دارید.
