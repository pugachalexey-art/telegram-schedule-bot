# bot_schedule_custom_v6d.py (clean minimal)
# Функції: сьогодні, завтра, тиждень, наступний тиждень, предмети, найближчі.
# Видалено: /date, нумерація пар у виводі.

import os, json, gspread, logging, pytz, math, itertools, locale
from datetime import datetime, timedelta, date
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

try:
    locale.setlocale(locale.LC_COLLATE, "uk_UA.UTF-8")
except Exception:
    pass

# ---------- Google Sheets ----------
def make_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

def get_records():
    gc = make_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(SHEET_NAME)
    return ws.get_all_records()

# ---------- Helpers ----------
def normalize_date(val):
    if isinstance(val, (datetime, date)): 
        return datetime(val.year, val.month, val.day)
    try:
        from dateutil.parser import parse as dtparse
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
    return ""

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

def get_type(rec):
    return (rec.get("type") or rec.get("Тип") or rec.get("notes") or rec.get("Примітки") or "").strip()

def get_time_span(rec):
    ts = hhmm(rec.get("time_start") or rec.get("Початок") or rec.get("Пара") or "")
    te = hhmm(rec.get("time_end")   or rec.get("Кінець")   or "")
    if ts and te: return f"{ts}–{te}"
    return ts or te or ""

def norm(s): return str(s or "").strip().casefold()

BANNED_SUBJECTS = {norm("вихідний"), norm("науковий день")}

def infer_subjects(rows):
    seen=set(); out=[]
    for r in rows:
        s = get_subject(r)
        if s and norm(s) not in BANNED_SUBJECTS and s not in seen:
            seen.add(s); out.append(s)
    try:
        out.sort(key=locale.strxfrm)
    except Exception:
        out.sort(key=lambda x: x.lower())
    return out

def filter_rows(rows, *, date_from=None, date_to=None, exact_date=None, subject=None):
    res=[]
    subj = norm(subject) if subject else None
    for r in rows:
        d = normalize_date(r.get("date") or r.get("Дата"))
        if exact_date and d != exact_date: 
            continue
        if date_from and d and d < date_from:
            continue
        if date_to and d and d > date_to:
            continue
        if subj and get_subject(r) and norm(get_subject(r)) != subj:
            continue
        res.append(r)
    def sort_key(rec):
        d = normalize_date(rec.get("date") or rec.get("Дата")) or datetime.min
        ts = hhmm(rec.get("time_start") or rec.get("Початок") or rec.get("Пара") or "")
        return (d, ts or "00:00")
    return sorted(res, key=sort_key)

def fmt_line(rec):
    span = get_time_span(rec)
    subj = get_subject(rec)
    typ  = get_type(rec)
    teacher = get_teacher(rec)
    right = ", ".join([p for p in [subj if subj else "", f"({typ})" if typ else "", teacher] if p])
    return f"{span} — {right}" if span and right else (right or span or "")

def fmt_day_block(dt_day, rows):
    header = f"{UA_DAYNAMES.get(dt_day.weekday(),'')}, {dt_day.strftime('%d.%m.%Y')}"
    if not rows: return header + "\n—"
    return "\n".join([header] + [fmt_line(r) for r in rows])

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Розклад на сьогодні", callback_data="m:today"),
         InlineKeyboardButton("Розклад на завтра", callback_data="m:tomorrow")],
        [InlineKeyboardButton("Розклад на тиждень", callback_data="m:week"),
         InlineKeyboardButton("Наступний тиждень", callback_data="m:week_next")],
        [InlineKeyboardButton("Розклад по предмету", callback_data="m:subject")],
        [InlineKeyboardButton("Найближчі пари", callback_data="m:next")],
    ])

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
            await update.effective_chat.send_message(chunks[0])
        for chunk in chunks[1:-1]:
            await update.effective_chat.send_message(chunk)
        if len(chunks) > 1:
            await update.effective_chat.send_message(chunks[-1], reply_markup=reply_markup)
    else:
        for chunk in chunks[:-1]:
            await update.message.reply_text(chunk)
        await update.message.reply_text(chunks[-1], reply_markup=reply_markup)

# ---------- Handlers ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Обери дію:", reply_markup=main_menu())

async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except BadRequest: pass
    data = q.data
    if data == "m:today":
        await handle_today(update, ctx, 0)
    elif data == "m:tomorrow":
        await handle_today(update, ctx, 1)
    elif data == "m:week":
        await handle_week(update, ctx)
    elif data == "m:week_next":
        await handle_week_next(update, ctx)
    elif data == "m:subject":
        await handle_subject_menu(update, ctx, page=0)
    elif data == "m:next":
        await handle_next(update, ctx)

async def handle_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE, delta_days: int):
    rows = get_records()
    now = datetime.now(KYIV_TZ) + timedelta(days=delta_days)
    target = datetime(now.year, now.month, now.day)
    rows_day = filter_rows(rows, exact_date=target)
    text = fmt_day_block(target, rows_day)
    await send_or_edit(update, text, reply_markup=main_menu())

async def handle_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    monday = now - timedelta(days=now.weekday())
    start = datetime(monday.year, monday.month, monday.day)
    out = []
    for i in range(6):  # пн-сб
        d = start + timedelta(days=i)
        day_rows = filter_rows(rows, exact_date=d)
        out.append(fmt_day_block(d, day_rows))
    await send_or_edit(update, "\n\n".join(out), reply_markup=main_menu())

async def handle_week_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    this_monday = now - timedelta(days=now.weekday())
    next_monday = this_monday + timedelta(days=7)
    start = datetime(next_monday.year, next_monday.month, next_monday.day)
    out = []
    for i in range(6):  # пн-сб
        d = start + timedelta(days=i)
        day_rows = filter_rows(rows, exact_date=d)
        out.append(fmt_day_block(d, day_rows))
    await send_or_edit(update, "Наступний тиждень\n\n" + "\n\n".join(out), reply_markup=main_menu())

async def handle_subject_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, page:int=0):
    rows = get_records()
    subjects = infer_subjects(rows)
    if not subjects:
        return await send_or_edit(update, "У таблиці немає предметів.", reply_markup=main_menu())
    per_page = 8
    pages = max(1, (len(subjects)+per_page-1)//per_page)
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
    res = filter_rows(rows, date_from=today, subject=subject_name)
    # згрупуємо по датах
    by_date = {}
    for r in res:
        d = normalize_date(r.get("date") or r.get("Дата"))
        by_date.setdefault(d, []).append(r)
    parts = []
    for d in sorted(by_date):
        parts.append(fmt_day_block(d, by_date[d]))
    text = f"Розклад по предмету: {subject_name}\n\n" + ("\n\n".join(parts) if parts else "—")
    await send_or_edit(update, text, reply_markup=main_menu())

async def handle_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE, limit:int=10):
    rows = get_records()
    now = datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    res = filter_rows(rows, date_from=today)
    if limit: res = res[:limit]
    # вивід без нумерації
    lines = [fmt_line(r) for r in res] or ["—"]
    await send_or_edit(update, "\n".join(lines), reply_markup=main_menu())

# ---- Commands ----
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — меню\n"
        "/today — розклад на сьогодні\n"
        "/tomorrow — розклад на завтра\n"
        "/week — розклад на тиждень\n"
        "/weeknext — розклад на наступний тиждень\n"
        "/subject Назва — розклад по предмету\n"
        "/next — найближчі пари"
    )

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_today(update, ctx, 0)
async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_today(update, ctx, 1)
async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_week(update, ctx)
async def cmd_weeknext(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_week_next(update, ctx)
async def cmd_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.partition(" ")[2].strip()
    if not name: return await handle_subject_menu(update, ctx, 0)
    await show_subject(update, ctx, name)
async def cmd_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_next(update, ctx)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, "Сталася помилка. Спробуй ще раз.")
    except Exception:
        pass

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("weeknext", cmd_weeknext))
    app.add_handler(CommandHandler("subject", cmd_subject))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
