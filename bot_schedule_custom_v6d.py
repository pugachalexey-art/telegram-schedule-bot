# bot_schedule_custom_v6d.py
# v6d (updated) — сповіщення + виправлення форматування:
# - Кнопки: "Підключити сповіщення" / "Відключити сповіщення"
# - Тест через 3 хв після підписки
# - Нагадування за 10 хв до початку пари (job_queue кожні 60 сек)
# - Підписки зберігаються у вкладці Google Sheets "Subscribers"
# - Реальні переносі рядків \n (без екранування), fallback-номер пари без "№"
# - Регулярка для /date виправлена
# УВАГА: сервісній пошті потрібен доступ Editor до таблиці

import os, json, gspread, logging, pytz, math, itertools, re, traceback, locale
from datetime import datetime, timedelta, date
from dateutil.parser import parse as dtparse
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from telegram.error import BadRequest, TelegramError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("schedbot")
KYIV_TZ = pytz.timezone("Europe/Kyiv")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_ID = os.getenv("SHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Schedule")

if not all([BOT_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID]):
    raise RuntimeError("Set BOT_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID env vars")

UA_DAYNAMES = {0:"Понеділок",1:"Вівторок",2:"Середа",3:"Четвер",4:"Пʼятниця",5:"Субота",6:"Неділя"}
UA_TO_EN = {"Понеділок":"Monday","Вівторок":"Tuesday","Середа":"Wednesday","Четвер":"Thursday","Пʼятниця":"Friday","Субота":"Saturday","Неділя":"Sunday"}

try:
    locale.setlocale(locale.LC_COLLATE, "uk_UA.UTF-8")
except Exception:
    pass

# ---------- Google Sheets (READ/WRITE) ----------
def make_gspread_client():
    # Потрібен запис для вкладки Subscribers
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def open_sheet():
    gc = make_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    return sh

def get_records():
    sh = open_sheet()
    ws = sh.worksheet(SHEET_NAME)
    recs = ws.get_all_records()
    logging.info("Loaded %d rows. Columns: %s", len(recs), list(recs[0].keys()) if recs else [])
    return recs

# --- Subscribers persistence ---
SUBS_WS_NAME = "Subscribers"
SUBS_HEADERS = ["chat_id", "enabled", "updated_at"]

def ensure_subs_ws(sh):
    try:
        ws = sh.worksheet(SUBS_WS_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SUBS_WS_NAME, rows=1000, cols=3)
        ws.update([SUBS_HEADERS])
    return ws

def get_subscribers_set():
    sh = open_sheet()
    ws = ensure_subs_ws(sh)
    values = ws.get_all_values()
    if not values or len(values) < 2:
        return set()
    headers = values[0]
    idx_chat = headers.index("chat_id") if "chat_id" in headers else 0
    idx_enabled = headers.index("enabled") if "enabled" in headers else 1
    subs = set()
    for row in values[1:]:
        try:
            chat_id = int(row[idx_chat])
            enabled = str(row[idx_enabled]).strip().lower() in ("1","true","yes","y","on","enable","enabled")
            if enabled:
                subs.add(chat_id)
        except Exception:
            continue
    return subs

def upsert_subscription(chat_id: int, enabled: bool):
    sh = open_sheet()
    ws = ensure_subs_ws(sh)
    values = ws.get_all_values()
    headers = values[0] if values else SUBS_HEADERS
    if not values or headers != SUBS_HEADERS:
        ws.clear()
        ws.update([SUBS_HEADERS])
        values = [SUBS_HEADERS]
    idx = None
    for i, row in enumerate(values[1:], start=2):
        if str(row[0]).strip() == str(chat_id):
            idx = i
            break
    now_str = datetime.now(KYIV_TZ).strftime("%Y-%m-%d %H:%M:%S")
    if idx is None:
        ws.append_row([str(chat_id), "TRUE" if enabled else "FALSE", now_str], value_input_option="USER_ENTERED")
    else:
        ws.update(f"A{idx}:C{idx}", [[str(chat_id), "TRUE" if enabled else "FALSE", now_str]])

def is_subscribed(chat_id: int) -> bool:
    sh = open_sheet()
    ws = ensure_subs_ws(sh)
    values = ws.get_all_values()
    if not values or len(values) < 2:
        return False
    for row in values[1:]:
        if row and str(row[0]).strip() == str(chat_id):
            return str(row[1]).strip().lower() in ("1","true","yes","y","on","enable","enabled")
    return False

# ---------- Helpers ----------
def normalize_date(val):
    if isinstance(val, (datetime, date)): return datetime(val.year, val.month, val.day)
    if not val: return None
    try:
        d = dtparse(str(val), dayfirst=True)
        return datetime(d.year, d.month, d.day)
    except Exception:
        return None

def hhmm(val):
    s = str(val or "").strip().replace(".",":").replace(" ", "")
    if not s: return ""
    if s.isdigit() and len(s) in (3,4):
        s = s.zfill(4); s = s[:2]+":"+s[2:]
    if len(s) >= 5 and s[2] == ":": return s[:5]
    return s

def derive_weekday(dtobj):
    return UA_DAYNAMES.get(dtobj.weekday(), "") if dtobj else ""

def get_type(rec):
    return (rec.get("type") or rec.get("Тип") or rec.get("notes") or rec.get("Примітки") or "").strip()

def get_subject(rec):
    for key in ["subject","Subject","Предмет","назва","Назва","discipline","Дисципліна"]:
        v = rec.get(key)
        if v: return str(v).strip()
    return ""

def get_teacher(rec):
    for key in ["teacher","Teacher","Викладач","Преподаватель"]:
        v = rec.get(key)
        if v: return str(v).strip()
    return ""

def get_lesson(rec, fallback=None):
    v = (rec.get("lesson") or rec.get("Lesson") or rec.get("№") or rec.get("Номер") or rec.get("Пара №") or fallback or "")
    return str(v).strip()

def get_time_span(rec):
    ts = hhmm(rec.get("time_start") or rec.get("Початок") or rec.get("Пара") or "")
    te = hhmm(rec.get("time_end")   or rec.get("Кінець")   or "")
    if ts and te: return f"{ts}–{te}"
    if ts: return ts
    if te: return te
    return ""

def parse_time_start(rec):
    ts = hhmm(rec.get("time_start") or rec.get("Початок") or rec.get("Пара") or "")
    if not ts:
        return None
    try:
        hh, mm = ts.split(":")
        return int(hh), int(mm)
    except Exception:
        return None

def unique(values):
    seen=set(); out=[]
    for v in values:
        v=(v or "").strip()
        if v and v not in seen: seen.add(v); out.append(v)
    return out

def norm(s): return str(s or "").strip().casefold()

BANNED_SUBJECTS = {norm("вихідний"), norm("науковий день")}

def infer_subjects(rows):
    subs = unique([get_subject(r) for r in rows])
    subs = [s for s in subs if norm(s) not in BANNED_SUBJECTS]
    try:
        subs.sort(key=locale.strxfrm)
    except Exception:
        subs.sort(key=lambda s: s.lower())
    return subs

def filter_rows(rows, *, target_date=None, weekday_en=None, subject=None, from_dt=None):
    out=[]
    norm_subject = norm(subject) if subject else None
    for r in rows:
        r_subject=get_subject(r)
        r_date=normalize_date(r.get("date") or r.get("Дата"))
        r_weekday=(r.get("weekday") or r.get("День") or "").strip()
        r_weekday_en=UA_TO_EN.get(r_weekday, r_weekday)
        ok=True
        if norm_subject and r_subject and norm(r_subject) != norm_subject: ok=False
        if target_date and r_date and r_date!=target_date: ok=False
        if weekday_en and r_weekday_en and r_weekday_en!=weekday_en: ok=False
        if from_dt and r_date and r_date<from_dt: ok=False
        if ok: out.append(r)
    def sort_key(rec):
        d=normalize_date(rec.get("date") or rec.get("Дата"))
        ts=hhmm(rec.get("time_start") or rec.get("Початок") or rec.get("Пара"))
        lesson=get_lesson(rec, "")
        return (d or datetime.min, lesson or (ts or "00:00"))
    out.sort(key=sort_key)
    return out

def fmt_line_core(rec, idx_for_fallback=None):
    # fallback тепер просто "4", без "№"
    lesson=get_lesson(rec, fallback=(str(idx_for_fallback) if idx_for_fallback else ""))
    span=get_time_span(rec)
    left = lesson
    if span: left = f"{lesson} ({span})" if lesson else f"({span})"
    subj=get_subject(rec)
    typ=get_type(rec)
    teacher=get_teacher(rec)
    subj_typ = (f"{subj} ({typ})" if subj and typ else (subj or (f"({typ})" if typ else "")))
    right = ", ".join([p for p in [subj_typ, teacher] if p])
    if left and right: return f"{left} — {right}"
    return left or right or ""

def group_by_date(rows):
    items=[]
    for r in rows:
        d=normalize_date(r.get("date") or r.get("Дата"))
        if d: items.append((d,r))
    items.sort(key=lambda x: (x[0], hhmm(x[1].get("time_start") or x[1].get("Початок") or "")))
    for d, group in itertools.groupby(items, key=lambda x: x[0]):
        yield d, [g[1] for g in group]

def fmt_today(rows_day, target_dt):
    header=f"{derive_weekday(target_dt)}, {target_dt.strftime('%d.%m.%Y')}"
    if not rows_day: return header+"\nНічого не знайдено."
    lines=[header]+[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(rows_day, start=1)]
    return "\n".join(lines)

def fmt_week(rows, monday_dt):
    days=[monday_dt + timedelta(days=i) for i in range(6)]
    blocks=[]
    rows_by_date={d:[] for d in days}
    for r in rows:
        d=normalize_date(r.get("date") or r.get("Дата"))
        if d in rows_by_date: rows_by_date[d].append(r)
    for d in days:
        header=f"{derive_weekday(d)}, {d.strftime('%d.%m.%Y')}"
        day_rows=sorted(rows_by_date[d], key=lambda rec: (get_lesson(rec, ""), hhmm(rec.get("time_start") or "")))
        if not day_rows: blocks.append(header+"\n—")
        else: blocks.append("\n".join([header]+[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(day_rows, start=1)]))
    return "\n\n".join(blocks)

def fmt_grouped_next(rows):
    if not rows: return "Нічого не знайдено."
    pieces=[]
    for d, group in group_by_date(rows):
        header=f"{derive_weekday(d)}, {d.strftime('%d.%m.%Y')}"
        lines=[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(group, start=1)]
        pieces.append("\n".join([header]+lines))
    return "\n\n".join(pieces)

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Розклад на сьогодні", callback_data="m:today"),
         InlineKeyboardButton("Розклад на завтра", callback_data="m:tomorrow")],
        [InlineKeyboardButton("Розклад на тиждень", callback_data="m:week")],
        [InlineKeyboardButton("Розклад по предмету", callback_data="m:subject")],
        [InlineKeyboardButton("Найближчі пари", callback_data="m:next")],
        [InlineKeyboardButton("Підключити сповіщення", callback_data="m:notify_on"),
         InlineKeyboardButton("Відключити сповіщення", callback_data="m:notify_off")],
    ])

# ---- Messaging helpers ----
MAX_CHUNK = 3500
def split_text(text, max_len=MAX_CHUNK):
    parts=[]
    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)
        if cut == -1: cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts

async def send_or_edit(update: Update, text: str, *, reply_markup=None):
    chunks = split_text(text)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(chunks[0], reply_markup=reply_markup if len(chunks)==1 else None)
        except TelegramError as e:
            logging.warning("Edit failed, fallback to send: %s", e)
            await update.effective_chat.send_message(chunks[0])
        for chunk in chunks[1:-1]:
            await update.effective_chat.send_message(chunk)
        if len(chunks) > 1:
            await update.effective_chat.send_message(chunks[-1], reply_markup=reply_markup)
    else:
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk)
        await update.message.reply_text(chunks[-1], reply_markup=reply_markup)

# ---------- Date parsing ----------
DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-\/](\d{1,2})[.\-\/](\d{2,4})\s*$")
def parse_user_date(text: str):
    t = (text or "").strip()
    m = DATE_RE.match(t)
    if m:
        dd, mm, yy = m.groups()
        dd = int(dd); mm = int(mm); yy = int(yy)
        if yy < 100: yy += 2000
        return datetime(yy, mm, dd)
    try:
        d = dtparse(t, dayfirst=True)
        return datetime(d.year, d.month, d.day)
    except Exception:
        return None

# ---------- Core Handlers ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Обери дію:", reply_markup=main_menu())

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except BadRequest:
        pass
    data = q.data
    try:
        if data == "m:today":
            await handle_today(update, ctx, 0)
        elif data == "m:tomorrow":
            await handle_today(update, ctx, 1)
        elif data == "m:week":
            await handle_week(update, ctx)
        elif data == "m:subject":
            await handle_subject_menu(update, ctx, page=0)
        elif data == "m:next":
            await handle_next(update, ctx)
        elif data == "m:notify_on":
            await handle_notify_on(update, ctx)
        elif data == "m:notify_off":
            await handle_notify_off(update, ctx)
        elif data.startswith("subj:"):
            _, page_str, token = data.split(":", 2)
            if token == "__page__":  # пагінація
                page = int(page_str)
                return await handle_subject_menu(update, ctx, page=page)
            try:
                idx = int(token)
            except ValueError:
                return await update.effective_chat.send_message("Помилка вибору предмета. Спробуй ще раз.")
            key = f"subjects_page_{page_str}"
            page_list = ctx.user_data.get(key) or []
            if 0 <= idx < len(page_list):
                name = page_list[idx]
                await show_subject(update, ctx, name)
            else:
                await update.effective_chat.send_message("Предмет не знайдено. Спробуй ще раз.")
    except Exception as e:
        logging.exception("Callback error: %s", e)
        try:
            await q.edit_message_text("Сталася помилка при обробці запиту. Спробуй ще раз із меню /start.")
        except Exception:
            pass

async def handle_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delta_days: int):
    rows = get_records()
    now = datetime.now(KYIV_TZ) + timedelta(days=delta_days)
    target = datetime(now.year, now.month, now.day)
    today_rows = filter_rows(rows, target_date=target)
    txt = fmt_today(today_rows, target)
    await send_or_edit(update, txt, reply_markup=main_menu())

async def handle_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    monday = now - timedelta(days=now.weekday())
    start = datetime(monday.year, monday.month, monday.day)
    end = start + timedelta(days=6)
    rows_week = [r for r in rows if (d:=normalize_date(r.get("date") or r.get("Дата"))) and start <= d < end]
    txt = fmt_week(rows_week, start)
    await send_or_edit(update, txt, reply_markup=main_menu())

async def handle_subject_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, page:int=0):
    rows = get_records()
    subjects = infer_subjects(rows)
    if not subjects:
        return await send_or_edit(update, "У таблиці немає предметів.", reply_markup=main_menu())
    per_page = 8
    pages = max(1, math.ceil(len(subjects)/per_page))
    page = max(0, min(page, pages-1))
    start = page*per_page; end = start+per_page
    page_subjects = subjects[start:end]
    ctx.user_data[f"subjects_page_{page}"] = page_subjects
    kb = [[InlineKeyboardButton(s, callback_data=f"subj:{page}:{i}")] for i, s in enumerate(page_subjects)]
    nav = []
    if page>0: nav.append(InlineKeyboardButton("« Назад", callback_data=f"subj:{page-1}:__page__"))
    if page<pages-1: nav.append(InlineKeyboardButton("Далі »", callback_data=f"subj:{page+1}:__page__"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("Меню", callback_data="m:today")])
    text = f"Оберіть предмет (стор. {page+1}/{pages})"
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except TelegramError:
            await update.effective_chat.send_message(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def show_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE, subject_name: str):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    res = filter_rows(rows, subject=subject_name, from_dt=today)
    body = fmt_grouped_next(res) if res else "Нічого не знайдено."
    txt = f"Розклад по предмету: {subject_name}\n\n{body}"
    await send_or_edit(update, txt, reply_markup=main_menu())

async def handle_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE, limit:int=10):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    upcoming = filter_rows(rows, from_dt=today)
    if limit and len(upcoming)>limit: upcoming = upcoming[:limit]
    txt = fmt_grouped_next(upcoming)
    await send_or_edit(update, txt, reply_markup=main_menu())

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — меню\n"
        "/today — розклад на сьогодні\n"
        "/tomorrow — розклад на завтра\n"
        "/week — розклад на тиждень\n"
        "/date DD.MM.YYYY — розклад на дату\n"
        "/subject Назва — розклад по предмету (без аргументу відкриє список)\n"
        "/next — найближчі пари\n"
        "/notify_on — підключити сповіщення\n"
        "/notify_off — відключити сповіщення\n"
        "/debug — діагностика таблиці"
    )

# --- Subscription handlers ---
async def handle_notify_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_subscribed(chat_id):
        return await send_or_edit(update, "Сповіщення вже підключені ✅", reply_markup=main_menu())
    try:
        upsert_subscription(chat_id, True)
    except Exception as e:
        logging.exception("Subscribe failed: %s", e)
        return await send_or_edit(
            update,
            "Не зміг записати підписку в Google Sheets. Перевір доступ Editor для сервісної пошти і змінну GOOGLE_CREDENTIALS_JSON.",
            reply_markup=main_menu(),
        )
    await send_or_edit(update, "Сповіщення підключені ✅\nПротягом 3 хв ви отримаєте тестове повідомлення.", reply_markup=main_menu())
    ctx.job_queue.run_once(send_test_notification, when=180, data={"chat_id": chat_id})

async def handle_notify_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_subscribed(chat_id):
        return await send_or_edit(update, "Сповіщення вже відключені ❎", reply_markup=main_menu())
    try:
        upsert_subscription(chat_id, False)
    except Exception as e:
        logging.exception("Unsubscribe failed: %s", e)
        return await send_or_edit(
            update,
            "Не зміг оновити підписку в Google Sheets. Перевір доступ Editor для сервісної пошти.",
            reply_markup=main_menu(),
        )
    await send_or_edit(update, "Сповіщення відключені ❎", reply_markup=main_menu())

async def send_test_notification(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = ctx.job.data.get("chat_id")
    try:
        await ctx.bot.send_message(chat_id, "🔔 Тестове сповіщення: все працює ✅")
    except Exception as e:
        logging.warning("Failed to send test notification: %s", e)

# --- Notification loop ---
_notified_keys = set()
_notified_day = None

async def notify_loop(ctx: ContextTypes.DEFAULT_TYPE):
    global _notified_day, _notified_keys
    try:
        now = datetime.now(KYIV_TZ)
        today = datetime(now.year, now.month, now.day)
        if _notified_day != today.date():
            _notified_day = today.date()
            _notified_keys = set()
        subs = get_subscribers_set()
        if not subs:
            return
        rows = get_records()
        # Сьогоднішні пари з коректним часом початку
        todays = [r for r in rows if (d:=normalize_date(r.get("date") or r.get("Дата"))) == today and parse_time_start(r)]
        for r in todays:
            hhmm_pair = parse_time_start(r)
            if not hhmm_pair:
                continue
            hh, mm = hhmm_pair
            start_dt = KYIV_TZ.localize(datetime(today.year, today.month, today.day, hh, mm))
            delta = (start_dt - now).total_seconds()
            # 0..600 сек до старту (10 хв)
            if 0 <= delta <= 600:
                key = (today.date().isoformat(), get_lesson(r,""), get_subject(r), hh, mm)
                if key in _notified_keys:
                    continue
                _notified_keys.add(key)
                msg = build_reminder_message(r, start_dt)
                for chat_id in subs:
                    try:
                        await ctx.bot.send_message(chat_id, msg)
                    except Exception as e:
                        logging.warning("Notify fail to %s: %s", chat_id, e)
    except Exception as e:
        logging.exception("notify_loop error: %s", e)

def build_reminder_message(rec, start_dt):
    span = get_time_span(rec)
    lesson = get_lesson(rec, "")
    subj = get_subject(rec)
    typ = get_type(rec)
    teacher = get_teacher(rec)
    left = lesson
    if span: left = f"{lesson} ({span})" if lesson else f"({span})"
    right_core = ", ".join([p for p in [f"{subj} ({typ})" if subj and typ else subj, teacher] if p])
    tstr = start_dt.strftime("%H:%M")
    return f"🔔 Нагадування: о {tstr} починається\n{left} — {right_core}"

# --- Command wrappers for external entry ---
async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_today(update, ctx, 0)
async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_today(update, ctx, 1)
async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_week(update, ctx)
async def cmd_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg: return await update.message.reply_text("Формат: /date DD.MM.YYYY (або YYYY-MM-DD)")
    target = parse_user_date(arg)
    if not target:
        return await update.message.reply_text("Не розпізнав дату. Приклад: /date 25.09.2025")
    rows = get_records()
    rows_day = filter_rows(rows, target_date=target)
    await send_or_edit(update, fmt_today(rows_day, target), reply_markup=main_menu())
async def cmd_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.partition(" ")[2].strip()
    if not name: return await handle_subject_menu(update, ctx, 0)
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    res = filter_rows(rows, subject=name, from_dt=today)
    body = fmt_grouped_next(res) if res else "Нічого не знайдено."
    await send_or_edit(update, f"Розклад по предмету: {name}\n\n{body}", reply_markup=main_menu())
async def cmd_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_next(update, ctx)
async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        rows = get_records()
        cols = list(rows[0].keys()) if rows else []
        subs = list(get_subscribers_set())
        now = datetime.now(KYIV_TZ)
        today = datetime(now.year, now.month, now.day)
        today_rows = filter_rows(rows, target_date=today)
        text = (
            f"Колонок: {len(cols)}\nНазви колонок: {cols}\n"
            f"Рядків у таблиці: {len(rows)}\nСьогодні: {len(today_rows)} рядків\n"
            f"Підписників: {len(subs)}"
        )
    except Exception as e:
        text = f"DEBUG ERROR: {e}\n{traceback.format_exc()}"
    await update.message.reply_text(text)

# --- Error handler ---
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    logging.error("Global error: %s\n%s", err, traceback.format_exc())
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, f"Сталася тимчасова помилка ({err.__class__.__name__}). Спробуй ще раз.")
    except Exception:
        pass

# --- Local entry (optional) ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("subject", cmd_subject))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("notify_on", handle_notify_on))
    app.add_handler(CommandHandler("notify_off", handle_notify_off))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)
    # Нагадування раз на хвилину
    app.job_queue.run_repeating(notify_loop, interval=60, first=10)
    # Старт: webhook якщо WEBHOOK_URL задано, інакше polling
    webhook_url = os.getenv("WEBHOOK_URL")
    listen_addr = os.getenv("LISTEN_ADDR", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram")
    if webhook_url:
        url_path_clean = webhook_path[1:] if webhook_path.startswith("/") else webhook_path
        app.run_webhook(
            listen=listen_addr,
            port=port,
            url_path=url_path_clean,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=None,
        )
    else:
        app.run_polling(allowed_updates=None, drop_pending_updates=True)

if __name__ == "__main__":
    main()
