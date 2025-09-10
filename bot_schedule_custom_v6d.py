# bot_schedule_custom_v6d.py
# v6d + notifications:
# - Subject view shows only dates >= today (Europe/Kyiv). (from base v6d)
# - Main menu: add "Увімкнути сповіщення" / "Вимкнути сповіщення".
# - Persist subscriptions in JSON; send test notification within 3 minutes when enabling.
# - Remind 10 хв до початку заняття (per Google Sheet date/time), using PTB JobQueue.
# - Daily refresh of jobs at 00:10 Europe/Kyiv.

import os, json, gspread, logging, pytz, math, itertools, re, traceback, locale
from datetime import datetime, timedelta, date, time as dtime
from dateutil.parser import parse as dtparse
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
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

# --- simple persistence for notification subscriptions & jobs ---
SUBS_FILE = os.getenv("SUBS_FILE", "/mnt/data/subscriptions.json")
def load_subs():
    try:
        with open(SUBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict): return {}
            return data
    except Exception:
        return {}

def save_subs(data: dict):
    try:
        Path(SUBS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(SUBS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save subs: %s", e)

def set_subscribed(user_id: int, chat_id: int, flag: bool):
    subs = load_subs()
    key = str(user_id)
    if flag:
        subs[key] = {"chat_id": chat_id, "enabled": True, "ts": int(datetime.now().timestamp())}
    else:
        subs[key] = {"chat_id": chat_id, "enabled": False, "ts": int(datetime.now().timestamp())}
    save_subs(subs)

def is_subscribed(user_id: int) -> bool:
    subs = load_subs()
    state = subs.get(str(user_id))
    return bool(state and state.get("enabled"))

def get_chat_for_user(user_id: int) -> int | None:
    subs = load_subs()
    state = subs.get(str(user_id))
    return int(state["chat_id"]) if state and "chat_id" in state else None

UA_DAYNAMES = {0:"Понеділок",1:"Вівторок",2:"Середа",3:"Четвер",4:"Пʼятниця",5:"Субота",6:"Неділя"}
UA_TO_EN = {"Понеділок":"Monday","Вівторок":"Tuesday","Середа":"Wednesday","Четвер":"Thursday","Пʼятниця":"Friday","Субота":"Saturday","Неділя":"Sunday"}

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
    recs = ws.get_all_records()
    logging.info("Loaded %d rows. Columns: %s", len(recs), list(recs[0].keys()) if recs else [])
    return recs

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

def parse_time_to_dt(date_dt: datetime, tstr: str) -> datetime | None:
    t = hhmm(tstr)
    if not t or len(t) < 4: return None
    try:
        hh, mm = t.split(":")
        naive = datetime(date_dt.year, date_dt.month, date_dt.day, int(hh), int(mm))
        return KYIV_TZ.localize(naive)
    except Exception:
        return None

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
    lesson=get_lesson(rec, fallback=(f"№{idx_for_fallback}" if idx_for_fallback else ""))
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
    if not rows_day: return header+"\\nНічого не знайдено."
    lines=[header]+[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(rows_day, start=1)]
    return "\\n".join(lines)

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
        if not day_rows: blocks.append(header+"\\n—")
        else: blocks.append("\\n".join([header]+[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(day_rows, start=1)]))
    return "\\n\\n".join(blocks)

def fmt_grouped_next(rows):
    if not rows: return "Нічого не знайдено."
    pieces=[]
    for d, group in group_by_date(rows):
        header=f"{derive_weekday(d)}, {d.strftime('%d.%m.%Y')}"
        lines=[fmt_line_core(r, idx_for_fallback=i) for i,r in enumerate(group, start=1)]
        pieces.append("\\n".join([header]+lines))
    return "\\n\\n".join(pieces)

def main_menu(notif_state: bool | None = None):
    notif_row = [
        InlineKeyboardButton("Увімкнути сповіщення", callback_data="m:notif_on"),
        InlineKeyboardButton("Вимкнути сповіщення", callback_data="m:notif_off"),
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Розклад на сьогодні", callback_data="m:today"),
         InlineKeyboardButton("Розклад на завтра", callback_data="m:tomorrow")],
        [InlineKeyboardButton("Розклад на тиждень", callback_data="m:week")],
        [InlineKeyboardButton("Розклад по предмету", callback_data="m:subject")],
        [InlineKeyboardButton("Найближчі пари", callback_data="m:next")],
        notif_row,
    ])

# ---- Messaging helpers ----
MAX_CHUNK = 3500
def split_text(text, max_len=MAX_CHUNK):
    parts=[]
    while len(text) > max_len:
        cut = text.rfind("\\n", 0, max_len)
        if cut == -1: cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\\n")
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
DATE_RE = re.compile(r"^\\s*(\\d{1,2})[.\\-/](\\d{1,2})[.\\-/](\\d{2,4})\\s*$")
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

# ---------- Notification scheduling ----------
def _format_reminder_line(rec):
    # Build a concise line for the reminder
    line = fmt_line_core(rec)  # already "№X (HH:MM–HH:MM) — Subject (Type), Teacher"
    # Strip left side number for cleaner notif
    return f"Нагадування: {line}"

def _iter_future_lessons(rows, *, from_dt_kiev: datetime | None = None):
    now = from_dt_kiev or datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    for r in rows:
        d = normalize_date(r.get("date") or r.get("Дата"))
        if not d: continue
        # time_start is needed
        ts = r.get("time_start") or r.get("Початок") or r.get("Пара")
        start_dt = parse_time_to_dt(d, ts) if ts else None
        if not start_dt: continue
        if start_dt <= now: continue
        yield r, start_dt

async def _send_reminder(ctx: ContextTypes.DEFAULT_TYPE):
    job = ctx.job
    chat_id = job.data.get("chat_id")
    text = job.data.get("text") or "Нагадування."
    try:
        await ctx.bot.send_message(chat_id, text)
    except Exception as e:
        logger.warning("Failed to send reminder to %s: %s", chat_id, e)

def _cancel_user_jobs(app, user_id: int):
    jobs = app.job_queue.get_jobs_by_name(f"user:{user_id}")
    for j in jobs:
        j.schedule_removal()

def schedule_user_reminders(app, user_id: int, chat_id: int):
    # Cancel previous
    _cancel_user_jobs(app, user_id)
    # Load schedule and enqueue jobs 10 minutes before
    try:
        rows = get_records()
    except Exception as e:
        logger.error("Cannot load records for scheduling: %s", e)
        return 0
    now = datetime.now(KYIV_TZ)
    count = 0
    for rec, start_dt in _iter_future_lessons(rows, from_dt_kiev=now):
        remind_at = start_dt - timedelta(minutes=10)
        if remind_at <= now:  # if already within 10 minutes, skip
            continue
        text = _format_reminder_line(rec)
        app.job_queue.run_once(
            _send_reminder,
            when=remind_at,
            data={"chat_id": chat_id, "text": text},
            name=f"user:{user_id}",
            chat_id=chat_id,
            user_id=user_id,
            timezone=KYIV_TZ,
        )
        count += 1
    logger.info("Scheduled %d reminder jobs for user %s", count, user_id)
    return count

def schedule_test_notification(app, chat_id: int, user_id: int):
    # Send test within 3 minutes; we pick 2 minutes
    app.job_queue.run_once(
        _send_reminder,
        when=timedelta(minutes=2),
        data={"chat_id": chat_id, "text": "Тест: сповіщення увімкнено. Це приклад нагадування."},
        name=f"user:{user_id}",
        chat_id=chat_id,
        user_id=user_id,
        timezone=KYIV_TZ,
    )

async def daily_refresh_all(ctx: ContextTypes.DEFAULT_TYPE):
    # Re-schedule for all enabled users once a day
    app = ctx.application
    subs = load_subs()
    n_users = 0
    for key, st in subs.items():
        if st.get("enabled"):
            uid = int(key)
            chat_id = int(st.get("chat_id"))
            schedule_user_reminders(app, uid, chat_id)
            n_users += 1
    logger.info("Daily refresh scheduled reminders for %d users", n_users)

# ---------- Handlers ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    enabled = is_subscribed(uid)
    state_txt = "увімкнені ✅" if enabled else "вимкнені ⛔️"
    await update.message.reply_text(f"Обери дію. Зараз сповіщення {state_txt}.", reply_markup=main_menu())

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
        elif data == "m:notif_on":
            await handle_notif_on(update, ctx)
        elif data == "m:notif_off":
            await handle_notif_off(update, ctx)
        elif data.startswith("subj:"):
            _, page_str, token = data.split(":", 2)
            if token == "__page__":
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

async def handle_notif_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if is_subscribed(uid):
        return await send_or_edit(update, "Сповіщення вже увімкнені. Якщо потрібно, можеш їх вимкнути.", reply_markup=main_menu())
    set_subscribed(uid, chat_id, True)
    # schedule for user
    jobs = schedule_user_reminders(ctx.application, uid, chat_id)
    # schedule test
    schedule_test_notification(ctx.application, chat_id, uid)
    note = "Сповіщення підключені, протягом 3 хв ви отримаєте тестове повідомлення."
    await send_or_edit(update, f"{note}\\nЗаплановано нагадувань: {jobs}", reply_markup=main_menu())

async def handle_notif_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    if not is_subscribed(uid):
        return await send_or_edit(update, "Сповіщення вже вимкнені.", reply_markup=main_menu())
    set_subscribed(uid, chat_id, False)
    _cancel_user_jobs(ctx.application, uid)
    await send_or_edit(update, "Сповіщення вимкнені. Більше нагадувань не надходитиме.", reply_markup=main_menu())

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
    # filter from today (Kyiv)
    now = datetime.now(KYIV_TZ)
    today = datetime(now.year, now.month, now.day)
    res = filter_rows(rows, subject=subject_name, from_dt=today)
    body = fmt_grouped_next(res) if res else "Нічого не знайдено."
    txt = f"Розклад по предмету: {subject_name}\\n\\n{body}"
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
        "/start — меню\\n"
        "/today — розклад на сьогодні\\n"
        "/tomorrow — розклад на завтра\\n"
        "/week — розклад на тиждень\\n"
        "/date DD.MM.YYYY — розклад на дату\\n"
        "/subject Назва — розклад по предмету (без аргументу відкриє список)\\n"
        "/next — найближчі пари\\n"
        "/notify_on — увімкнути сповіщення\\n"
        "/notify_off — вимкнути сповіщення\\n"
        "/debug — діагностика таблиці"
    )

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
    await send_or_edit(update, f"Розклад по предмету: {name}\\n\\n{body}", reply_markup=main_menu())
async def cmd_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE): await handle_next(update, ctx)

async def cmd_notify_on(update: Update, ctx: ContextTypes.DEFAULT_TYPE): 
    # convenience command
    await handle_notif_on(update, ctx)

async def cmd_notify_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE): 
    await handle_notif_off(update, ctx)

async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        rows = get_records()
        cols = list(rows[0].keys()) if rows else []
        subs = infer_subjects(rows)[:15]
        now = datetime.now(KYIV_TZ)
        today = datetime(now.year, now.month, now.day)
        today_rows = filter_rows(rows, target_date=today)
        text = (
            f"Колонок: {len(cols)}\\n"
            f"Назви колонок: {cols}\\n"
            f"Рядків у таблиці: {len(rows)}\\n"
            f"Перші предмети (відсортовані): {subs}\\n"
            f"Сьогодні знайдено рядків: {len(today_rows)}"
        )
    except Exception as e:
        text = f"DEBUG ERROR: {e}\\n{traceback.format_exc()}"
    await update.message.reply_text(text)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    logging.error("Global error: %s\\n%s", err, traceback.format_exc())
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(update.effective_chat.id, f"Сталася тимчасова помилка ({err.__class__.__name__}). Спробуй ще раз.")
    except Exception:
        pass

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("subject", cmd_subject))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("notify_on", cmd_notify_on))
    app.add_handler(CommandHandler("notify_off", cmd_notify_off))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("debug", cmd_debug))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_error_handler(on_error)

    # Daily refresh at 00:10 Kyiv time
    app.job_queue.run_daily(
        daily_refresh_all,
        time=dtime(hour=0, minute=10, second=0, tzinfo=KYIV_TZ),
        name="daily_refresh_all",
        timezone=KYIV_TZ,
    )

    # Restore jobs for existing subscribers on start
    subs = load_subs()
    for key, st in subs.items():
        if st.get("enabled"):
            uid = int(key)
            chat_id = int(st.get("chat_id"))
            schedule_user_reminders(app, uid, chat_id)

    # Runner selection is done in wrapper script (polling/webhook)
    # Here we just return app for reuse if needed
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
