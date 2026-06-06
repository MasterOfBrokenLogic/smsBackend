import asyncio
import os
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
BASE_URL = os.environ["BASE_URL"].rstrip("/")

db: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
telegram_app: Application = None
user_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    try:
        telegram_app = Application.builder().token(BOT_TOKEN).build()
        telegram_app.add_handler(CommandHandler("start", cmd_start))
        telegram_app.add_handler(CallbackQueryHandler(handle_callback))
        telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        await telegram_app.initialize()
        await telegram_app.start()
        try:
            await telegram_app.bot.set_webhook(
                url=f"{BASE_URL}/webhook",
                secret_token=WEBHOOK_SECRET,
            )
            print(f"Webhook set to {BASE_URL}/webhook")
        except Exception as e:
            print(f"Webhook setup failed (non-fatal): {e}")
    except Exception as e:
        print(f"Startup error: {e}")
        raise
    yield
    try:
        await telegram_app.stop()
        await telegram_app.shutdown()
    except Exception as e:
        print(f"Shutdown error: {e}")


fastapi_app = FastAPI(lifespan=lifespan)


def fmt_time(ts_str: str) -> str:
    if not ts_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt
        secs = int(diff.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return ts_str


def sender_label(sender: str) -> str:
    known = {
        "truecaller": "Truecaller", "jio": "Jio", "airtel": "Airtel",
        "bsnl": "BSNL", "sbi": "SBI", "hdfc": "HDFC", "icici": "ICICI",
        "axis": "Axis Bank", "kotak": "Kotak", "paytm": "Paytm",
        "phonepe": "PhonePe", "gpay": "Google Pay", "amazon": "Amazon",
        "flipkart": "Flipkart", "swiggy": "Swiggy", "zomato": "Zomato",
        "ola": "Ola", "uber": "Uber", "instagram": "Instagram",
        "whatsapp": "WhatsApp", "google": "Google", "apple": "Apple",
        "netflix": "Netflix", "hotstar": "Hotstar", "zepto": "Zepto",
        "blinkit": "Blinkit", "dunzo": "Dunzo", "cred": "CRED",
    }
    s = sender.lower()
    for key, label in known.items():
        if key in s:
            return label
    if sender.startswith("+") or sender.lstrip("+").isdigit():
        return sender
    parts = sender.lstrip("0123456789-").strip()
    return parts.upper() if len(parts) <= 6 else parts.title()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    keyboard = [
        [InlineKeyboardButton("Devices", callback_data="menu:devices")],
        [InlineKeyboardButton("Unread Messages", callback_data="menu:unread")],
        [InlineKeyboardButton("Device Status", callback_data="menu:status")],
    ]
    await update.message.reply_text(
        "Control Panel",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def main_menu(query):
    keyboard = [
        [InlineKeyboardButton("Devices", callback_data="menu:devices")],
        [InlineKeyboardButton("Unread Messages", callback_data="menu:unread")],
        [InlineKeyboardButton("Device Status", callback_data="menu:status")],
    ]
    await query.edit_message_text(
        "Control Panel",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_CHAT_ID:
        return
    await query.answer()
    data = query.data

    if data == "menu:main":
        await main_menu(query)
    elif data == "menu:devices":
        await show_devices(query)
    elif data == "menu:unread":
        await show_unread(query)
    elif data == "menu:status":
        await show_status(query)
    elif data.startswith("device:"):
        device_id = data.split(":", 1)[1]
        await show_device_menu(query, device_id)
    elif data.startswith("dev_messages:"):
        parts = data.split(":")
        device_id = parts[1]
        offset = int(parts[2]) if len(parts) > 2 else 0
        await show_messages(query, device_id, offset)
    elif data.startswith("view_msg:"):
        msg_id = data.split(":", 1)[1]
        await view_message(query, msg_id)
    elif data.startswith("dev_search:"):
        device_id = data.split(":", 1)[1]
        user_state[query.from_user.id] = {"action": "search", "device_id": device_id}
        await query.edit_message_text(
            "Enter search keyword:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data=f"device:{device_id}")]]
            ),
        )
    elif data.startswith("search_result:"):
        parts = data.split(":")
        device_id = parts[1]
        msg_id = parts[2]
        await view_search_result(query, device_id, msg_id)
    elif data.startswith("dev_send:"):
        device_id = data.split(":", 1)[1]
        user_state[query.from_user.id] = {"action": "send_recipient", "device_id": device_id}
        await query.edit_message_text(
            "Enter recipient number:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data=f"device:{device_id}")]]
            ),
        )
    elif data.startswith("send_confirm:"):
        parts = data.split(":", 2)
        device_id = parts[1]
        queue_id = parts[2]
        db.table("outbound_queue").update({"status": "confirmed"}).eq("id", queue_id).execute()
        await query.edit_message_text("SMS queued for sending.")
    elif data.startswith("send_cancel:"):
        parts = data.split(":", 2)
        device_id = parts[1]
        queue_id = parts[2]
        db.table("outbound_queue").delete().eq("id", queue_id).execute()
        await show_device_menu(query, device_id)
    elif data.startswith("dev_rename:"):
        device_id = data.split(":", 1)[1]
        user_state[query.from_user.id] = {"action": "rename", "device_id": device_id}
        await query.edit_message_text(
            "Enter new name for this device:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data=f"device:{device_id}")]]
            ),
        )


async def show_devices(query):
    res = db.table("devices").select("*").order("created_at").execute()
    devices = res.data
    if not devices:
        await query.edit_message_text(
            "No devices registered yet.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:main")]]
            ),
        )
        return
    keyboard = []
    for d in devices:
        status = "Online" if d["is_online"] else "Offline"
        keyboard.append([InlineKeyboardButton(
            f"{d['name']}  —  {status}",
            callback_data=f"device:{d['id']}"
        )])
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu:main")])
    await query.edit_message_text(
        "Select a device:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_device_menu(query, device_id: str):
    res = db.table("devices").select("*").eq("id", device_id).single().execute()
    d = res.data
    unread = db.table("messages").select("id", count="exact").eq("device_id", device_id).eq("is_read", False).execute()
    unread_count = unread.count or 0
    status_line = "Online" if d["is_online"] else f"Offline  —  Last seen: {fmt_time(d['last_seen'])}"
    battery = f"Battery: {d['battery_level']}%" if d["battery_level"] is not None else "Battery: N/A"
    charging = "Charging" if d["is_charging"] else "Not charging"
    network = d["network_type"] or "Unknown"
    info = f"{d['name']}\n{status_line}\n{battery}  |  {charging}  |  {network}"
    keyboard = [
        [InlineKeyboardButton(f"Messages ({unread_count} unread)", callback_data=f"dev_messages:{device_id}:0")],
        [InlineKeyboardButton("Send SMS", callback_data=f"dev_send:{device_id}")],
        [InlineKeyboardButton("Search", callback_data=f"dev_search:{device_id}")],
        [InlineKeyboardButton("Rename Device", callback_data=f"dev_rename:{device_id}")],
        [InlineKeyboardButton("Back", callback_data="menu:devices")],
    ]
    await query.edit_message_text(info, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_messages(query, device_id: str, offset: int):
    days = 5
    since = (datetime.now(timezone.utc) - timedelta(days=days + (offset // 20) * 5)).isoformat()
    res = (
        db.table("messages")
        .select("*")
        .eq("device_id", device_id)
        .gte("received_at", since)
        .order("received_at", desc=True)
        .range(offset, offset + 19)
        .execute()
    )
    messages = res.data
    keyboard = []
    for m in messages:
        label = sender_label(m["sender"])
        time_label = fmt_time(m["received_at"])
        read_dot = "" if m["is_read"] else "  *"
        keyboard.append([InlineKeyboardButton(
            f"{label}  —  {time_label}{read_dot}",
            callback_data=f"view_msg:{m['id']}"
        )])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("Newer", callback_data=f"dev_messages:{device_id}:{offset - 20}"))
    if len(messages) == 20:
        nav.append(InlineKeyboardButton("Load more", callback_data=f"dev_messages:{device_id}:{offset + 20}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("Back", callback_data=f"device:{device_id}")])
    device = db.table("devices").select("name").eq("id", device_id).single().execute()
    await query.edit_message_text(
        f"Messages  —  {device.data['name']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def view_message(query, msg_id: str):
    res = db.table("messages").select("*, devices(name, id)").eq("id", msg_id).single().execute()
    m = res.data
    db.table("messages").update({"is_read": True}).eq("id", msg_id).execute()
    device_id = m["device_id"]
    time_str = fmt_time(m["received_at"])
    text = f"From: {m['sender']}\n{time_str}\n\n{m['body']}"
    keyboard = [[InlineKeyboardButton("Back", callback_data=f"dev_messages:{device_id}:0")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def view_search_result(query, device_id: str, msg_id: str):
    res = db.table("messages").select("*").eq("id", msg_id).single().execute()
    m = res.data
    db.table("messages").update({"is_read": True}).eq("id", msg_id).execute()
    time_str = fmt_time(m["received_at"])
    text = f"From: {m['sender']}\n{time_str}\n\n{m['body']}"
    keyboard = [[InlineKeyboardButton("Back", callback_data=f"device:{device_id}")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def show_unread(query):
    res = (
        db.table("messages")
        .select("*, devices(name, id)")
        .eq("is_read", False)
        .order("received_at", desc=True)
        .limit(30)
        .execute()
    )
    messages = res.data
    if not messages:
        await query.edit_message_text(
            "No unread messages.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:main")]]
            ),
        )
        return
    keyboard = []
    for m in messages:
        label = sender_label(m["sender"])
        device_name = m["devices"]["name"] if m.get("devices") else "Unknown"
        time_label = fmt_time(m["received_at"])
        keyboard.append([InlineKeyboardButton(
            f"{device_name}  —  {label}  —  {time_label}",
            callback_data=f"view_msg:{m['id']}"
        )])
    keyboard.append([InlineKeyboardButton("Back", callback_data="menu:main")])
    await query.edit_message_text(
        "Unread Messages",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_status(query):
    res = db.table("devices").select("*").order("created_at").execute()
    devices = res.data
    if not devices:
        await query.edit_message_text(
            "No devices registered.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Back", callback_data="menu:main")]]
            ),
        )
        return
    lines = ["Device Status\n"]
    for d in devices:
        status = "Online" if d["is_online"] else "Offline"
        last = fmt_time(d["last_seen"])
        battery = f"{d['battery_level']}%" if d["battery_level"] is not None else "N/A"
        charging = "Charging" if d["is_charging"] else "Not charging"
        network = d["network_type"] or "Unknown"
        lines.append(
            f"{d['name']}\n"
            f"  Status: {status}  |  Last seen: {last}\n"
            f"  Battery: {battery}  |  {charging}\n"
            f"  Network: {network}\n"
        )
    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data="menu:status")],
        [InlineKeyboardButton("Back", callback_data="menu:main")],
    ]
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    uid = update.effective_user.id
    state = user_state.get(uid)
    if not state:
        return
    text = update.message.text.strip()
    action = state["action"]
    device_id = state.get("device_id")

    if action == "search":
        keyword = text.lower()
        res = (
            db.table("messages")
            .select("*")
            .eq("device_id", device_id)
            .ilike("body", f"%{keyword}%")
            .order("received_at", desc=True)
            .limit(30)
            .execute()
        )
        also_sender = (
            db.table("messages")
            .select("*")
            .eq("device_id", device_id)
            .ilike("sender", f"%{keyword}%")
            .order("received_at", desc=True)
            .limit(10)
            .execute()
        )
        seen_ids = set()
        results = []
        for m in res.data + also_sender.data:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                results.append(m)
        del user_state[uid]
        if not results:
            await update.message.reply_text("No messages found.")
            return
        keyboard = []
        for m in results:
            label = sender_label(m["sender"])
            time_label = fmt_time(m["received_at"])
            keyboard.append([InlineKeyboardButton(
                f"{label}  —  {time_label}",
                callback_data=f"search_result:{device_id}:{m['id']}"
            )])
        keyboard.append([InlineKeyboardButton("Back", callback_data=f"device:{device_id}")])
        await update.message.reply_text(
            f"Results for '{text}'",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif action == "send_recipient":
        user_state[uid] = {"action": "send_body", "device_id": device_id, "recipient": text}
        await update.message.reply_text(
            f"Recipient: {text}\nEnter message:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Cancel", callback_data=f"device:{device_id}")]]
            ),
        )

    elif action == "send_body":
        recipient = state["recipient"]
        res = db.table("outbound_queue").insert({
            "device_id": device_id,
            "recipient": recipient,
            "body": text,
            "status": "pending",
        }).execute()
        queue_id = res.data[0]["id"]
        del user_state[uid]
        device = db.table("devices").select("name").eq("id", device_id).single().execute()
        keyboard = [
            [
                InlineKeyboardButton("Confirm", callback_data=f"send_confirm:{device_id}:{queue_id}"),
                InlineKeyboardButton("Cancel", callback_data=f"send_cancel:{device_id}:{queue_id}"),
            ]
        ]
        await update.message.reply_text(
            f"Device: {device.data['name']}\nTo: {recipient}\n\n{text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif action == "rename":
        db.table("devices").update({"name": text}).eq("id", device_id).execute()
        del user_state[uid]
        await update.message.reply_text(f"Device renamed to: {text}")


@fastapi_app.post("/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})


class SmsPayload(BaseModel):
    sender: str
    body: str
    received_at: str = None


class StatusPayload(BaseModel):
    battery_level: int = None
    is_charging: bool = False
    network_type: str = None


@fastapi_app.post("/api/sms")
async def receive_sms(payload: SmsPayload, x_device_token: str = Header(...)):
    res = db.table("devices").select("*").eq("token", x_device_token).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Unknown device")
    device = res.data[0]
    device_id = device["id"]
    received_at = payload.received_at or datetime.now(timezone.utc).isoformat()
    msg_res = db.table("messages").insert({
        "device_id": device_id,
        "sender": payload.sender,
        "body": payload.body,
        "received_at": received_at,
        "is_read": False,
    }).execute()
    msg_id = msg_res.data[0]["id"]
    label = sender_label(payload.sender)
    keyboard = [[InlineKeyboardButton("View", callback_data=f"view_msg:{msg_id}")]]
    sent = await telegram_app.bot.send_message(
        chat_id=OWNER_CHAT_ID,
        text=f"New SMS on {device['name']}  —  {label}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    db.table("messages").update({"tg_message_id": sent.message_id}).eq("id", msg_id).execute()
    return {"ok": True}


@fastapi_app.post("/api/status")
async def update_status(payload: StatusPayload, x_device_token: str = Header(...)):
    res = db.table("devices").select("id").eq("token", x_device_token).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Unknown device")
    device_id = res.data[0]["id"]
    db.table("devices").update({
        "battery_level": payload.battery_level,
        "is_charging": payload.is_charging,
        "network_type": payload.network_type,
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "is_online": True,
    }).eq("id", device_id).execute()
    return {"ok": True}


@fastapi_app.get("/api/pending/{device_token}")
async def get_pending(device_token: str):
    res = db.table("devices").select("id").eq("token", device_token).execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Unknown device")
    device_id = res.data[0]["id"]
    pending = (
        db.table("outbound_queue")
        .select("*")
        .eq("device_id", device_id)
        .eq("status", "confirmed")
        .execute()
    )
    return {"queue": pending.data}


@fastapi_app.post("/api/pending/{queue_id}/done")
async def mark_done(queue_id: str, x_device_token: str = Header(...)):
    db.table("outbound_queue").update({"status": "sent"}).eq("id", queue_id).execute()
    return {"ok": True}


@fastapi_app.get("/ping")
async def ping():
    return {"ok": True}
