import logging
import os
from time import time
import asyncio
from telegram import Update, ReplyParameters
from telegram.constants import *
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from openai import OpenAI
import pickle
import signal



AI_API_KEY = os.getenv("AI_API_KEY")
AI_MODEL = "deepseek/deepseek-chat:free"
AI_BASE_URL = "https://openrouter.ai/api/v1/"

TOKEN = os.getenv("TOKEN")
BOT_USERNAME = "Carri_the_cat_bot"
TIMEOUT = 30

START_MESSAGE = """Привет!
Меня зовут Карри, я твой котик-психолог, мяу 🐱
Не стесняйся рассказывать мне все, что хочешь!
Чтобы я ответил в групповом чате, тегни меня :3"""
FORGET_MESSAGE = """Ты уверен, что хочешь, чтобы я забыл наш диалог?
Напиши Да или Нет
Если ответишь что угодно, но не Да, то я не буду тебя забывать"""


FORGET_FLAG = False
prev_messages = {}
group_prev_messages = {}
CARRI_PROMPT = ""
ai = None

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def shutdown(signum, frame):
    logger.info("Shutting down...")
    # Сохранение данных перед выходом
    exit(0)
    
def save_history(data, filename):
    try:
        with open(filename, 'wb') as f:
            pickle.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save {filename}: {e}")

async def start(update: Update, context: CallbackContext) -> None:
    global prev_messages
    logger.info(f"got /start message from {update.message.from_user.full_name}")
    await update.message.reply_text(START_MESSAGE)
    if update.message.from_user.id not in prev_messages:
        prev_messages.setdefault(update.message.from_user.id, [])


async def forget(update: Update, context: CallbackContext) -> None:
    logger.info(f"got /forget message from {update.message.from_user.full_name}")
    global FORGET_FLAG
    await update.message.from_user.send_message(FORGET_MESSAGE)
    FORGET_FLAG = True

async def message_handler(update: Update, context: CallbackContext) -> None:
    global FORGET_FLAG, prev_messages
    try:
        logger.info(f"got message from {update.message.from_user.full_name}")
        
        if FORGET_FLAG:
            if update.message.text == "Да":
                prev_messages.pop(update.message.from_user.id, None)
                group_prev_messages.pop(update.message.from_user.id, None)
                with open('data.pickle', 'wb') as f:
                    pickle.dump(prev_messages, f)
                with open('group_data.pickle', 'wb') as f:
                    pickle.dump(group_prev_messages, f)
                FORGET_FLAG = False
                await update.message.reply_text("Было приятно пообщаться! Если хочешь снова поговорить, напиши /start в ЛС или тегни в группе, мяу!")
                return
            else:
                FORGET_FLAG = False
                await update.message.reply_text("Хорошо, я ничего не забуду 😊")
                return
            
        try:
            rpl = await update.message.reply_text("Котек думоет...")
            await ask_carri(update.message, rpl, prev_messages, "data.pickle")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await update.message.reply_text("Мяу... не могу ответить прямо сейчас 😿")

    except Exception as e:
        logger.error(f"Error in message_handler: {e}")
    
    
async def group_message_handler(update: Update, context: CallbackContext) -> None:
    global group_prev_messages
    try:
        logger.info(f"Групповое сообщение от {update.message.from_user.full_name}: {update.message.text}")
        
        mentioned = any(
            entity.type == "mention" and 
            update.message.text[entity.offset:entity.offset+entity.length].lower() == f"@{BOT_USERNAME.lower()}"
            for entity in update.message.entities or []
        )
        
        if not mentioned:
            return
        
        # Удаляем упоминание из текста
        clean_text = update.message.text.replace(f"@{BOT_USERNAME}", "").strip()
        if not clean_text:
            await update.message.reply_text("Мяу? Что ты хочешь спросить?")
            return
        
        # Отправляем ответ
        try:
            rpl = await update.message.reply_text("Котек думоет...")
            await ask_carri(update.message, rpl, group_prev_messages, "group_data.pickle")
        except Exception as e:
            logger.error(f"Error processing group message: {e}")
            await update.message.reply_text("Мяу... не могу сейчас ответить в группе 😿")
    except Exception as e:
        logger.error(f"Error in group_message_handler: {e}")
    

async def ask_carri(input_message, output_message, prev_msgs, file_prev_msgs):
    if not input_message.text.strip():
        await output_message.edit_text("Мяу? Ты ничего не написал!")
        return
    try:
        if input_message.from_user.id not in prev_msgs:
                prev_msgs[input_message.from_user.id] = []
        
        prev_msgs[input_message.from_user.id].append({"role": "user", "content": input_message.text})

        if len(prev_msgs[input_message.from_user.id]) > 50:
            prev_msgs[input_message.from_user.id].pop(0)
        
        try:
            stream = ai.chat.completions.create(model=AI_MODEL,
                                                messages=[{"role": "system", "content": CARRI_PROMPT},
                                                        {"role": "user", "content": " ".join(*prev_msgs[input_message.from_user.id])}],
                                                stream=True,
                                                timeout=TIMEOUT)
        except asyncio.TimeoutError:
            await output_message.edit_text("Мяу... я слишком долго думал и устал 😿 Попробуй спросить снова!")
            logger.error("AI request timeout")
            return
        logger.info("Stream created")
        answ = ""
        
        last_update = time()
        last_text = ""
        
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                tmp = chunk.choices[0].delta.content
                answ += tmp
                
                # Обновляем только если текст изменился и прошло >0.3 сек
                if answ != last_text and time() - last_update > 0.3:
                    try:
                        await output_message.edit_text(answ)
                        last_update = time()
                        last_text = answ
                    except Exception as e:
                        if "Message is not modified" not in str(e):
                            logger.warning(f"Error editing message: {e}")
        
        # Финальное обновление
        if answ and answ != last_text:
            try:
                await output_message.edit_text(answ + "\n🐾")
            except Exception as e:
                logger.warning(f"Error finalizing message: {e}")
    except Exception as e:
        logger.error(f"Error in ask_carri: {e}")
        await output_message.edit_text("Мяу... что-то пошло не так 😿")
    

def main() -> None:
    global CARRI_PROMPT, prev_messages, group_prev_messages, ai
    logger.info("Starting bot...")
    application = (Application
                   .builder()
                   .token(TOKEN)
                   .read_timeout(30)
                   .write_timeout(30)
                   .connect_timeout(30)
                   .pool_timeout(30)
                   .build())
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("forget", forget))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS & filters.Entity("mention"), group_message_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, message_handler))

    logger.info("Bot started in polling mode")
    application.run_polling()

if __name__ == '__main__':
    if not TOKEN or not AI_API_KEY:
        logger.fatal("Missing required environment variables")
        exit(1)
    
    try:
        with open("prompt.txt", "r") as f:
            CARRI_PROMPT = f.read()
    except FileNotFoundError:
        logger.fatal("prompt.txt not found")
        exit

    try:
        with open('data.pickle', 'rb') as f:
            prev_messages = pickle.load(f)
    except FileNotFoundError:
        prev_messages = dict()
        
    try:
        with open('group_data.pickle', 'rb') as f:
            group_prev_messages = pickle.load(f)
    except FileNotFoundError:
        group_prev_messages = dict()

    ai = OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY)
    main()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)