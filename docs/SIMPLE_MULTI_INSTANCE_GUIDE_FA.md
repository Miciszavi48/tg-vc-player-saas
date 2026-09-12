# راهنمای ساده اجرای چند ربات روی یک سرور

این راهنما برای کسی نوشته شده که تازه با این پروژه کار می‌کند.
هدف: بتوانید چند ربات مستقل را روی یک سرور اوبونتو اجرا کنید، بدون اینکه به هم دست بزنند.

---

## ۱) اینستنس (Instance) یعنی چه؟

هر «اینستنس» یعنی **یک ربات کامل و مستقل**.

هر اینستنس یک اسم دارد؛ به آن اسم می‌گوییم `INSTANCE_ID` (مثلاً `musicbot` یا `musicbot-b`).
همین یک اسم باعث می‌شود همه‌چیزِ آن ربات جدا شود:

| چیز | برای اینستنس `musicbot` | برای اینستنس `musicbot-b` |
|---|---|---|
| پوشه نصب | `/opt/musicbot` | `/opt/musicbot-b` |
| فایل تنظیمات | `/opt/musicbot/app/config.env` | `/opt/musicbot-b/app/config.env` |
| سرویس systemd | `musicbot-musicbot.service` | `musicbot-musicbot-b.service` |
| دیتابیس | `musicbot_db` | `musicbot_musicbot_b` |
| فضای Redis | `musicbot:musicbot:` | `musicbot:musicbot-b:` |
| کاربر سرویس | `musicbot` | `mb-musicbot-b` |
| فایل قفل | `/run/musicbot-musicbot/` | `/run/musicbot-musicbot-b/` |

**قانون طلایی:** دو ربات هرگز نباید یک توکن، یک دیتابیس، یا یک پورت داشته باشند.

قوانین اسم‌گذاری `INSTANCE_ID`:
- فقط حروف کوچک انگلیسی، عدد و خط تیره (`-`)
- باید با حرف شروع شود
- حداکثر ۲۸ کاراکتر
- مثال درست: `musicbot` ، `musicbot-b` ، `shop-bot`
- مثال غلط: `MusicBot` (حرف بزرگ) ، `1bot` (با عدد شروع شده) ، `my_bot` (زیرخط ممنوع)

---

## ۲) نصب ربات اول

روی این سرور، ربات اول از قبل نصب شده است با این مشخصات:

- اینستنس: `musicbot`
- پوشه: `/opt/musicbot`
- سرویس: `musicbot-musicbot.service`
- دیتابیس: `musicbot_db`

اگر بخواهید از صفر نصب کنید، فقط کافی است **داخل همان پوشه** بروید و بدون هیچ گزینه‌ای اجرا کنید:

```bash
cd /opt/musicbot
sudo ./setup_server.sh
```

اسکریپت خودش این‌ها را تشخیص می‌دهد:
- **مسیر نصب** = همان پوشه‌ای که خود فایل `setup_server.sh` در آن است (نه پوشه‌ای که در آن ایستاده‌اید)
- **اسم اینستنس** = اسم همان پوشه (`/opt/musicbot` ← `musicbot`) و یک‌بار از شما تأیید می‌گیرد

بار اول، اگر توکن نداشته باشید، خود اسکریپت سؤال می‌پرسد:

```
  Bot token from @BotFather: ...
  API_ID from my.telegram.org: ...
  API_HASH from my.telegram.org: ...
  Developer/Owner Telegram user id: ...
```

جواب‌ها مستقیم در فایل `/opt/musicbot/app/config.env` ذخیره می‌شوند (توکن روی صفحه چاپ نمی‌شود).

اگر ترجیح می‌دهید دستی پر کنید:

```bash
sudo nano /opt/musicbot/app/config.env
sudo ./setup_server.sh
```

> نکته: اجرای دوباره‌ی `sudo ./setup_server.sh` خطرناک نیست. اسکریپت «تکرارپذیر» است،
> رمزها و توکن شما را نگه می‌دارد و فقط چیزهای جاافتاده را می‌پرسد.

---

## ۳) نصب ربات دوم در پوشه‌ای دیگر

فقط کافی است اسم و پوشه را عوض کنید. کدها را کپی کنید و بعد:

```bash
sudo mkdir -p /opt/musicbot-b
sudo cp -r /opt/musicbot/. /opt/musicbot-b/
sudo rm -f /opt/musicbot-b/app/config.env    # تنظیمات ربات اول را نبرید
sudo rm -rf /opt/musicbot-b/venv /opt/musicbot-b/var

cd /opt/musicbot-b
sudo ./setup_server.sh --health-port 18111 --metrics-port 19111
```

اسم `musicbot-b` خودکار از روی اسم پوشه گرفته می‌شود؛ لازم نیست `--instance` یا `--path` بدهید.

اسکریپت خودش این کارها را می‌کند:
- پوشه و محیط پایتون (venv) می‌سازد
- کاربر لینوکسی `mb-musicbot-b` می‌سازد
- دیتابیس `musicbot_musicbot_b` را می‌سازد
- سرویس `musicbot-musicbot-b.service` را می‌سازد و روشن می‌کند

بعد توکن ربات دوم را بگذارید:

```bash
sudo nano /opt/musicbot-b/app/config.env
cd /opt/musicbot-b && sudo ./setup_server.sh
```

---

## ۴) کدام مقدارهای `app/config.env` حتماً باید فرق کنند؟

این‌ها **باید** برای هر ربات جدا باشند:

| کلید | چرا |
|---|---|
| `INSTANCE_ID` | اسم یکتای ربات؛ همه‌چیز از روی آن جدا می‌شود |
| `BOT_TOKEN` | توکن تکراری یعنی دو ربات سر گرفتن پیام دعوا می‌کنند |
| `DATABASE_URL` | هر ربات باید دیتابیس خودش را داشته باشد |
| `INSTANCE_ROOT` و `INSTANCE_DATA_DIR` | مسیر پوشه‌ی همان ربات |
| `HEALTH_PORT` و `METRICS_PORT` | دو برنامه نمی‌توانند یک پورت را بگیرند |
| `DEVELOPER_ID` | مالک/ادمین هر ربات |

این‌ها **همیشه ثابت و مشترک** هستند — اسکریپت خودش می‌نویسد و هرگز از شما نمی‌پرسد؛ دست نزنید:

```
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_NAMESPACE_ROOT=musicbot
```

جدا بودن ربات‌ها در Redis از راه **پیشوند کلیدها** انجام می‌شود: `musicbot:<اسم اینستنس>:`
پس یک Redis مشترک کاملاً امن است. PostgreSQL و Redis هم به‌صورت سرویس مشترک می‌مانند.

> اگر برای ربات دوم توکن یا دیتابیس ربات اول را بگذارید، اطلاعات قاطی نمی‌شود چون ربات
> با خطای واضح بالا نمی‌آید — ولی ربات کار نمی‌کند. پس حتماً جدا بگذارید.

---

## ۵) دیتابیس و پورت جدا

**دیتابیس:** هر ربات یک دیتابیس PostgreSQL جدا. اسکریپت خودش می‌سازد.
داخل هر دیتابیس یک جدول به اسم `bot_instance_metadata` هست که می‌گوید این دیتابیس مال کدام ربات است.
اگر ربات دیگری بخواهد از آن استفاده کند، این پیام را می‌دهد و بالا نمی‌آید:

```
Database instance ownership mismatch: this database belongs to 'musicbot', not 'musicbot-b'.
```

این یعنی محافظ کار می‌کند و اطلاعات مشتری‌ها قاطی نمی‌شود.

**پورت:** اگر پورت ندهید، مقدارش `0` می‌شود یعنی خاموش.
اگر می‌خواهید سلامت ربات را چک کنید، پورت بدهید. برای ربات اول این سرور:

```bash
curl http://127.0.0.1:18110/     # {"status":"ok","instance_id":"musicbot"}
curl http://127.0.0.1:19110/     # musicbot_up{instance_id="musicbot"} 1
```

برای اینکه ببینید پورتی آزاد است یا نه:

```bash
ss -ltn | grep 18111
```

---

## ۶) دستورهای روزمره (روشن، خاموش، وضعیت، لاگ)

هر ربات یک ابزار کوچک به اسم `musicbotctl` در پوشه‌ی خودش دارد:

```bash
/opt/musicbot/musicbotctl status     # وضعیت
/opt/musicbot/musicbotctl restart    # ری‌استارت
/opt/musicbot/musicbotctl stop       # خاموش
/opt/musicbot/musicbotctl start      # روشن
/opt/musicbot/musicbotctl logs       # دیدن زنده‌ی لاگ (با Ctrl+C خارج شوید)
```

برای ربات دوم دقیقاً همان‌ها ولی از پوشه‌ی خودش:

```bash
/opt/musicbot-b/musicbotctl status
```

با systemd هم می‌شود (نتیجه یکی است):

```bash
sudo systemctl status musicbot-musicbot.service
sudo systemctl restart musicbot-musicbot.service
sudo journalctl -u musicbot-musicbot.service -f
```

> مهم: هر دستور فقط روی **همان ربات** اثر دارد. `restart` ربات اول به ربات دوم کاری ندارد.

---

## ۷) آپدیت امن یک ربات

کد جدید را در یک پوشه‌ی جدا بگذارید (مثلاً `/root/release-new`) و بعد:

```bash
sudo /root/release-new/setup_server.sh --upgrade --instance musicbot
```

یا کوتاه‌تر:

```bash
/opt/musicbot/musicbotctl upgrade /root/release-new
```

این کار فقط همان ربات را آپدیت می‌کند. ربات دوم دست‌نخورده می‌ماند.
فایل `app/config.env` و توکن شما هم پاک نمی‌شود.

> اسکریپت پوشه‌ی ریلیز را با پوشه‌ی نصب اشتباه نمی‌گیرد: با `--instance` از روی
> لیست نصب‌شده‌ها (`/etc/musicbot/instances/`) مسیر درست را پیدا می‌کند و اگر
> نتواند، با پیام واضح متوقف می‌شود.

---

## ۸) اجرای Alembic (به‌روزرسانی جدول‌های دیتابیس)

معمولاً لازم نیست دستی بزنید؛ اسکریپت نصب خودش انجام می‌دهد.
اگر خواستید دستی بزنید، از ابزار خود ربات استفاده کنید تا فقط روی دیتابیس **همان ربات** اجرا شود:

```bash
/opt/musicbot/musicbotctl migrate
```

برای دیدن نسخه‌ی فعلی:

```bash
cd /opt/musicbot/app
sudo -u musicbot env MUSICBOT_ENV_FILE=/opt/musicbot/app/config.env PYTHONPATH=/opt/musicbot \
  /opt/musicbot/venv/bin/alembic current
```

نسخه‌ی درست الان این است: `0033_instance_database_ownership`

> هشدار: هیچ‌وقت Alembic را بدون `MUSICBOT_ENV_FILE` اجرا نکنید، چون ممکن است روی دیتابیس اشتباه اجرا شود.

---

## ۹) بکاپ و بازگردانی

**بکاپ گرفتن (دیتابیس همان ربات):**

```bash
/opt/musicbot/musicbotctl backup
```

فایل در این مسیر ساخته می‌شود: `/opt/musicbot/var/backups/`

**بکاپ کامل دستی (دیتابیس + تنظیمات):**

```bash
STAMP=$(date +%Y%m%d_%H%M%S)
sudo -u postgres pg_dump musicbot_db | gzip -9 | sudo tee /root/musicbot_db_$STAMP.sql.gz >/dev/null
sudo cp /opt/musicbot/app/config.env /root/env_$STAMP.backup
```

**برگرداندن (فقط وقتی لازم شد):**

```bash
/opt/musicbot/musicbotctl stop
gzip -dc /root/musicbot_db_20260717_110418.sql.gz | sudo -u postgres psql -d musicbot_db
/opt/musicbot/musicbotctl start
```

> قبل از هر کار خطرناک، اول بکاپ بگیرید. بکاپ را هم یک‌بار تست کنید که باز می‌شود:
> `gzip -t فایل.sql.gz`

---

## ۱۰) حذف یک ربات بدون آسیب به بقیه

```bash
/opt/musicbot-b/musicbotctl uninstall
```

این دستور فقط سرویس همان ربات را خاموش و پاک می‌کند.
**دیتابیس، فایل `app/config.env`، بکاپ‌ها و پوشه دست‌نخورده می‌مانند** تا اگر پشیمان شدید برگردید.

اگر واقعاً می‌خواهید همه‌چیز آن ربات پاک شود (برگشت‌ناپذیر است):

```bash
sudo rm -rf /opt/musicbot-b
sudo -u postgres psql -c "DROP DATABASE musicbot_musicbot_b;"
sudo -u postgres psql -c "DROP ROLE musicbot_musicbot_b;"
sudo userdel mb-musicbot-b
```

> حواستان باشد اسم درست را بنویسید. ربات‌های دیگر اسم دیگری دارند و به آن‌ها کاری نداشته باشید.

---

## ۱۱) فایل تنظیمات کجاست؟

فقط و فقط یک فایل تنظیمات وجود دارد:

```
/opt/<اسم اینستنس>/app/config.env
```

مثال: `/opt/musicbot/app/config.env` و `/opt/musicbot-b/app/config.env`

اگر از نسخه‌های قدیمی‌تر فایل `/opt/musicbot/.env` دارید، اسکریپت نصب خودش:
1. آن را به `app/config.env` منتقل می‌کند (توکن و رمزها حفظ می‌شوند)،
2. یک نسخه‌ی پشتیبان با نام `.env.migrated.<تاریخ>` نگه می‌دارد،
3. فایل قدیمی را برمی‌دارد تا دیگر با فایل اصلی قاطی نشود.

> بعد از نصب، فقط `app/config.env` را ویرایش کنید. ربات فایل `.env` را دیگر نمی‌خواند.

---

## ۱۲) خطاهای رایج و راه‌حل ساده

**۱. `INSTANCE_ID is required`**
یعنی در فایل تنظیمات اسم اینستنس نیست.
راه‌حل: در `/opt/musicbot/app/config.env` این خط را اضافه کنید: `INSTANCE_ID=musicbot`
(اگر با `sudo ./setup_server.sh` نصب کنید، خودش می‌نویسد.)

**۲. `INSTANCE_ID must match ^[a-z][a-z0-9-]{0,27}$`** یا
`Cannot derive an instance id from folder name '...'`
یعنی اسم پوشه برای اسم اینستنس مناسب نیست (حرف بزرگ یا زیرخط دارد).
راه‌حل: یا اسم پوشه را درست کنید (`musicbot-b`)، یا خودتان اسم بدهید:

```bash
sudo ./setup_server.sh --instance musicbot-b
```

**۳. `Refusing to install musicbot-musicbot.service alongside it`**
یعنی یک سرویس قدیمی روی همان پوشه وجود دارد.
راه‌حل: اگر می‌خواهید سرویس قدیمی برداشته شود:

```bash
sudo ./setup_server.sh --instance musicbot --path /opt/musicbot --adopt-legacy-unit
```

**۴. `Database instance ownership mismatch`**
یعنی این دیتابیس مال ربات دیگری است.
راه‌حل: برای این ربات یک دیتابیس جدا بسازید یا `--db-name` درست بدهید.

**۵. `Instance 'musicbot' already owns .../musicbot.lock`**
یعنی همین ربات از قبل روشن است.
راه‌حل: اول `musicbotctl stop` بزنید، بعد روشن کنید.

**۶. `Redis lease is already held`**
یعنی ربات قبلی درست خاموش نشده (مثلاً کشته شده) و قفلش تا حداکثر ۹۰ ثانیه می‌ماند.
راه‌حل: ۹۰ ثانیه صبر کنید؛ خودش درست می‌شود. systemd هم خودکار دوباره تلاش می‌کند.

**۷. `address already in use`**
یعنی پورت تکراری است.
راه‌حل: برای ربات دوم پورت دیگری بدهید: `--health-port 18111 --metrics-port 19111`

**۸. `Read-only file system` یا `Permission denied`**
یعنی سرویس اجازه‌ی نوشتن ندارد.
راه‌حل: دوباره اسکریپت نصب را اجرا کنید تا دسترسی‌ها و سرویس درست ساخته شوند.

**۹. `Port 18110 is already registered to instance ...`**
یعنی پورت را قبلاً به ربات دیگری داده‌اید.
راه‌حل: پورت دیگری انتخاب کنید. برای دیدن ربات‌های نصب‌شده:

```bash
ls /etc/musicbot/instances/
cat /etc/musicbot/instances/musicbot.conf
```

**۱۰. `pytgcalls is not available — voice chat features will be disabled`**
یعنی پکیج صدا درست نصب نشده و پخش موزیک کار نمی‌کند.
راه‌حل: پکیج درست `py-tgcalls` است، نه `pytgcalls`:

```bash
/opt/musicbot/venv/bin/pip uninstall -y pytgcalls
/opt/musicbot/venv/bin/pip install "py-tgcalls[pyrogram]"
/opt/musicbot/venv/bin/pip install --force-reinstall \
  https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip
/opt/musicbot/musicbotctl restart
```

> نکته: بعد از نصب `py-tgcalls` یا `pyromod` حتماً آخر از همه Kurigram را دوباره نصب کنید،
> وگرنه pyrogram معمولی جای آن می‌نشیند و ربات خراب می‌شود.

**۱۱. ربات روشن نمی‌شود و لاگ چیزی نشان نمی‌دهد**
اول وضعیت و لاگ را ببینید:

```bash
sudo systemctl status musicbot-musicbot.service
sudo journalctl -u musicbot-musicbot.service -n 50 --no-pager
```

اگر نوشته بود `configure BOT_TOKEN in ...` یعنی توکن نگذاشته‌اید.

---

## خلاصه‌ی خیلی کوتاه

```bash
# ربات اول — فقط برو داخل پوشه و اجرا کن
sudo ./setup_server.sh

# ربات دوم — همان دستور، فقط پورت‌ها را جدا بده
cd /opt/musicbot-b && sudo ./setup_server.sh --health-port 18111 --metrics-port 19111

# کار روزمره
/opt/musicbot/musicbotctl status
/opt/musicbot/musicbotctl logs
/opt/musicbot/musicbotctl restart
```

سه چیز را یادتان باشد:
1. دستور اصلی فقط `sudo ./setup_server.sh` است (بقیه گزینه‌ها پیشرفته‌اند).
2. تنظیمات همیشه در `/opt/<اسم اینستنس>/app/config.env` است.
3. `REDIS_URL` و `REDIS_NAMESPACE_ROOT` ثابت‌اند؛ دست نزنید.

برای جزئیات فنی بیشتر، فایل کامل انگلیسی را ببینید:
`docs/MULTI_INSTANCE_DEPLOYMENT.md`
