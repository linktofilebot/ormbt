import os
from aiohttp import web
import asyncio
import random
import string
import aiohttp
import re
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta

# ==================== ১. কনফিগারেশন ====================
API_ID = 29904834                 
API_HASH = "8b4fd9ef578af114502feeafa2d31938"        
BOT_TOKEN = "8313292799:AAHxjrKVfbaMTA89fasbJSva-2u55pzraJ4"      
ADMIN_ID = 7525127704              
MONGODB_URI = "mongodb+srv://MDParvezHossain:MDParvezHossain@cluster0.pma8wsn.mongodb.net/?appName=Cluster0"   
OWNER_USERNAME = "AkashDeveloperBot"   

DEFAULT_LOG_CHANNEL = -1003513942313

# ==================== ২. ডাটাবেস সেটআপ ====================
db_client = AsyncIOMotorClient(MONGODB_URI)
db = db_client["file_store_pro_db"]
users_col = db["users"]
files_col = db["stored_files"]
plans_col = db["plans"]
redeem_col = db["redeem_codes"]
settings_col = db["settings"]
channels_col = db["channels"] 

app = Client("file_store_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==================== ৩. সাহায্যকারী ফাংশনসমূহ ====================

async def get_log_channel():
    data = await settings_col.find_one({"id": "log_channel_id"})
    return data["value"] if data else DEFAULT_LOG_CHANNEL

async def get_video_limit():
    data = await settings_col.find_one({"id": "video_limit"})
    return data.get("count", 1) if data else 1

async def check_premium(user_id):
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_premium"):
        expiry = user.get("expiry_date")
        if expiry and datetime.now() > expiry:
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_premium": False}})
            return False, "Free (Expired)"
        return True, expiry.strftime('%Y-%m-%d %H:%M')
    return False, "Regular Member"

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

def parse_duration(t_str):
    try:
        num = int(''.join(filter(str.isdigit, t_str)))
        if "min" in t_str.lower(): return timedelta(minutes=num)
        if "hour" in t_str.lower(): return timedelta(hours=num)
        if "day" in t_str.lower(): return timedelta(days=num)
    except: return None
    return None

async def auto_delete_msg(client, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try: await client.delete_messages(chat_id, message_id)
    except: pass

# ==================== ৪. ফাইল ডেলিভারি সিস্টেম ====================

async def send_files_logic(client, message, cmd_name, is_extra=False):
    user_id = message.from_user.id
    
    # চ্যানেল ডাটা নির্ধারণ
    if is_extra:
        extra_data = await settings_col.find_one({"id": "extra_channel"})
        if not extra_data:
            return await message.reply("❌ এক্সট্রা ফাইল চ্যানেল সেট করা নেই। অ্যাডমিনকে `/extfile [ID]` ব্যবহার করতে বলুন।")
        chat_id = extra_data["chat_id"]
        db_cmd_key = "extra_files_global"
    else:
        channel_data = await channels_col.find_one({"command": cmd_name})
        if not channel_data:
            return await message.reply(f"❌ '{cmd_name}' কমান্ডটি বর্তমানে সক্রিয় নয়।")
        chat_id = channel_data["chat_id"]
        db_cmd_key = cmd_name

    is_prem, _ = await check_premium(user_id)
    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "is_premium": False, "indices": {}}}, upsert=True)
        user_data = {"indices": {}}
    
    indices = user_data.get("indices", {})
    current_idx = indices.get(db_cmd_key, 0)
    limit_val = await get_video_limit()

    if is_prem:
        # ডাটাবেস থেকে ফাইল সংগ্রহ (নির্দিষ্ট চ্যানেলের জন্য)
        files = await files_col.find({"chat_id": chat_id}).sort("msg_id", 1).skip(current_idx).limit(limit_val).to_list(limit_val)
        
        if not files:
            indices[db_cmd_key] = 0
            await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}})
            return await message.reply(f"✅ এই ক্যাটাগরির সব ফাইল শেষ! আবার শুরু থেকে দেখানো হবে।")
        
        timer_data = await settings_col.find_one({"id": "auto_delete"})
        protect = (await settings_col.find_one({"id": "forward_setting"}) or {}).get("protect", False)

        for f in files:
            try:
                sent = await client.copy_message(user_id, f["chat_id"], f["msg_id"], protect_content=protect)
                if sent and timer_data:
                    asyncio.create_task(auto_delete_msg(client, user_id, sent.id, timer_data["seconds"]))
            except: continue
        
        indices[db_cmd_key] = current_idx + len(files)
        await users_col.update_one({"user_id": user_id}, {"$set": {"indices": indices}})
    else:
        # ভেরিফিকেশন সিস্টেম
        me = await client.get_me()
        v_type = "extra" if is_extra else cmd_name
        verify_url = f"https://t.me/{me.username}?start=verify_{v_type}"
        short_link = await get_shortlink(verify_url)
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 ভেরিফাই লিংক", url=short_link)]])
        await message.reply(f"🚫 **ভেরিফিকেশন আবশ্যক!**\n\nফাইল পেতে নিচে ক্লিক করে ভেরিফাই করুন। প্রিমিয়াম মেম্বার হলে সরাসরি পাবেন।", reply_markup=btn)

# ==================== ৫. অ্যাডমিন কমান্ডসমূহ ====================

@app.on_message(filters.command("addcnl") & filters.user(ADMIN_ID))
async def add_channel_cmd(client, message):
    if len(message.command) < 3: return await message.reply("📝 উদা: `/addcnl -100xxx movies`")
    try:
        c_id, cmd = int(message.command[1]), message.command[2].lower()
        chat = await client.get_chat(c_id)
        await channels_col.update_one({"command": cmd}, {"$set": {"chat_id": c_id, "title": chat.title, "command": cmd}}, upsert=True)
        status = await message.reply(f"✅ চ্যানেল `{chat.title}` কমান্ড `/{cmd}` এ সেট হয়েছে। ইন্ডেক্সিং হচ্ছে...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await status.edit(f"✅ ইন্ডেক্সিং সম্পন্ন! ফাইল পাওয়া গেছে: `{count}`")
    except Exception as e: await message.reply(f"❌ এরর: {e}")

@app.on_message(filters.command("extfile") & filters.user(ADMIN_ID))
async def set_extra_file_channel(client, message):
    if len(message.command) < 2: return await message.reply("📝 উদা: `/extfile -100xxxx` (এটি গেট ফাইল বাটনের জন্য)")
    try:
        c_id = int(message.command[1])
        chat = await client.get_chat(c_id)
        await settings_col.update_one({"id": "extra_channel"}, {"$set": {"chat_id": c_id, "title": chat.title}}, upsert=True)
        status = await message.reply(f"🚀 এক্সট্রা ফাইল চ্যানেল সেট: `{chat.title}`\nইন্ডেক্সিং শুরু হচ্ছে...")
        count = 0
        async for m in client.get_chat_history(c_id):
            if m.video or m.document or m.audio:
                await files_col.update_one({"chat_id": c_id, "msg_id": m.id}, {"$set": {"chat_id": c_id, "msg_id": m.id}}, upsert=True)
                count += 1
        await status.edit(f"✅ এক্সট্রা চ্যানেল ইন্ডেক্স সম্পন্ন! ফাইল: `{count}`")
    except Exception as e: await message.reply(f"❌ এরর: {e}")

@app.on_message(filters.command("delcnl") & filters.user(ADMIN_ID))
async def del_channel_cmd(client, message):
    if len(message.command) < 2: return
    cmd = message.command[1].lower()
    chnl = await channels_col.find_one({"command": cmd})
    if chnl:
        await files_col.delete_many({"chat_id": chnl["chat_id"]})
        await channels_col.delete_one({"command": cmd})
        await message.reply(f"✅ `/{cmd}` কমান্ডের সব ডাটা রিমুভ হয়েছে।")

@app.on_message(filters.command("channels") & filters.user(ADMIN_ID))
async def list_channels(client, message):
    all_c = await channels_col.find().to_list(100)
    extra = await settings_col.find_one({"id": "extra_channel"})
    txt = "📋 **সক্রিয় কমান্ডসমূহ:**\n\n"
    for c in all_c: txt += f"🔹 /{c['command']} ➔ `{c['title']}`\n"
    if extra: txt += f"\n📂 **এক্সট্রা ফাইল (Get File):** `{extra['title']}`"
    await message.reply(txt)

# ==================== ৬. ইউজার হ্যান্ডলার ও কমান্ডস ====================

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        v_type = message.command[1].replace("verify_", "")
        if v_type == "extra": return await send_files_logic(client, message, "", is_extra=True)
        else: return await send_files_logic(client, message, v_type)

    is_prem, status = await check_premium(user_id)
    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Get Files", callback_data="get_extra_files")],
        [InlineKeyboardButton("💎 Plans", callback_data="show_plans_logic"), InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]
    ])
    await message.reply_text(f"👋 আসসালামু আলাইকুম!\n🆔 আইডি: `{user_id}`\n💎 মেম্বারশিপ: {status}\n\nবাটনে ক্লিক করুন অথবা কাস্টম কমান্ড দিন।", reply_markup=btn)

@app.on_callback_query(filters.regex("get_extra_files"))
async def cb_extra_files(client, query):
    await send_files_logic(client, query, "", is_extra=True)
    await query.answer()

@app.on_message(filters.command("getfile"))
async def get_file_cmd(client, message):
    await send_files_logic(client, message, "", is_extra=True)

@app.on_message(filters.text & filters.private)
async def custom_cmd_detector(client, message):
    if not message.text.startswith("/"): return
    cmd_name = message.text.split()[0].replace("/", "").lower()
    # সিস্টেম কমান্ডগুলো বাদ দিয়ে চেক করা
    sys_cmds = ["start", "getfile", "redeem", "extfile", "addcnl", "delcnl", "channels", "stats", "set_log", "add_redeem", "add_premium", "addtime", "set_forward"]
    if cmd_name in sys_cmds: return
    exists = await channels_col.find_one({"command": cmd_name})
    if exists: await send_files_logic(client, message, cmd_name)

# ==================== ৭. বাকি সব সেটিংস (Admin/Common) ====================

@app.on_message(filters.command("set_log") & filters.user(ADMIN_ID))
async def set_log_admin(client, message):
    try:
        l_id = int(message.command[1])
        await settings_col.update_one({"id": "log_channel_id"}, {"$set": {"value": l_id}}, upsert=True)
        await message.reply(f"✅ লগ চ্যানেল সেট হয়েছে।")
    except: pass

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_admin(client, message):
    u = await users_col.count_documents({})
    f = await files_col.count_documents({})
    await message.reply(f"📊 **পরিসংখ্যান:**\n\n👥 মোট ইউজার: `{u}`\n📁 মোট ফাইল: `{f}`")

@app.on_message(filters.command("add_redeem") & filters.user(ADMIN_ID))
async def add_red_admin(client, message):
    try:
        dur, count = message.command[1], int(message.command[2])
        codes = []
        for _ in range(count):
            c = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            await redeem_col.insert_one({"code": c, "duration": dur, "is_used": False})
            codes.append(f"`{c}`")
        await message.reply("✅ জেনারেট হওয়া কোডসমূহ:\n" + "\n".join(codes))
    except: await message.reply("উদা: `/add_redeem 1month 5`")

@app.on_message(filters.command("redeem"))
async def redeem_user(client, message):
    if len(message.command) < 2: return
    code = message.command[1]
    data = await redeem_col.find_one({"code": code, "is_used": False})
    if not data: return await message.reply("❌ ভুল বা পুরাতন কোড!")
    dur = parse_duration(data["duration"])
    expiry = datetime.now() + (dur if dur else timedelta(days=30))
    await users_col.update_one({"user_id": message.from_user.id}, {"$set": {"is_premium": True, "expiry_date": expiry}}, upsert=True)
    await redeem_col.update_one({"code": code}, {"$set": {"is_used": True}})
    await message.reply(f"🎉 প্রিমিয়াম সফল! মেয়াদ: {expiry.strftime('%Y-%m-%d')}")

@app.on_message(filters.command("addtime") & filters.user(ADMIN_ID))
async def set_timer(client, message):
    try:
        t_str = message.command[1]
        dur = parse_duration(t_str)
        await settings_col.update_one({"id": "auto_delete"}, {"$set": {"seconds": dur.total_seconds()}}, upsert=True)
        await message.reply(f"✅ অটো ডিলিট সময়: `{t_str}`")
    except: pass

@app.on_message(filters.command("set_forward") & filters.user(ADMIN_ID))
async def set_fwd(client, message):
    status = message.command[1].lower() == "on"
    await settings_col.update_one({"id": "forward_setting"}, {"$set": {"protect": status}}, upsert=True)
    await message.reply(f"✅ প্রোটেকশন {'চালু' if status else 'বন্ধ'}।")

@app.on_message(filters.chat & (filters.video | filters.document | filters.audio))
async def auto_save_handler(client, message):
    # চেক করে দেখা চ্যানেলটি কি আমাদের কোনো কমান্ড বা এক্সট্রা চ্যানেলের সাথে যুক্ত
    is_saved = await channels_col.find_one({"chat_id": message.chat.id})
    is_extra = await settings_col.find_one({"id": "extra_channel", "chat_id": message.chat.id})
    if is_saved or is_extra:
        await files_col.update_one({"chat_id": message.chat.id, "msg_id": message.id}, {"$set": {"chat_id": message.chat.id, "msg_id": message.id}}, upsert=True)

# ==================== ৮. ওয়েব সার্ভার ও রান ====================

async def uptime_handler(request): return web.Response(text="Bot Alive 🚀")

async def main():
    server = web.Application()
    server.router.add_get("/", uptime_handler)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    
    await app.start()
    print("বট সফলভাবে চালু হয়েছে! কাস্টম কমান্ড ও এক্সট্রা চ্যানেল সিস্টেম সক্রিয়।")
    await idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
