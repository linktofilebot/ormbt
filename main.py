import os
from aiohttp import web
import asyncio
import random
import string
import aiohttp
import re  # নতুন যোগ করা হয়েছে লিংকের জন্য
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timedelta

# ==================== ১. কনফিগারেশন ====================
API_ID = 29904834                 
API_HASH = "8b4fd9ef578af114502feeafa2d31938"        
BOT_TOKEN = "8313292799:AAHxjrKVfbaMTA89fasbJSva-2u55pzraJ4"      
ADMIN_ID = 7525127704              
LOG_CHANNEL = -1003513942313       
FILE_CHANNEL = -1003606044547      
MONGODB_URI = "mongodb+srv://MDParvezHossain:MDParvezHossain@cluster0.pma8wsn.mongodb.net/?appName=Cluster0"   
OWNER_USERNAME = "AkashDeveloperBot"   

# ==================== ২. ডাটাবেস ও ক্লায়েন্ট সেটআপ ====================
db_client = AsyncIOMotorClient(MONGODB_URI)
db = db_client["file_store_pro_db"]
users_col = db["users"]
files_col = db["stored_files"]
plans_col = db["plans"]
redeem_col = db["redeem_codes"]
settings_col = db["settings"]
custom_cmds_col = db["custom_commands"] # <--- নতুন যোগ করা হয়েছে (নতুন কমান্ডের জন্য)

app = Client("file_store_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==================== ৩. সাহায্যকারী ফাংশনসমূহ (Helpers) ====================

# ভিডিও লিমিট ডাটাবেস থেকে নেওয়ার ফাংশন (নতুন যুক্ত)
async def get_video_limit():
    data = await settings_col.find_one({"id": "video_limit"})
    return data.get("count", 1) if data else 1

def get_readable_time(expiry_date):
    delta = expiry_date - datetime.now()
    seconds = int(delta.total_seconds())
    if seconds <= 0: return "Expired"
    months, seconds = divmod(seconds, 30 * 24 * 3600)
    weeks, seconds = divmod(seconds, 7 * 24 * 3600)
    days, seconds = divmod(seconds, 24 * 3600)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if months: parts.append(f"{months} মাস")
    if weeks: parts.append(f"{weeks} সপ্তাহ")
    if days: parts.append(f"{days} দিন")
    if hours: parts.append(f"{hours} ঘণ্টা")
    if minutes: parts.append(f"{minutes} মিনিট")
    return ", ".join(parts)

async def send_premium_report(client, user_id, expiry_date, method="Redeem Code"):
    try:
        user = await client.get_users(user_id)
        readable_time = get_readable_time(expiry_date)
        username = f"@{user.username}" if user.username else "None"
        report_text = (
            f"🚀 **প্রিমিয়াম মেম্বারশিপ আপডেট**\n\n"
            f"👤 **নাম:** {user.first_name}\n"
            f"🆔 **আইডি:** `{user.id}`\n"
            f"🔗 **ইউজারনেম:** {username}\n"
            f"⏳ **মেয়াদ:** {readable_time}\n"
            f"📅 **শেষ হবে:** {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🛠 **পদ্ধতি:** {method}"
        )
        try:
            photo_id = None
            async for photo in client.get_chat_photos(user_id, limit=1): photo_id = photo.file_id
            if photo_id: await client.send_photo(LOG_CHANNEL, photo_id, caption=report_text)
            else: await client.send_message(LOG_CHANNEL, report_text)
        except: await client.send_message(LOG_CHANNEL, report_text)
        await client.send_message(user_id, f"🎉 **অভিনন্দন! আপনার প্রিমিয়াম সফলভাবে একটিভ হয়েছে।**\n\n{report_text}")
    except Exception as e: print(f"Report Error: {e}")

async def check_premium(user_id):
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("is_premium"):
        expiry = user.get("expiry_date")
        if expiry and datetime.now() > expiry:
            await users_col.update_one({"user_id": user_id}, {"$set": {"is_premium": False}})
            return False, "Free User (Expired)"
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
                return data.get("shortenedUrl") or data.get("shortlink") or data.get("url") or url
    except: return url

def parse_duration(t_str):
    try:
        num = int(''.join(filter(str.isdigit, t_str)))
        if "min" in t_str: return timedelta(minutes=num)
        if "hour" in t_str: return timedelta(hours=num)
        if "day" in t_str: return timedelta(days=num)
        if "month" in t_str: return timedelta(days=num * 30)
    except: return None

async def is_protect_on():
    data = await settings_col.find_one({"id": "forward_setting"})
    return data.get("protect", False) if data else False

async def auto_delete_msg(client, chat_id, message_id, seconds):
    await asyncio.sleep(seconds)
    try:
        await client.delete_messages(chat_id, message_id)
    except: pass

# লিংক থেকে চ্যানেল আইডি এবং লাস্ট মেসেজ আইডি বের করার ফাংশন
def parse_tg_link(link):
    regex = r"(?:https?://)?t\.me/(?:c/)?([^/]+)/(\d+)"
    match = re.search(regex, link)
    if match:
        chat_val = match.group(1)
        last_msg_id = int(match.group(2))
        if chat_val.isdigit():
            chat_id = int("-100" + chat_val)
        else:
            chat_id = f"@{chat_val}" if not chat_val.startswith("@") else chat_val
        return chat_id, last_msg_id
    return None, None

# ==================== ৪. ইউজার কমান্ড হ্যান্ডলার ====================

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    log_txt = (f"👤 **নতুন ইউজার অ্যাক্টিভিটি**\n\n🆔 আইডি: `{user_id}`\n🎭 নাম: {message.from_user.first_name}\n🔗 ইউজারনেম: @{message.from_user.username if message.from_user.username else 'None'}")
    await client.send_message(LOG_CHANNEL, log_txt)

    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "is_premium": False, "p_index": 0, "f_index": 0}}, upsert=True)

    # ভেরিফিকেশন লিংক দিয়ে আসলে (Deep Linking)
    if len(message.command) > 1 and message.command[1].startswith("verify"):
        # ভেরিফিকেশন হ্যান্ডলিং (মেইন ফাইল চ্যানেল)
        is_prem, _ = await check_premium(user_id)
        if is_prem: return await message.reply("আপনি ইতিমধ্যে প্রিমিয়াম মেম্বার। ফাইল পেতে সরাসরি গেট ফাইল বাটনে ক্লিক করুন।")
        
        user_data = await users_col.find_one({"user_id": user_id})
        f_idx = user_data.get("f_index", 0)
        
        limit_val = await get_video_limit()
        files = await files_col.find().sort("_id", 1).skip(f_idx).limit(limit_val).to_list(limit_val)
        
        if not files:
            await users_col.update_one({"user_id": user_id}, {"$set": {"f_index": 0}}) 
            return await message.reply("সব ভিডিও দেখা শেষ! গেট ফাইলে ক্লিক করে আবার শুরু থেকে দেখুন।")
            
        await message.reply(f"✅ ভেরিফিকেশন সফল! {len(files)}টি ভিডিও পাঠানো হচ্ছে...")
        p_on = await is_protect_on()
        timer_data = await settings_col.find_one({"id": "auto_delete"})
        
        for f in files:
            try:
                sent_msg = await client.copy_message(user_id, FILE_CHANNEL, f["msg_id"], protect_content=p_on)
                if sent_msg and timer_data:
                    asyncio.create_task(auto_delete_msg(client, user_id, sent_msg.id, timer_data["seconds"]))
            except: pass
        
        await users_col.update_one({"user_id": user_id}, {"$inc": {"f_index": len(files)}})
        return

    is_prem, status_txt = await check_premium(user_id)
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("📂 Get Files", callback_data="get_file_logic")],[InlineKeyboardButton("💎 View Plans", callback_data="show_plans_logic"), InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]])
    
    start_text = (f"👋 আসসালামু আলাইকুম {message.from_user.first_name}!\n\n🆔 **আপনার আইডি:** `{user_id}`\n🎭 **আপনার নাম:** {message.from_user.first_name}\n💎 **মেম্বারশিপ:** {status_txt}\n\nফাইল পেতে নিচের বাটনে ক্লিক করুন।")
    try:
        async for photo in client.get_chat_photos(user_id, limit=1):
            await message.reply_photo(photo=photo.file_id, caption=start_text, reply_markup=btn)
            return
    except: pass
    await message.reply_text(start_text, reply_markup=btn)

@app.on_callback_query(filters.regex("get_file_logic"))
@app.on_message(filters.command("getfile"))
async def getfile_handler(client, update):
    is_cb = isinstance(update, CallbackQuery)
    user_id = update.from_user.id
    
    user_data = await users_col.find_one({"user_id": user_id})
    if not user_data:
        await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "is_premium": False, "p_index": 0, "f_index": 0}}, upsert=True)
        user_data = await users_col.find_one({"user_id": user_id})

    is_prem, _ = await check_premium(user_id)

    if is_prem:
        p_idx = user_data.get("p_index", 0)
        limit_val = await get_video_limit()
        files = await files_col.find().sort("_id", 1).skip(p_idx).limit(limit_val).to_list(limit_val)
        
        if not files:
            await users_col.update_one({"user_id": user_id}, {"$set": {"p_index": 0}}) 
            msg = "সব ফাইল শেষ! আবার প্রথম থেকে শুরু হবে।"
            if is_cb: await update.message.reply(msg)
            else: await update.reply(msg)
            return
        
        if is_cb: await update.answer(f"{len(files)}টি ভিডিও পাঠানো হচ্ছে...", show_alert=False)
        p_on = await is_protect_on()
        timer_data = await settings_col.find_one({"id": "auto_delete"})
        
        for f in files:
            try:
                sent_msg = await client.copy_message(user_id, FILE_CHANNEL, f["msg_id"], protect_content=p_on)
                if sent_msg and timer_data:
                    asyncio.create_task(auto_delete_msg(client, user_id, sent_msg.id, timer_data["seconds"]))
            except: pass
        
        await users_col.update_one({"user_id": user_id}, {"$inc": {"p_index": len(files)}})

    else:
        me = await client.get_me()
        verify_url = f"https://t.me/{me.username}?start=verify_{user_id}"
        short_link = await get_shortlink(verify_url)
        txt = "🚫 **ভেরিফিকেশন বাধ্যতামূলক!**\n\nফাইল পেতে নিচের লিংকে ক্লিক করে ভেরিফাই করুন। প্রিমিয়াম মেম্বার হলে সরাসরি ভিডিও পাবেন।"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 ভেরিফাই লিংক", url=short_link)]])
        if is_cb: await update.message.reply(txt, reply_markup=btn); await update.answer()
        else: await update.reply(txt, reply_markup=btn)

@app.on_message(filters.command("skipfile"))
async def skip_file_handler(client, message):
    user_id = message.from_user.id
    is_prem, _ = await check_premium(user_id)
    index_field = "p_index" if is_prem else "f_index"

    if len(message.command) < 2:
        return await message.reply("📝 **ব্যবহার:** `/skipfile সংখ্যা` অথবা `/skipfile next`")

    input_val = message.command[1].lower()
    if input_val == "next":
        limit_val = await get_video_limit()
        await users_col.update_one({"user_id": user_id}, {"$inc": {index_field: limit_val}})
        return await message.reply(f"⏭ {limit_val}টি ফাইল স্কিপ করা হয়েছে।")

    try:
        target_index = int(input_val)
        await users_col.update_one({"user_id": user_id}, {"$set": {index_field: target_index}})
        await message.reply(f"✅ ইনডেক্স {target_index} এ সেট করা হয়েছে।")
    except:
        await message.reply("❌ ভুল ফরম্যাট! সংখ্যা ব্যবহার করুন।")

@app.on_message(filters.command("stats"))
async def stats_handler(client, message):
    total_users = await users_col.count_documents({})
    total_files = await files_col.count_documents({})
    premium_users = await users_col.count_documents({"is_premium": True})
    regular_users = total_users - premium_users
    
    stats_txt = (
        "📊 **বট লাইভ পরিসংখ্যান**\n\n"
        f"📁 **মোট ভিডিও ফাইল:** `{total_files}` টি\n"
        f"👥 **মোট ইউজার:** `{total_users}` জন\n"
        f"💎 **প্রিমিয়াম মেম্বার:** `{premium_users}` জন\n"
        f"👤 **সাধারণ মেম্বার:** `{regular_users}` জন\n\n"
        f"📢 **যুক্ত চ্যানেল সংখ্যা:** `২টি` (File & Log)\n"
        "⚡ **বট স্ট্যাটাস:** সচল (Active)"
    )
    
    btn = InlineKeyboardMarkup([[
        InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}"),
        InlineKeyboardButton("Close ❌", callback_data="close_stats")
    ]])
    
    await message.reply_text(stats_txt, reply_markup=btn)

@app.on_callback_query(filters.regex("close_stats"))
async def close_stats(client, query):
    await query.message.delete()

@app.on_callback_query(filters.regex("show_plans_logic"))
@app.on_message(filters.command(["plan", "buy_plan"]))
async def plan_commands(client, update):
    is_cb = isinstance(update, CallbackQuery)
    plans = await plans_col.find().to_list(100)
    if not plans: 
        msg = "বর্তমানে কোনো প্ল্যান সেট করা নেই।"
        if is_cb: return await update.answer(msg, show_alert=True)
        return await update.reply(msg)

    txt = "💎 **আমাদের প্রিমিয়াম প্ল্যানসমূহ:**\n\n"
    for p in plans: txt += f"🔹 {p['days']} দিন - {p['price']} টাকা\n"
    txt += f"\n💳 মেম্বারশিপ কিনতে যোগাযোগ করুন: @{OWNER_USERNAME}"
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")],[InlineKeyboardButton("🔙 ফিরে যান", callback_data="back_home")]])
    if is_cb: await update.message.edit_text(txt, reply_markup=btn)
    else: await update.reply_text(txt, reply_markup=btn)

@app.on_callback_query(filters.regex("back_home"))
async def back_home(client, query):
    user_id = query.from_user.id
    is_prem, status_txt = await check_premium(user_id)
    btn = InlineKeyboardMarkup([[InlineKeyboardButton("📂 Get Files", callback_data="get_file_logic")],[InlineKeyboardButton("💎 View Plans", callback_data="show_plans_logic"), InlineKeyboardButton("Owner 👑", url=f"https://t.me/{OWNER_USERNAME}")]])
    await query.message.edit_text(f"👋 আসসালামু আলাইকুম!\n🆔 আপনার আইডি: `{user_id}`\n💎 মেম্বারশিপ: {status_txt}", reply_markup=btn)

@app.on_message(filters.command("redeem"))
async def redeem_cmd(client, message):
    if len(message.command) < 2: return await message.reply("কোড দিন! উদা: `/redeem WK7jd0TjTe`")
    code_str = message.command[1].strip()
    data = await redeem_col.find_one({"code": code_str, "is_used": False})
    if not data: return await message.reply("❌ ভুল বা পুরাতন কোড!")
    expiry = datetime.now() + parse_duration(data["duration"])
    await users_col.update_one({"user_id": message.from_user.id}, {"$set": {"is_premium": True, "expiry_date": expiry, "p_index": 0}}, upsert=True)
    await redeem_col.update_one({"code": code_str}, {"$set": {"is_used": True}})
    await send_premium_report(client, message.from_user.id, expiry, method=f"Redeem Code ({data['duration']})")

# ==================== ৫. অ্যাডমিন কমান্ডসমূহ ====================

# --- নতুন আপডেট: /addcmd কমান্ড ---
@app.on_message(filters.command("addcmd") & filters.user(ADMIN_ID))
async def add_custom_command_handler(client, message):
    if len(message.command) < 3:
        return await message.reply("📝 **ব্যবহার:** `/addcmd কমান্ড_নাম চ্যানেল_আইডি`\n\nউদাহরণ: `/addcmd adult -10012345678` (কমান্ডটি /adult হিসেবে কাজ করবে)")
    
    cmd_name = message.command[1].lower()
    try:
        target_chat_id = int(message.command[2])
        # ডাটাবেসে কমান্ড ও চ্যানেল সেভ
        await custom_cmds_col.update_one(
            {"cmd": cmd_name}, 
            {"$set": {"chat_id": target_chat_id, "created_at": datetime.now()}}, 
            upsert=True
        )
        await message.reply(f"✅ সফল! নতুন কমান্ড `/{cmd_name}` সেট করা হয়েছে যা `{target_chat_id}` চ্যানেল থেকে ফাইল দিবে।")
    except:
        await message.reply("❌ ভুল চ্যানেল আইডি! শুধুমাত্র সংখ্যা দিন (যেমন: -100xxxx)")

# --- ডাইনামিক কমান্ড হ্যান্ডলার (নতুন চ্যানেল থেকে ফাইল পাঠাতে) ---
@app.on_message(filters.text & filters.private)
async def handle_dynamic_commands(client, message):
    if not message.text.startswith("/"): return
    
    cmd_input = message.text.split()[0][1:].lower()
    
    # মেইন কমান্ডগুলো এড়িয়ে যাওয়া
    if cmd_input in ["start", "getfile", "skipfile", "stats", "plan", "redeem", "index", "batch_index", "addcmd"]:
        return

    # ডাটাবেসে এই কমান্ডটি আছে কিনা চেক করা
    cmd_data = await custom_cmds_col.find_one({"cmd": cmd_input})
    if not cmd_data: return

    user_id = message.from_user.id
    target_channel = cmd_data["chat_id"]
    
    # ইউজারের এই কমান্ডের জন্য আলাদা ইনডেক্স মেইনটেইন করা
    user_data = await users_col.find_one({"user_id": user_id})
    # 'custom_indexes' ডিকশনারিতে কমান্ড অনুযায়ী ইনডেক্স থাকবে
    custom_indexes = user_data.get("custom_indexes", {})
    current_idx = custom_indexes.get(cmd_input, 0)
    
    is_prem, _ = await check_premium(user_id)
    limit_val = await get_video_limit()

    if is_prem:
        # প্রিমিয়াম ইউজার সরাসরি পাবে (মেইন ফাইল চ্যানেলের সাথে কোন সম্পর্ক নেই)
        # এখানে নির্দিষ্ট চ্যানেল থেকে মেসেজ পাঠানোর জন্য history থেকে নেওয়া হচ্ছে অথবা আলাদা logic
        try:
            sent_count = 0
            p_on = await is_protect_on()
            timer_data = await settings_col.find_one({"id": "auto_delete"})
            
            # নির্দিষ্ট চ্যানেলের মেসেজ হিস্টোরি থেকে ফাইল খুজে বের করা
            files_found = []
            async for m in client.get_chat_history(target_channel, offset_id=current_idx if current_idx > 0 else 0, limit=100):
                if m.video or m.document or m.audio:
                    files_found.append(m.id)
                if len(files_found) >= limit_val: break

            if not files_found:
                return await message.reply("এই চ্যানেলে আর কোন ফাইল পাওয়া যায়নি বা ইনডেক্স শেষ।")

            for msg_id in files_found:
                sent_msg = await client.copy_message(user_id, target_channel, msg_id, protect_content=p_on)
                if sent_msg and timer_data:
                    asyncio.create_task(auto_delete_msg(client, user_id, sent_msg.id, timer_data["seconds"]))
                last_sent_id = msg_id
            
            # ইউজারের ইনডেক্স আপডেট (কমান্ড ভিত্তিক)
            await users_col.update_one({"user_id": user_id}, {"$set": {f"custom_indexes.{cmd_input}": files_found[-1]}})
        except Exception as e:
            await message.reply(f"Error: {e}")
    else:
        # সাধারণ ইউজারদের ভেরিফাই করতে বলা হবে
        await message.reply("🚫 এই ক্যাটাগরির ফাইল পেতে আপনাকে প্রিমিয়াম হতে হবে অথবা মেইন গেট ফাইল ভেরিফাই করতে হবে। (অথবা আপনার ইচ্ছেমতো শর্টলিংক লজিক এখানে দিতে পারেন)")

# --- বাকি সব অ্যাডমিন কমান্ড আগের মতোই ---

@app.on_message(filters.command("sendvideo") & filters.user(ADMIN_ID))
async def set_send_video_limit(client, message):
    if len(message.command) < 2:
        return await message.reply("📝 **সঠিক ব্যবহার:** `/sendvideo संख्या` (যেমন: `/sendvideo 5`)")
    try:
        count = int(message.command[1])
        if count < 1:
            return await message.reply("❌ সংখ্যা অবশ্যই ১ এর বেশি হতে হবে।")
        await settings_col.update_one({"id": "video_limit"}, {"$set": {"count": count}}, upsert=True)
        await message.reply(f"✅ সফল! এখন থেকে প্রতি ক্লিকে **{count}টি** করে ভিডিও পাঠানো হবে।")
    except ValueError:
        await message.reply("❌ ভুল ফরম্যাট! শুধু সংখ্যা ব্যবহার করুন।")

@app.on_message(filters.command("index") & filters.user(ADMIN_ID))
async def index_files_handler(client, message):
    status_msg = await message.reply("🔍 ইন্ডেক্সিং শুরু হচ্ছে... অনুগ্রহ করে অপেক্ষা করুন।")
    count = 0
    try:
        async for m in client.get_chat_history(FILE_CHANNEL):
            if m.video or m.document or m.audio:
                exists = await files_col.find_one({"msg_id": m.id})
                if not exists:
                    await files_col.insert_one({"msg_id": m.id, "added_at": datetime.now()})
                    count += 1
                    if count % 50 == 0:
                        await status_msg.edit(f"⏳ প্রসেসিং চলছে... {count} টি নতুন ফাইল পাওয়া গেছে।")
        await status_msg.edit(f"✅ ইন্ডেক্সিং সম্পন্ন!\n\n📂 মোট নতুন ফাইল সেভ হয়েছে: `{count}` টি।")
    except Exception as e:
        await status_msg.edit(f"❌ ভুল হয়েছে: {e}")

@app.on_message(filters.command("batch_index") & filters.user(ADMIN_ID))
async def batch_index_handler(client, message):
    if len(message.command) < 2:
        return await message.reply("📝 **সঠিক নিয়ম:** `/batch_index [মেসেজ লিংক]`")
    link = message.command[1]
    chat_id, last_id = parse_tg_link(link)
    if not chat_id:
        return await message.reply("❌ ভুল লিংক! লাস্ট মেসেজের লিংক দিন।")
    status = await message.reply(f"🔍 ইনডেক্সিং শুরু হচ্ছে...\nচ্যানেল: `{chat_id}`\nশেষ আইডি: `{last_id}`")
    count = 0
    for i in range(1, last_id + 1):
        try:
            msg = await client.copy_message(chat_id=FILE_CHANNEL, from_chat_id=chat_id, message_id=i)
            if msg.video or msg.document or msg.audio:
                await files_col.insert_one({"msg_id": msg.id, "added_at": datetime.now()})
                count += 1
            if i % 25 == 0:
                await status.edit(f"⏳ প্রসেসিং চলছে...\nচেক করা হয়েছে: {i}/{last_id}\nসেভ হয়েছে: {count}")
            await asyncio.sleep(0.5)
        except: continue
    await status.edit(f"✅ **ইনডেক্সিং সম্পন্ন!**\n\n📂 মোট সেভ হয়েছে: `{count}` টি।")

@app.on_message(filters.command("cleardata") & filters.user(ADMIN_ID))
async def cleardata_admin(client, message):
    try:
        await files_col.delete_many({})
        await users_col.update_many({}, {"$set": {"p_index": 0, "f_index": 0, "custom_indexes": {}}})
        await message.reply("✅ ডাটাবেস থেকে সকল ফাইল এবং ইউজার ইনডেক্স ডিলিট করা হয়েছে!")
    except Exception as e:
        await message.reply(f"Error: {e}")

@app.on_message(filters.command("remove_premium") & filters.user(ADMIN_ID))
async def remove_prem_admin(client, message):
    try:
        u_id = int(message.command[1])
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": False}, "$unset": {"expiry_date": ""}})
        await message.reply(f"✅ ইউজার {u_id} এর প্রিমিয়াম রিমুভ হয়েছে।")
    except: await message.reply("সঠিক নিয়ম: `/remove_premium ID`")

@app.on_message(filters.command("add_premium") & filters.user(ADMIN_ID))
async def add_prem_manual(client, message):
    try:
        u_id, days = int(message.command[1]), int(message.command[2])
        expiry = datetime.now() + timedelta(days=days)
        await users_col.update_one({"user_id": u_id}, {"$set": {"is_premium": True, "expiry_date": expiry, "p_index": 0}}, upsert=True)
        await message.reply(f"✅ ইউজার {u_id} এখন প্রিমিয়াম মেম্বার।")
        await send_premium_report(client, u_id, expiry, method=f"Admin Manual")
    except: await message.reply("সঠিক নিয়ম: `/add_premium ID দিন`")

@app.on_message(filters.command("add_redeem") & filters.user(ADMIN_ID))
async def add_red_admin(client, message):
    try:
        duration, count = message.command[1], int(message.command[2])
        codes = []
        for _ in range(count):
            c = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            await redeem_col.insert_one({"code": c, "duration": duration, "is_used": False})
            codes.append(f"`{c}`")
        await message.reply(f"✅ তৈরি হয়েছে:\n" + "\n".join(codes))
    except: await message.reply("সঠিক নিয়ম: `/add_redeem 1month 5`")

@app.on_message(filters.command("addplan") & filters.user(ADMIN_ID))
async def addplan_admin(client, message):
    try:
        days, price = int(message.command[1]), int(message.command[2])
        await plans_col.update_one({"days": days}, {"$set": {"price": price}}, upsert=True)
        await message.reply(f"✅ প্ল্যান এড হয়েছে: {days} দিন - {price} টাকা")
    except: await message.reply("সঠিক নিয়ম: `/addplan দিন টাকা`")

@app.on_message(filters.command("delplan") & filters.user(ADMIN_ID))
async def delplan_admin(client, message):
    try:
        days = int(message.command[1])
        await plans_col.delete_one({"days": days})
        await message.reply(f"✅ প্ল্যান ডিলিট হয়েছে।")
    except: await message.reply("উদা: `/delplan 30`")

@app.on_message(filters.command("set_shortener") & filters.user(ADMIN_ID))
async def set_short_admin(client, message):
    try:
        url, key = message.command[1], message.command[2]
        await settings_col.update_one({"id": "shortener"}, {"$set": {"base_url": url, "api_key": key}}, upsert=True)
        await message.reply(f"✅ সর্টেনার সেট হয়েছে।")
    except: await message.reply("সঠিক নিয়ম: `/set_shortener Domain API`")

@app.on_message(filters.command("del_shortener") & filters.user(ADMIN_ID))
async def del_short_admin(client, message):
    await settings_col.delete_one({"id": "shortener"})
    await message.reply("❌ সর্টেনার সেটিংস ডিলিট করা হয়েছে।")

@app.on_message(filters.command("addtime") & filters.user(ADMIN_ID))
async def add_time_cmd(client, message):
    try:
        time_str = message.command[1]
        duration = parse_duration(time_str)
        await settings_col.update_one({"id": "auto_delete"}, {"$set": {"seconds": duration.total_seconds(), "time_str": time_str}}, upsert=True)
        await message.reply(f"✅ অটো ডিলিট সেট: **{time_str}**")
    except: await message.reply("উদা: `/addtime 5min`")

@app.on_message(filters.command("deltime") & filters.user(ADMIN_ID))
async def del_time_cmd(client, message):
    await settings_col.delete_one({"id": "auto_delete"})
    await message.reply("❌ অটো ডিলিট টাইমার বন্ধ করা হয়েছে।")

@app.on_message(filters.command("set_forward") & filters.user(ADMIN_ID))
async def set_fwd_admin(client, message):
    try:
        status = message.command[1].lower()
        await settings_col.update_one({"id": "forward_setting"}, {"$set": {"protect": (status == "on")}}, upsert=True)
        await message.reply(f"✅ অ্যান্টি-ফরোয়ার্ড {status} হয়েছে।")
    except: await message.reply("নিয়ম: `/set_forward on/off`")

# ==================== ৬. অটো সেভ ও ফাইল হ্যান্ডলার ====================

@app.on_message(filters.chat(FILE_CHANNEL) & (filters.video | filters.document | filters.audio))
async def auto_save_handler(client, message):
    if message.text and message.text.startswith("/"): return
    await files_col.insert_one({"msg_id": message.id, "added_at": datetime.now()})
    await client.send_message(LOG_CHANNEL, f"✅ নতুন ফাইল সেভ হয়েছে! ID: `{message.id}`")

# ==================== ৭. রান কমান্ডস ও ওয়েব সার্ভার ====================

async def uptime_handler(request):
    return web.Response(text="Bot is Alive! 🚀")

async def web_server():
    server = web.Application()
    server.router.add_get("/", uptime_handler) 
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

async def main():
    await web_server()
    await app.start()
    print("বটটি সফলভাবে চালু হয়েছে! 🚀")
    await idle()

if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        pass
