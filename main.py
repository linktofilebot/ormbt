import os
import asyncio
import random
import string
import aiohttp
import re
from datetime import datetime, timedelta
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient

# ==================== ১. কনফিগারেশন ====================
API_ID = 29904834                 
API_HASH = "8b4fd9ef578af114502feeafa2d31938"        
BOT_TOKEN = "8313292799:AAHxjrKVfbaMTA89fasbJSva-2u55pzraJ4"      
ADMIN_ID = 7525127704              
MONGODB_URI = "mongodb+srv://MDParvezHossain:MDParvezHossain@cluster0.pma8wsn.mongodb.net/?appName=Cluster0"   
OWNER_USERNAME = "AkashDeveloperBot"   

# ==================== ২. ডাটাবেস সেটআপ ====================
db_client = AsyncIOMotorClient(MONGODB_URI)
db = db_client["file_store_pro_db"]
users_col = db["users"]
files_col = db["stored_files"]
channels_col = db["channels"] 
settings_col = db["settings"]
plans_col = db["plans"]

app = Client("file_store_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==================== ৩. উন্নত টাইম পার্সার ও সাহায্যকারী ফাংশনসমূহ ====================

def parse_duration_advanced(t_str):
    """y=year, mo=month, w=week, d=day, h=hour, m=min, s=sec"""
    t_str = t_str.lower().strip()
    match = re.match(r"(\d+)([a-z]+)", t_str)
    if not match: return None
    value, unit = int(match.group(1)), match.group(2)
    if unit in ['y', 'year']: return timedelta(days=value * 365)
    if unit in ['mo', 'month']: return timedelta(days=value * 30)
    if unit in ['w', 'week']: return timedelta(weeks=value)
    if unit in ['d', 'day']: return timedelta(days=value)
    if unit in ['h', 'hour']: return timedelta(hours=value)
    if unit in ['m', 'min', 'minute']: return timedelta(minutes=value)
    if unit in ['s', 'sec', 'second']: return timedelta(seconds=value)
    return None

async def check_premium(user_id):
    if user_id == ADMIN_ID: return True, "অ্যাডমিন (Owner)"
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_premium"):
        expiry = user.get("expiry_date")
        if expiry and datetime.now() > expiry:
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_premium": False}})
            return False, "ফ্রী মেম্বার (মেয়াদ শেষ)"
        return True, (expiry.strftime('%Y-%m-%d %H:%M') if expiry else "লাইফটাইম")
    return False, "ফ্রী মেম্বার"

async def get_shortlink(url):
    s = await settings_col.find_one({"id": "shortener"})
    if not s: return url
    api_url = f"https://{s['base_url']}/api?api={s['api_key']}&url={url}"
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

# ==================== ৪. ফাইল ডেলিভারি লজিক ====================

async def send_files_logic(client, message, cmd_name, is_extra=False):
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    
    if is_extra:
        # শুধুমাত্র /extfile চ্যানেল থেকে ফাইল নিবে (বাটন ও /getfile এর জন্য)
        extra_data = await settings_col.find_one({"id": "extra_channel"})
        if not extra_data:
            return await (message.reply if hasattr(message, 'reply') else message.message.reply)("❌ গেট ফাইল চ্যানেল সেট করা নেই। অ্যাডমিনকে `/extfile [ID]` ব্যবহার করতে বলুন।")
        chat_id = extra_data["chat_id"]
        db_cmd_key = "extra_files_global"
    else:
        # কাস্টম কমান্ডের চ্যানেল থেকে ফাইল নিবে (যেমন /movies)
        channel_data = await channels_col.find_one({"command": cmd_name})
        if not channel_data:
            return await message.reply(f"❌ '{cmd_name}' কমান্ডটি সক্রিয় নয়।")
        chat_id = channel_data["chat_id"]
        db_cmd_key = cmd_name

    is_prem, _ = await check_premium(user_id)
    user_data = await users_col.find_one({"user_id": user_id}) or {}
    indices = user_data.get("indices", {})
    current_idx = indices.get(db_cmd_key, 0)
    
    # অ্যাডমিন লিমিট চেক
    v_limit_doc = await settings_col.find_one({"id": "video_limit"})
    limit_val = v_limit_doc.get("count", 2) if v_limit_doc else 2

    if is_prem:
        # শুধুমাত্র নির্দিষ্ট চ্যানেলের ভিডিওগুলো নিবে
        files = await files_col.find({"chat_id": chat_id}).sort("msg_id", 1).skip(current_idx).limit(limit_val).to_list(limit_val)
        
        if not files:
            indices[db_cmd_key] = 0
            await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}}, upsert=True)
            return await (message.reply if hasattr(message, 'reply') else message.message.reply)("✅ সব ভিডিও দেখা শেষ! আবার শুরু থেকে দেখতে ট্রাই করুন।")
        
        timer_data = await settings_col.find_one({"id": "auto_delete"})
        protect = (await settings_col.find_one({"id": "forward_setting"}) or {}).get("protect", False)

        for f in files:
            try:
                sent = await client.copy_message(user_id, f["chat_id"], f["msg_id"], protect_content=protect)
                if sent and timer_data:
                    asyncio.create_task(auto_delete_msg(user_id, sent.id, timer_data["seconds"]))
            except: continue
        
        indices[db_cmd_key] = current_idx + len(files)
        await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}}, upsert=True)
    else:
        # ফ্রি ইউজার ভেরিফিকেশন লিঙ্ক
        me = await client.get_me()
        v_type = "extra" if is_extra else cmd_name
        verify_url = f"https://t.me/{me.username}?start=verify_{v_type}"
        short_link = await get_shortlink(verify_url)
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 ভেরিফাই লিংক", url=short_link)]])
        text = "🚫 **ভেরিফিকেশন আবশ্যক!**\n\nফাইল পেতে নিচের লিংকে ক্লিক করে ভেরিফাই করুন। প্রিমিয়াম মেম্বার হলে সরাসরি পাবেন।"
        if hasattr(message, 'reply'): await message.reply(text, reply_markup=btn)
        else: await message.message.reply(text, reply_markup=btn)

# ==================== ৫. অ্যাডমিন কমান্ডসমূহ ====================

@app.on_message(filters.command("extfile") & filters.user(ADMIN_ID))
async def set_ext_channel(client, message):
    if len(message.command) < 2: return await message.reply("উদা: `/extfile -100xxxx` (বাটনের ফাইল এর জন্য)")
    try:
        c_id = int(message.command[1])
        chat = await client.get_chat(c_id)
        await settings_col.update_one({"id": "extra_channel"}, {"$set": {"chat_id": c_id, "title": chat.title}}, upsert=True)
        st = await message.reply(f"🚀 গেট ফাইল চ্যানেল সেট: `{chat.title}`\nইন্ডেক্সিং...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await st.edit(f"✅ সম্পন্ন! গেট ফাইল বাটন এখন এই চ্যানেলের `{count}` ভিডিও দিবে।")
    except Exception as e: await message.reply(f"❌ এরর: {e}")

@app.on_message(filters.command("addcnl") & filters.user(ADMIN_ID))
async def add_cnl_handler(client, message):
    if len(message.command) < 3: return await message.reply("উদা: `/addcnl -100xxx movies` (কমান্ডের জন্য)")
    try:
        c_id, cmd = int(message.command[1]), message.command[2].lower()
        chat = await client.get_chat(c_id)
        await channels_col.update_one({"command": cmd}, {"$set": {"chat_id": c_id, "title": chat.title, "command": cmd}}, upsert=True)
        st = await message.reply(f"✅ `{chat.title}` লিঙ্ক হয়েছে। ইন্ডেক্সিং...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await st.edit(f"✅ সম্পন্ন! `/{cmd}` এখন এই চ্যানেলের `{count}` ফাইল দিবে।")
    except Exception as e: await message.reply(f"❌ এরর: {e}")

@app.on_message(filters.command("add_premium") & filters.user(ADMIN_ID))
async def add_prem_cmd(client, message):
    try:
        u_id, dur_str = int(message.command[1]), message.command[2]
        dur = parse_duration_advanced(dur_str)
        if not dur: return await message.reply("❌ ভুল ফরম্যাট! (y, mo, w, d, h, m)")
        exp = datetime.now() + dur
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": True, "expiry_date": exp}}, upsert=True)
        await message.reply(f"✅ ইউজার `{u_id}` প্রিমিয়াম হয়েছে।\n📅 মেয়াদ: `{exp.strftime('%Y-%m-%d %H:%M')}`")
    except: await message.reply("উদা: `/add_premium ID 1y` (বা 1mo, 7d)")

@app.on_message(filters.command("remove_premium") & filters.user(ADMIN_ID))
async def remove_prem_cmd(client, message):
    try:
        u_id = int(message.command[1])
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": False}, "$unset": {"expiry_date": ""}})
        await message.reply(f"✅ ইউজার `{u_id}` রিমুভ হয়েছে।")
    except: await message.reply("📝 উদা: `/remove_premium 12345`")

@app.on_message(filters.command("premium_list") & filters.user(ADMIN_ID))
async def prem_list_cmd(client, message):
    users = await users_col.find({"is_premium": True}).to_list(None)
    if not users: return await message.reply("ℹ️ কোনো প্রিমিয়াম মেম্বার নেই।")
    txt = "💎 **প্রিমিয়াম মেম্বার লিস্ট:**\n\n"
    for u in users:
        exp = u.get('expiry_date')
        txt += f"👤 `{u['user_id']}` | 📅 `{exp.strftime('%Y-%m-%d %H:%M') if exp else 'LifeTime'}`\n"
    await message.reply(txt)

@app.on_message(filters.command("set_timer") & filters.user(ADMIN_ID))
async def set_timer_cmd(client, message):
    try:
        sec = int(message.command[1])
        await settings_col.update_one({"id": "auto_delete"}, {"$set": {"seconds": sec}}, upsert=True)
        await message.reply(f"✅ অটো-ডিলিট `{sec}` সেকেন্ড সেট হয়েছে।")
    except: await message.reply("উদা: `/set_timer 600` (১০ মিনিট)")

@app.on_message(filters.command("set_limit") & filters.user(ADMIN_ID))
async def set_limit_cmd(client, message):
    try:
        lim = int(message.command[1])
        await settings_col.update_one({"id": "video_limit"}, {"$set": {"count": lim}}, upsert=True)
        await message.reply(f"✅ ভিডিও লিমিট `{lim}` সেট হয়েছে।")
    except: await message.reply("উদা: `/set_limit 5`")

@app.on_message(filters.command("set_shortener") & filters.user(ADMIN_ID))
async def set_short_cmd(client, message):
    try:
        url, key = message.command[1], message.command[2]
        await settings_col.update_one({"id": "shortener"}, {"$set": {"base_url": url, "api_key": key}}, upsert=True)
        await message.reply("✅ শর্টনার সেট হয়েছে।")
    except: await message.reply("উদা: `/set_shortener domain.com key`")

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_cmd(client, message):
    u = await users_col.count_documents({})
    p = await users_col.count_documents({"is_premium": True})
    f = await files_col.count_documents({})
    await message.reply(f"📊 **স্ট্যাটাস:**\n\n👤 ইউজার: `{u}`\n💎 প্রিমিয়াম: `{p}`\n📁 ফাইল: `{f}`")

@app.on_message(filters.command("add_plan") & filters.user(ADMIN_ID))
async def add_plan_cmd(client, message):
    try:
        name, price = message.command[1], message.command[2]
        await plans_col.update_one({"name": name}, {"$set": {"price": price}}, upsert=True)
        await message.reply(f"✅ প্ল্যান `{name}` সেট হয়েছে।")
    except: await message.reply("উদা: `/add_plan 1Month 100Tk`")

# ==================== ৬. ইউজার হ্যান্ডলার ও কমান্ডস ====================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    user_id = message.from_user.id
    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        v_type = message.command[1].replace("verify_", "")
        if v_type == "extra": return await send_files_logic(client, message, "", is_extra=True)
        else: return await send_files_logic(client, message, v_type)

    is_prem, status = await check_premium(user_id)
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Get Files", callback_data="get_extra_files")],
        [InlineKeyboardButton("💎 Plans", callback_data="show_plans"), InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]
    ])
    await message.reply_text(f"👋 আসসালামু আলাইকুম!\n🆔 আইডি: `{user_id}`\n💎 মেম্বারশিপ: `{status}`", reply_markup=btn)

@app.on_callback_query()
async def cb_handler(client, query: CallbackQuery):
    if query.data == "get_extra_files":
        await send_files_logic(client, query, "", is_extra=True)
    elif query.data == "show_plans":
        plans = await plans_col.find().to_list(None)
        txt = "💎 **প্রিমিয়াম প্ল্যানসমূহ:**\n\n"
        if not plans: txt += "🔹 ৩০ দিন - ১০০ টাকা (ডিফল্ট)\n"
        else:
            for p in plans: txt += f"🔹 {p['name']} - {p['price']}\n"
        txt += f"\nযোগাযোগ: @{OWNER_USERNAME}"
        await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_home")]]))
    elif query.data == "back_home":
        _, st = await check_premium(query.from_user.id)
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
    sys = ["start", "stats", "premium_list", "remove_premium", "add_premium", "addcnl", "extfile", "getfile", "set_timer", "set_limit", "set_shortener", "add_plan"]
    if cmd in sys: return
    exists = await channels_col.find_one({"command": cmd})
    if exists: await send_files_logic(client, message, cmd)

@app.on_message((filters.video | filters.document | filters.audio) & ~filters.private)
async def auto_save_handler(client, message):
    is_saved = await channels_col.find_one({"chat_id": message.chat.id})
    is_extra = await settings_col.find_one({"id": "extra_channel", "chat_id": message.chat.id})
    if is_saved or is_extra:
        await files_col.update_one({"chat_id": message.chat.id, "msg_id": message.id}, {"$set": {"chat_id": message.chat.id, "msg_id": message.id}}, upsert=True)

# ==================== ৭. রান ও ওয়েব সার্ভার ====================

async def main():
    server = web.Application()
    server.router.add_get("/", lambda r: web.Response(text="Bot is Alive!"))
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await app.start()
    print(">>> বট সফলভাবে চালু হয়েছে! <<<")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
