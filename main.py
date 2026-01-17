import os
import asyncio
import random
import string
import aiohttp
import re
import sys
import time
from datetime import datetime, timedelta
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from motor.motor_asyncio import AsyncIOMotorClient

# ==================== ১. কনফিগারেশন ====================
API_ID = 29904834                 
API_HASH = "8b4fd9ef578af114502feeafa2d31938"        
BOT_TOKEN = "8313292799:AAHxjrKVfbaMTA89fasbJSva-2u55pzraJ4"      
ADMIN_ID = 7525127704              
MONGODB_URI = "mongodb+srv://MDParvezHossain:MDParvezHossain@cluster0.pma8wsn.mongodb.net/?appName=Cluster0"   
OWNER_USERNAME = "AkashDeveloperBot"   

# ডিফল্ট সেটিংস
DEFAULT_LOG_CHANNEL = -1003513942313

# ==================== ২. ডাটাবেস কানেকশন ====================
db_client = AsyncIOMotorClient(MONGODB_URI)
db = db_client["file_store_pro_db"]
users_col = db["users"]
files_col = db["stored_files"]
channels_col = db["channels"] 
settings_col = db["settings"]
plans_col = db["plans"]
banned_users = db["banned_users"]

app = Client("file_store_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==================== ৩. সাহায্যকারী ফাংশনসমূহ ====================

def parse_duration_advanced(t_str):
    """উন্নত টাইম পার্সার: y, mo, w, d, h, m, s"""
    t_str = t_str.lower().strip()
    match = re.match(r"(\d+)([a-z]+)", t_str)
    if not match: return None
    value, unit = int(match.group(1)), match.group(2)
    if unit in ['y', 'year', 'years']: return timedelta(days=value * 365)
    if unit in ['mo', 'month', 'months']: return timedelta(days=value * 30)
    if unit in ['w', 'week', 'weeks']: return timedelta(weeks=value)
    if unit in ['d', 'day', 'days']: return timedelta(days=value)
    if unit in ['h', 'hour', 'hours']: return timedelta(hours=value)
    if unit in ['m', 'min', 'minute', 'minutes']: return timedelta(minutes=value)
    if unit in ['s', 'sec', 'second', 'seconds']: return timedelta(seconds=value)
    return None

async def get_settings(id, key, default=None):
    data = await settings_col.find_one({"id": id})
    if data: return data.get(key, default)
    return default

async def check_premium(user_id):
    if user_id == ADMIN_ID: return True, "Owner/Admin"
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_premium"):
        expiry = user.get("expiry_date")
        if expiry and datetime.now() > expiry:
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_premium": False}})
            return False, "ফ্রী মেম্বার (মেয়াদ শেষ)"
        return True, (expiry.strftime('%Y-%m-%d %H:%M') if expiry else "লাইফটাইম")
    return False, "ফ্রী মেম্বার"

async def get_shortlink(url):
    # সর্টেনার স্ট্যাটাস চেক
    is_active = await get_settings("shortener", "status", True)
    if not is_active: return url

    s_url = await get_settings("shortener", "base_url")
    s_key = await get_settings("shortener", "api_key")
    if not s_url or not s_key: return url
    api_url = f"https://{s_url}/api?api={s_key}&url={url}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as res:
                data = await res.json()
                return data.get("shortenedUrl") or data.get("url") or url
    except: return url

async def auto_delete_msg(chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try: await app.delete_messages(chat_id, message_id)
    except: pass

async def send_log(text):
    log_chat = await get_settings("log_channel", "chat_id", DEFAULT_LOG_CHANNEL)
    try: await app.send_message(log_chat, text)
    except: pass

# ==================== ৪. কোর ফাইল ডেলিভারি লজিক ====================

async def send_files_logic(client, message, cmd_name, is_extra=False, already_verified=False):
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    
    # ব্যান চেক
    if await banned_users.find_one({"user_id": user_id}):
        msg_text = "🚫 আপনি ব্যান!"
        if hasattr(message, 'reply'): return await message.reply(msg_text)
        else: return await message.message.reply(msg_text)

    if is_extra:
        chat_id_data = await settings_col.find_one({"id": "extra_channel"})
        if not chat_id_data:
            msg = "❌ গেট ফাইল চ্যানেল সেট করা নেই।"
            return await (message.reply(msg) if hasattr(message, 'reply') else message.message.reply(msg))
        chat_id = chat_id_data["chat_id"]
        db_cmd_key = "extra_files_global"
    else:
        channel_data = await channels_col.find_one({"command": cmd_name})
        if not channel_data:
            return await message.reply(f"❌ `{cmd_name}` কমান্ডটি বর্তমানে ডাটাবেসে নেই।")
        chat_id = channel_data["chat_id"]
        db_cmd_key = cmd_name

    is_prem, _ = await check_premium(user_id)
    shortener_status = await get_settings("shortener", "status", True)
    
    user_data = await users_col.find_one({"user_id": user_id}) or {}
    indices = user_data.get("indices", {})
    current_idx = indices.get(db_cmd_key, 0)
    limit_val = await get_settings("video_limit", "count", 2)

    # প্রিমিয়াম ইউজার অথবা সর্টেনার অফ থাকলে অথবা ইতিমধ্যে ভেরিফাইড হলে ফাইল পাঠাবে
    if is_prem or not shortener_status or already_verified:
        files = await files_col.find({"chat_id": chat_id}).sort("msg_id", 1).skip(current_idx).limit(limit_val).to_list(limit_val)
        
        if not files:
            indices[db_cmd_key] = 0
            await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}}, upsert=True)
            text = "✅ এই ক্যাটাগরির সব ফাইল দেখা শেষ! আবার শুরু থেকে দেখতে কমান্ডটি দিন।"
            if hasattr(message, 'reply'): return await message.reply(text)
            else: return await message.message.reply(text)
        
        timer_sec = await get_settings("auto_delete", "seconds")
        protect = await get_settings("forward_setting", "protect", False)

        for f in files:
            try:
                sent = await client.copy_message(user_id, f["chat_id"], f["msg_id"], protect_content=protect)
                if sent and timer_sec:
                    asyncio.create_task(auto_delete_msg(user_id, sent.id, timer_sec))
            except: continue
        
        indices[db_cmd_key] = current_idx + len(files)
        await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}}, upsert=True)
    else:
        # ভেরিফিকেশন লিংক লজিক
        me = await client.get_me()
        v_type = "extra" if is_extra else cmd_name
        v_url = await get_shortlink(f"https://t.me/{me.username}?start=verify_{v_type}")
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 ভেরিফাই লিংক (Verify)", url=v_url)]])
        text = "🚫 **ভেরিফিকেশন আবশ্যক!**\n\nফাইল পেতে নিচে ক্লিক করে ভেরিফাই করুন। প্রিমিয়াম মেম্বার হলে সরাসরি ফাইল পাবেন।"
        if hasattr(message, 'reply'): await message.reply(text, reply_markup=btn)
        else: await message.message.reply(text, reply_markup=btn)

# ==================== ৫. অ্যাডমিন ম্যানেজমেন্ট কমান্ডসমূহ ====================

@app.on_message(filters.command("addcnl") & filters.user(ADMIN_ID))
async def add_cnl_handler(client, message):
    if len(message.command) < 3: return await message.reply("📝 উদা: `/addcnl -100xxxx movies`")
    try:
        c_id, cmd = int(message.command[1]), message.command[2].lower()
        chat = await client.get_chat(c_id)
        await channels_col.update_one({"command": cmd}, {"$set": {"chat_id": c_id, "title": chat.title, "command": cmd}}, upsert=True)
        st = await message.reply(f"✅ `{chat.title}` লিঙ্কড। ইন্ডেক্সিং হচ্ছে...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await st.edit(f"✅ সম্পন্ন! মোট `{count}` টি ফাইল `{cmd}` কমান্ডে সেভ হয়েছে।")
    except Exception as e: await message.reply(f"এরর: {e}")

@app.on_message(filters.command("deleteall") & filters.user(ADMIN_ID))
async def delete_all_handler(client, message):
    if len(message.command) < 2: return await message.reply("📝 উদা: `/deleteall -100xxxx` (চ্যানেল আইডি দিন)")
    try:
        c_id = int(message.command[1])
        res = await files_col.delete_many({"chat_id": c_id})
        await channels_col.delete_one({"chat_id": c_id})
        await message.reply(f"✅ সম্পন্ন! চ্যানেল `{c_id}` এর মোট `{res.deleted_count}` টি ফাইল ডাটাবেস থেকে রিমুভ করা হয়েছে।")
    except Exception as e: await message.reply(f"এরর: {e}")

@app.on_message(filters.command("shortener") & filters.user(ADMIN_ID))
async def shortener_toggle_cmd(client, message):
    if len(message.command) < 2: return await message.reply("📝 `/shortener on` অথবা `/shortener off` output")
    status = message.command[1].lower() == "on"
    await settings_col.update_one({"id": "shortener"}, {"$set": {"status": status}}, upsert=True)
    await message.reply(f"✅ সর্টেনার লিঙ্ক এখন **{'চালু (ON)' if status else 'বন্ধ (OFF)'}** করা হয়েছে।")

@app.on_message(filters.command("extfile") & filters.user(ADMIN_ID))
async def ext_file_handler(client, message):
    if len(message.command) < 2: return await message.reply("📝 উদা: `/extfile -100xxxx`")
    try:
        c_id = int(message.command[1])
        chat = await client.get_chat(c_id)
        await settings_col.update_one({"id": "extra_channel"}, {"$set": {"chat_id": c_id, "title": chat.title}}, upsert=True)
        st = await message.reply(f"🚀 গেট ফাইল চ্যানেল সেট: `{chat.title}`। ইন্ডেক্সিং...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await st.edit(f"✅ সম্পন্ন! গেট ফাইল চ্যানেলে `{count}` ফাইল সেভ হয়েছে।")
    except Exception as e: await message.reply(f"এরর: {e}")

@app.on_message(filters.command("add_plan") & filters.user(ADMIN_ID))
async def add_plan_handler(client, message):
    if len(message.command) < 3: return await message.reply("📝 উদা: `/add_plan 30Days 100Tk` (স্পেস দিন)")
    name, price = message.command[1], message.command[2]
    await plans_col.update_one({"name": name}, {"$set": {"name": name, "price": price}}, upsert=True)
    await message.reply(f"✅ প্রিমিয়াম প্ল্যান অ্যাড হয়েছে: `{name}` - `{price}`")

@app.on_message(filters.command("del_plan") & filters.user(ADMIN_ID))
async def del_plan_handler(client, message):
    if len(message.command) < 2: return await message.reply("📝 উদা: `/del_plan 30Days` (প্ল্যানের নাম দিন)")
    name = message.command[1]
    res = await plans_col.delete_one({"name": name})
    if res.deleted_count > 0:
        await message.reply(f"✅ প্রিমিয়াম প্ল্যান `{name}` ডিলিট করা হয়েছে।")
    else:
        await message.reply(f"❌ `{name}` নামে কোনো প্ল্যান ডাটাবেসে পাওয়া যায়নি।")

@app.on_message(filters.command("add_premium") & filters.user(ADMIN_ID))
async def add_prem_handler(client, message):
    try:
        u_id, dur_str = int(message.command[1]), message.command[2]
        duration = parse_duration_advanced(dur_str)
        if not duration: return await message.reply("❌ ভুল ফরম্যাট! (y, mo, w, d, h, m)")
        expiry = datetime.now() + duration
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": True, "expiry_date": expiry}}, upsert=True)
        await message.reply(f"✅ ইউজার `{u_id}` প্রিমিয়াম হয়েছে। মেয়াদ: `{expiry.strftime('%Y-%m-%d %H:%M')}`")
        await send_log(f"💎 **নতুন প্রিমিয়াম মেম্বার:**\nID: `{u_id}`\nমেয়াদ: {dur_str}")
    except: await message.reply("📝 `/add_premium ID 1mo` (y, mo, d, h, m সাপোর্ট করে)")

@app.on_message(filters.command("remove_premium") & filters.user(ADMIN_ID))
async def rem_prem_handler(client, message):
    try:
        u_id = int(message.command[1])
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": False}, "$unset": {"expiry_date": ""}})
        await message.reply(f"✅ ইউজার `{u_id}` এখন সাধারণ মেম্বার।")
    except: await message.reply("📝 `/remove_premium ID`")

@app.on_message(filters.command("premium_list") & filters.user(ADMIN_ID))
async def prem_list_admin(client, message):
    users = await users_col.find({"is_premium": True}).to_list(None)
    if not users: return await message.reply("ℹ️ কোনো প্রিমিয়াম মেম্বার নেই।")
    txt = "💎 **প্রিমিয়াম মেম্বার লিস্ট:**\n\n"
    for u in users:
        exp = u.get('expiry_date')
        txt += f"👤 `{u['user_id']}` | 📅 `{exp.strftime('%Y-%m-%d %H:%M') if exp else 'LifeTime'}`\n"
    await message.reply(txt)

@app.on_message(filters.command("set_timer") & filters.user(ADMIN_ID))
async def timer_handler(client, message):
    try:
        sec = int(message.command[1])
        await settings_col.update_one({"id": "auto_delete"}, {"$set": {"seconds": sec}}, upsert=True)
        await message.reply(f"✅ অটো ডিলিট `{sec}` সেকেন্ড সেট হয়েছে।")
    except: await message.reply("📝 `/set_timer 600` (১০ মিনিট)")

@app.on_message(filters.command("set_limit") & filters.user(ADMIN_ID))
async def limit_handler(client, message):
    try:
        lim = int(message.command[1])
        await settings_col.update_one({"id": "video_limit"}, {"$set": {"count": lim}}, upsert=True)
        await message.reply(f"✅ ভিডিও লিমিট `{lim}` সেট হয়েছে।")
    except: await message.reply("📝 `/set_limit 5`")

@app.on_message(filters.command("set_shortener") & filters.user(ADMIN_ID))
async def short_set_handler(client, message):
    try:
        url, key = message.command[1], message.command[2]
        await settings_col.update_one({"id": "shortener"}, {"$set": {"base_url": url, "api_key": key}}, upsert=True)
        await message.reply("✅ সর্টেনার কনফিগারেশন সেট হয়েছে।")
    except: await message.reply("📝 `/set_shortener domain.com key`")

@app.on_message(filters.command("set_log") & filters.user(ADMIN_ID))
async def log_set_handler(client, message):
    try:
        c_id = int(message.command[1])
        await settings_col.update_one({"id": "log_channel"}, {"$set": {"chat_id": c_id}}, upsert=True)
        await message.reply("✅ লগ চ্যানেল সেট হয়েছে।")
    except: await message.reply("📝 `/set_log -100xxxx`")

@app.on_message(filters.command("set_protect") & filters.user(ADMIN_ID))
async def protect_set_handler(client, message):
    try:
        val = message.command[1].lower() == "on"
        await settings_col.update_one({"id": "forward_setting"}, {"$set": {"protect": val}}, upsert=True)
        await message.reply(f"✅ ফরওয়ার্ড প্রোটেকশন {'চালু' if val else 'বন্ধ'} হয়েছে।")
    except: await message.reply("📝 `/set_protect on/off`")

@app.on_message(filters.command("ban") & filters.user(ADMIN_ID))
async def ban_handler(client, message):
    try:
        u_id = int(message.command[1])
        await banned_users.update_one({"user_id": u_id}, {"$set": {"user_id": u_id}}, upsert=True)
        await message.reply(f"✅ ইউজার `{u_id}` ব্যান হয়েছে।")
    except: await message.reply("📝 `/ban ID`")

@app.on_message(filters.command("unban") & filters.user(ADMIN_ID))
async def unban_handler(client, message):
    try:
        u_id = int(message.command[1])
        await banned_users.delete_one({"user_id": u_id})
        await message.reply(f"✅ ইউজার `{u_id}` আনব্যান হয়েছে।")
    except: await message.reply("📝 `/unban ID`")

@app.on_message(filters.command("broadcast") & filters.user(ADMIN_ID))
async def broadcast_handler(client, message):
    if not message.reply_to_message: return await message.reply("📝 ব্রডকাস্টের জন্য কোনো মেসেজ রিপ্লাই দিন।")
    st = await message.reply("📣 ব্রডকাস্ট শুরু হচ্ছে...")
    users = await users_col.find().to_list(None)
    done, fail = 0, 0
    for u in users:
        try:
            await message.reply_to_message.copy(u["user_id"])
            done += 1
        except FloodWait as e: await asyncio.sleep(e.x); await message.reply_to_message.copy(u["user_id"]); done += 1
        except: fail += 1
    await st.edit(f"✅ ব্রডকাস্ট সম্পন্ন!\nসফল: {done}\nব্যর্থ: {fail}")

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_handler(client, message):
    u = await users_col.count_documents({})
    p = await users_col.count_documents({"is_premium": True})
    f = await files_col.count_documents({})
    c = await channels_col.count_documents({})
    await message.reply(f"📊 **বট পরিসংখ্যান:**\n\n👤 মোট ইউজার: `{u}`\n💎 প্রিমিয়াম মেম্বার: `{p}`\n📁 মোট ফাইল: `{f}`\n🔗 মোট কমান্ড: `{c}`")

# ==================== ৬. ইউজার হ্যান্ডলার ও কমান্ডস ====================

@app.on_message(filters.command("plans") & filters.private)
async def plans_command_handler(client, message):
    plans = await plans_col.find().to_list(None)
    txt = "💎 **আমাদের প্রিমিয়াম প্ল্যানসমূহ:**\n\n"
    if not plans:
        txt += "🔹 বর্তমানে কোনো প্ল্যান অ্যাড করা নেই।\n"
    else:
        for p in plans: txt += f"🔹 {p['name']} - {p['price']}\n"
    txt += f"\n✅ সুবিধা: সরাসরি ফাইল পাবেন, কোনো ভেরিফিকেশন লাগবে না।\n💬 যোগাযোগ: @{OWNER_USERNAME}"
    await message.reply(txt)

@app.on_message(filters.command("skip") & filters.private)
async def skip_handler(client, message):
    if len(message.command) < 3: 
        return await message.reply("📝 উদা: `/skip movies 100` (মুভি কমান্ডের ১০০টি ফাইল স্কিপ করতে)\n`/skip movies 0` (আবার প্রথম থেকে শুরু হবে)")
    
    cmd = message.command[1].lower()
    try: num = int(message.command[2])
    except: return await message.reply("❌ সংখ্যাটি সঠিক নয়।")

    user_id = message.from_user.id
    db_key = "extra_files_global" if cmd in ["extra", "getfile"] else cmd

    await users_col.update_one({"user_id": user_id}, {"$set": {f"indices.{db_key}": num}}, upsert=True)
    if num == 0:
        await message.reply(f"✅ `{cmd}` কমান্ড এখন শুরু (০) থেকে ফাইল দেওয়া শুরু করবে।")
    else:
        await message.reply(f"✅ `{cmd}` কমান্ডের ইন্ডেক্স `{num}` এ সেট হয়েছে।")

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    if await banned_users.find_one({"user_id": user_id}): return
    await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True)

    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        v_type = message.command[1].replace("verify_", "")
        # ভেরিফাই করার পর 'already_verified=True' দিয়ে কল করা হয়েছে
        if v_type == "extra": return await send_files_logic(client, message, "", is_extra=True, already_verified=True)
        else: return await send_files_logic(client, message, v_type, already_verified=True)

    is_prem, status = await check_premium(user_id)
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Get Files", callback_data="get_extra_files")],
        [InlineKeyboardButton("💎 Plans", callback_data="show_plans"), InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]
    ])
    await message.reply_text(f"👋 আসসালামু আলাইকুম {message.from_user.first_name}!\n🆔 আইডি: `{user_id}`\n💎 মেম্বারশিপ: `{status}`\n\nফাইল পেতে নিচের বাটনে ক্লিক করুন অথবা কাস্টম কমান্ড ব্যবহার করুন।", reply_markup=btn)

@app.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    user_id = query.from_user.id
    if query.data == "get_extra_files":
        await send_files_logic(client, query, "", is_extra=True)
    elif query.data == "show_plans":
        plans = await plans_col.find().to_list(None)
        txt = "💎 **আমাদের প্রিমিয়াম প্ল্যানসমূহ:**\n\n"
        if not plans: txt += "🔹 বর্তমানে কোনো প্ল্যান নেই।\n"
        else:
            for p in plans: txt += f"🔹 {p['name']} - {p['price']}\n"
        txt += f"\n✅ সুবিধা: সরাসরি ফাইল পাবেন।\n💬 যোগাযোগ: @{OWNER_USERNAME}"
        await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]))
    elif query.data == "back_home":
        _, st = await check_premium(user_id)
        await query.message.edit_text(f"স্বাগতম!\n💎 মেম্বারশিপ: {st}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Get Files", callback_data="get_extra_files")],
            [InlineKeyboardButton("💎 Plans", callback_data="show_plans")]
        ]))
    await query.answer()

@app.on_message(filters.command("getfile") & filters.private)
async def getfile_direct(client, message):
    await send_files_logic(client, message, "", is_extra=True)

@app.on_message(filters.text & filters.private)
async def custom_detector(client, message):
    if not message.text.startswith("/"): return
    cmd = message.text.split()[0].replace("/", "").lower()
    
    # সিস্টেম কমান্ডগুলো এভয়েড করা
    sys_cmds = ["start", "stats", "premium_list", "remove_premium", "add_premium", "addcnl", "extfile", "getfile", 
                "set_timer", "set_limit", "set_shortener", "add_plan", "del_plan", "broadcast", "ban", "unban", "set_log", "set_protect", 
                "deleteall", "skip", "shortener", "plans"]
    if cmd in sys_cmds: return
    
    exists = await channels_col.find_one({"command": cmd})
    if exists: await send_files_logic(client, message, cmd)

@app.on_message((filters.video | filters.document | filters.audio) & ~filters.private)
async def auto_save(client, message):
    chat_id = message.chat.id
    is_saved = await channels_col.find_one({"chat_id": chat_id})
    extra = await settings_col.find_one({"id": "extra_channel", "chat_id": chat_id})
    if is_saved or extra:
        await files_col.update_one({"chat_id": chat_id, "msg_id": message.id}, {"$set": {"chat_id": chat_id, "msg_id": message.id}}, upsert=True)
        await send_log(f"📥 **নতুন ফাইল সেভ:**\nচ্যানেল: {message.chat.title}\nID: {message.id}")

# ==================== ৭. ওয়েব সার্ভার ও রান ====================

async def main():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Alive and Strong! 🚀"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    
    await app.start()
    print(">>> বট সফলভাবে চালু হয়েছে! ম্যাসিভ কন্ট্রোল প্যানেল সক্রিয়। <<<")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
