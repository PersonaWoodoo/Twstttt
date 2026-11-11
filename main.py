import os, re, json, time, hmac, hashlib, sqlite3, threading, logging, html
from urllib.parse import parse_qsl
from io import BytesIO

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN", "9227421022:AAFbbu73dtZR2vSG7GwvZeoKUuhbuDXLDzs").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8959461245"))
CHANNEL = os.getenv("CHANNEL_USERNAME", "sky_shop001").lstrip("@")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/sky_shop001")
SELLER = os.getenv("SELLER_USERNAME", "Salamchik01").lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
PORT = int(os.getenv("PORT", "3000"))
DB = os.getenv("DATABASE_PATH", "sky_shop.sqlite3")
if not BOT_TOKEN:
    raise RuntimeError("В переменных хостинга укажи BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sky-shop")
web = Flask(__name__)
sessions = {}
bot_app = None

def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, blocked INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, price TEXT, order_text TEXT, photo TEXT, description TEXT);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER, product_id INTEGER, PRIMARY KEY(user_id,product_id));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        INSERT OR IGNORE INTO settings VALUES('maintenance','0');
        """)

def get_setting(k, default=""):
    with db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else default

def set_setting(k, v):
    with db() as c:
        c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k,str(v)))

def fmt(i): return f"#{int(i):03d}"

def admin_kb():
    tech = "🟢 Выключить техработы" if get_setting("maintenance") == "1" else "🟡 Включить техработы"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Добавить товар", callback_data="add")],
        [InlineKeyboardButton("🔴 Убрать товар", callback_data="remove"), InlineKeyboardButton("🔵 Статистика", callback_data="stats")],
        [InlineKeyboardButton(tech, callback_data="maintenance")]
    ])

def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔴 Отменить", callback_data="cancel")]])

def user_kb():
    first = InlineKeyboardButton("🛍 ОТКРЫТЬ МАГАЗИН", web_app=WebAppInfo(url=WEBAPP_URL)) if WEBAPP_URL.startswith("https://") else InlineKeyboardButton("🛍 ОТКРЫТЬ МАГАЗИН", url=CHANNEL_URL)
    return InlineKeyboardMarkup([[first], [InlineKeyboardButton("📢 Наш канал", url=CHANNEL_URL)]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with db() as c:
        c.execute("INSERT INTO users(id,username,first_name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,blocked=0",
                  (u.id, u.username or "", u.first_name or ""))
    if u.id == ADMIN_ID:
        await update.message.reply_text("⚙️ SKY SHOP — панель администратора", reply_markup=admin_kb())
        return
    try:
        stickers = await context.bot.get_sticker_set("UtyaDuckFull")
        if stickers.stickers:
            await update.message.reply_sticker(stickers.stickers[0].file_id)
    except Exception as e:
        log.info("Стикер не отправлен: %s", e)
    await update.message.reply_text(
        "🦆 Привет! Ты попал в <b>SKY SHOP</b>!\n\n"
        "Добро пожаловать в магазин стильных находок. Выбирай понравившиеся товары, "
        "добавляй их в избранное и связывайся с продавцом для покупки.\n\n"
        "✨ Приятных покупок!",
        parse_mode="HTML", reply_markup=user_kb()
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("⛔ Нет доступа.")
    await update.message.reply_text("⚙️ Панель администратора:", reply_markup=admin_kb())

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    uid, action = q.from_user.id, q.data
    if action == "add":
        sessions[uid] = {"step":"title","p":{}}
        await q.message.reply_text("Введи название товара:", reply_markup=cancel_kb())
    elif action == "remove":
        sessions[uid] = {"step":"remove"}
        await q.message.reply_text("Введи ID товара, например #001:", reply_markup=cancel_kb())
    elif action == "stats":
        with db() as c:
            a=c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            b=c.execute("SELECT COUNT(*) FROM users WHERE blocked=1").fetchone()[0]
            d=c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            f=c.execute("SELECT COUNT(*) FROM favorites").fetchone()[0]
        await q.message.reply_text(f"📊 Статистика\n\n👥 Пользователей: {a}\n🚫 Известных блокировок: {b}\n🛍 Товаров: {d}\n❤️ Добавлений в избранное: {f}\n\nTelegram не показывает полный список заблокировавших бота.", reply_markup=admin_kb())
    elif action == "maintenance":
        v = "0" if get_setting("maintenance") == "1" else "1"
        set_setting("maintenance",v)
        await q.message.reply_text("🛠 Технические работы включены." if v=="1" else "✅ Магазин снова работает.", reply_markup=admin_kb())
    elif action == "cancel":
        sessions.pop(uid,None)
        await q.message.reply_text("Отменено.", reply_markup=admin_kb())
    elif action == "save":
        s=sessions.get(uid)
        if not s or s["step"]!="review": return
        p=s["p"]
        with db() as c:
            cur=c.execute("INSERT INTO products(title,price,order_text,photo,description) VALUES(?,?,?,?,?)",
                          (p["title"],p["price"],p["order_text"],p.get("photo"),p["description"]))
            pid=cur.lastrowid
        sessions.pop(uid,None)
        await q.message.reply_text(f"✅ Добавлен товар {fmt(pid)} — {p['title']} ({p['price']} ₽)", reply_markup=admin_kb())
    elif action == "confirmdel":
        s=sessions.get(uid,{})
        pid=s.get("pid")
        if pid:
            with db() as c:
                c.execute("DELETE FROM favorites WHERE product_id=?",(pid,))
                c.execute("DELETE FROM products WHERE id=?",(pid,))
        sessions.pop(uid,None)
        await q.message.reply_text("🗑 Товар удалён.", reply_markup=admin_kb())

async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if u.id != ADMIN_ID: return
    s=sessions.get(u.id)
    if not s: return
    v=(update.message.text or "").strip()
    if v=="/cancel":
        sessions.pop(u.id,None); return await update.message.reply_text("Отменено.",reply_markup=admin_kb())
    step=s["step"]
    if step=="remove":
        try: pid=int(v.lstrip("#"))
        except: return await update.message.reply_text("Введи ID, например #001.")
        with db() as c: p=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        if not p: return await update.message.reply_text("Товар не найден. Попробуй ещё раз.")
        s["pid"]=pid
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔴 Да, удалить",callback_data="confirmdel"),InlineKeyboardButton("Отмена",callback_data="cancel")]])
        return await update.message.reply_text(f"Удалить {fmt(pid)} — {p['title']} за {p['price']} ₽?",reply_markup=kb)
    p=s["p"]
    if step=="title": p["title"]=v; s["step"]="price"; await update.message.reply_text("Введи цену в рублях.")
    elif step=="price":
        if not re.fullmatch(r"\d+(?:[.,]\d{1,2})?",v): return await update.message.reply_text("Цена должна быть числом, например 600.")
        p["price"]=v.replace(",","."); s["step"]="order"; await update.message.reply_text("Введи индивидуальный текст для кнопки «Заказать».")
    elif step=="order": p["order_text"]=v; s["step"]="photo"; await update.message.reply_text("Отправь фото товара.")
    elif step=="description":
        p["description"]=v; s["step"]="review"
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🟢 Готово",callback_data="save"),InlineKeyboardButton("🔴 Отменить",callback_data="cancel")]])
        await update.message.reply_text(f"Проверь товар:\n\n<b>{html.escape(p['title'])}</b>\nЦена: {p['price']} ₽\nОписание: {html.escape(v)}\nФото: {'есть' if p.get('photo') else 'нет'}",
                                        parse_mode="HTML",reply_markup=kb)

async def admin_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; s=sessions.get(u.id)
    if u.id!=ADMIN_ID or not s or s["step"]!="photo": return
    s["p"]["photo"]=update.message.photo[-1].file_id
    s["step"]="description"
    await update.message.reply_text("Теперь отправь описание товара.")

def verify_init_data(raw):
    try:
        values=dict(parse_qsl(raw,keep_blank_values=True))
        received=values.pop("hash")
        check="\n".join(f"{k}={v}" for k,v in sorted(values.items()))
        secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
        calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc,received): return None
        if time.time()-int(values.get("auth_date","0"))>86400: return None
        return json.loads(values["user"])
    except Exception: return None

def current_user():
    u=verify_init_data(request.headers.get("X-Telegram-Init-Data",""))
    if not u or not u.get("id"): return None
    with db() as c:
        c.execute("INSERT INTO users(id,username,first_name) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,blocked=0",
                  (u["id"],u.get("username",""),u.get("first_name","")))
    return u

def is_channel_member(uid):
    try:
        r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                       params={"chat_id":"@"+CHANNEL,"user_id":uid},timeout=10).json()
        if not r.get("ok"): return None
        m=r["result"]
        return m["status"] in ("creator","administrator","member") or (m["status"]=="restricted" and m.get("is_member"))
    except Exception: return None

@web.get("/")
def home(): return send_from_directory(".", "index.html")

@web.get("/api/config")
def config(): return jsonify(maintenance=get_setting("maintenance")=="1",channelUrl=CHANNEL_URL)

@web.get("/api/products")
def products_api():
    u=current_user()
    if not u: return jsonify(error="Открой магазин через кнопку в Telegram-боте."),401
    if get_setting("maintenance")=="1": return jsonify(maintenance=True,products=[])
    member=is_channel_member(u["id"])
    if member is False: return jsonify(subscriptionRequired=True,channelUrl=CHANNEL_URL),403
    if member is None: return jsonify(error="Не удалось проверить подписку. Сделай бота администратором канала."),503
    with db() as c: rows=c.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return jsonify(products=[{"id":fmt(p["id"]),"title":p["title"],"price":p["price"],
        "order_text":p["order_text"],"description":p["description"],
        "imageUrl":f"/api/photo/{p['id']}" if p["photo"] else None} for p in rows])

@web.get("/api/photo/<int:pid>")
def photo_api(pid):
    u=current_user()
    if not u: return jsonify(error="Unauthorized"),401
    if is_channel_member(u["id"]) is not True: return jsonify(error="Подпишись на канал"),403
    with db() as c: p=c.execute("SELECT photo FROM products WHERE id=?",(pid,)).fetchone()
    if not p or not p["photo"]: return jsonify(error="Фото не найдено"),404
    try:
        f=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",params={"file_id":p["photo"]},timeout=10).json()
        if not f.get("ok"): return jsonify(error="Telegram file error"),502
        path=f["result"]["file_path"]
        img=requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}",timeout=20)
        img.raise_for_status()
        return send_file(BytesIO(img.content),mimetype=img.headers.get("Content-Type","image/jpeg"))
    except Exception as e:
        log.exception("photo proxy error: %s",e)
        return jsonify(error="Не удалось загрузить фото"),502

@web.get("/api/favorites")
def favorites_api():
    u=current_user()
    if not u: return jsonify(error="Unauthorized"),401
    with db() as c: rows=c.execute("SELECT product_id FROM favorites WHERE user_id=?",(u["id"],)).fetchall()
    return jsonify(ids=[fmt(x["product_id"]) for x in rows])

@web.post("/api/favorites/<pid>")
def toggle_favorite(pid):
    u=current_user()
    if not u: return jsonify(error="Unauthorized"),401
    try: n=int(pid.lstrip("#"))
    except: return jsonify(error="Bad product ID"),400
    with db() as c:
        if not c.execute("SELECT 1 FROM products WHERE id=?",(n,)).fetchone(): return jsonify(error="Товар не найден"),404
        exists=c.execute("SELECT 1 FROM favorites WHERE user_id=? AND product_id=?",(u["id"],n)).fetchone()
        if exists: c.execute("DELETE FROM favorites WHERE user_id=? AND product_id=?",(u["id"],n)); fav=False
        else: c.execute("INSERT OR IGNORE INTO favorites VALUES(?,?)",(u["id"],n)); fav=True
    return jsonify(favorite=fav)

@web.get("/health")
def health(): return jsonify(ok=True,service="SKY SHOP")

def run_web():
    web.run(host="0.0.0.0",port=PORT,threaded=True,use_reloader=False)

def main():
    global bot_app
    init_db()
    bot_app=Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start",start))
    bot_app.add_handler(CommandHandler("admin",admin_cmd))
    bot_app.add_handler(CallbackQueryHandler(callbacks))
    bot_app.add_handler(MessageHandler(filters.PHOTO,admin_photo))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,admin_text))
    threading.Thread(target=run_web,daemon=True).start()
    log.info("SKY SHOP bot + API starting")
    bot_app.run_polling()

if __name__=="__main__": main()
