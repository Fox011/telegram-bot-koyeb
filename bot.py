#!/usr/bin/env python3
"""
🤖 ТЕЛЕГРАМ БОТ НАПОМИНАНИЙ
Упрощенная версия без JobQueue для начала
"""

import os
import json
import asyncio
import pytz
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials
import nest_asyncio

# Загружаем переменные из .env файла
from dotenv import load_dotenv
load_dotenv()

# Применяем nest_asyncio для совместимости
nest_asyncio.apply()

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1hN3zFqE3fsb1nLwH3kj2t-5OlzhAIR8A_LMxLaskkd8")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-1002146448322"))
TIMEZONE = pytz.timezone('Europe/Moscow')

# Состояния для диалога
(WAITING_TEXT, WAITING_DATE, WAITING_TIME, WAITING_REPEAT) = range(4)

# Варианты повторения
REPEAT_OPTIONS = [
    "❌ Не повторять",
    "🔄 Каждый день",
    "📅 Каждую неделю",
    "🎄 Каждый год",
    "⏰ За день до",
    "📝 За 3 дня до",
    "🗓️ За неделю до",
    "📆 Понедельник",
    "📆 Вторник",
    "📆 Среда",
    "📆 Четверг",
    "📆 Пятница",
    "📆 Суббота",
    "📆 Воскресенье"
]

# Дни недели для парсинга
WEEKDAYS_RU = {
    'понедельник': 0,
    'вторник': 1,
    'среда': 2,
    'четверг': 3,
    'пятница': 4,
    'суббота': 5,
    'воскресенье': 6,
    'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6
}

# ========== СОЗДАНИЕ ФАЙЛА CREDENTIALS ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
def create_credentials_from_env():
    """
    Создает файл credentials из переменной окружения GOOGLE_CREDENTIALS_JSON.
    """
    try:
        # Получаем JSON строку из переменной окружения
        credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")

        if not credentials_json:
            print("❌ Переменная окружения GOOGLE_CREDENTIALS_JSON не найдена.")
            print("ℹ️  Установите GOOGLE_CREDENTIALS_JSON в настройках сервера")
            return None

        # Парсим JSON
        credentials_data = json.loads(credentials_json)

        # Важно: заменяем \\n на \n в приватном ключе
        if 'private_key' in credentials_data:
            credentials_data['private_key'] = credentials_data['private_key'].replace('\\n', '\n')

        # Сохраняем во временный файл
        creds_file = "/tmp/credentials.json"
        with open(creds_file, 'w') as f:
            json.dump(credentials_data, f, indent=2)

        print(f"✅ Файл {creds_file} создан из переменной окружения")
        return creds_file

    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга JSON из переменной окружения: {e}")
        return None
    except Exception as e:
        print(f"❌ Ошибка создания файла credentials: {e}")
        return None

def setup_google_sheets():
    """Настраивает подключение к Google Sheets"""
    try:
        # Создаем файл с учетными данными из переменной окружения
        creds_file = create_credentials_from_env()
        if not creds_file:
            print("⚠️  Не удалось создать файл credentials. Проверьте переменную GOOGLE_CREDENTIALS_JSON.")
            return None

        # Подключаемся к Google Sheets
        scope = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(creds_file, scopes=scope)
        client = gspread.authorize(creds)

        # Открываем таблицу
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        sheet = spreadsheet.sheet1

        # Проверяем заголовки
        headers = sheet.row_values(1)
        if len(headers) < 8:
            sheet.update('A1:H1', [[
                'Текст',               # A
                'Дата',                # B (ДД.ММ)
                'Время',               # C (ЧЧ:ММ)
                'Повторение',          # D
                'Кто добавил',         # E
                'Когда добавлено',     # F
                'Время напоминания',   # G (полная дата ДД.ММ.ГГГГ ЧЧ:ММ)
                'Статус отправки'      # H
            ]])
            print("✅ Созданы заголовки таблицы")

        print("✅ Подключение к Google Sheets установлено")
        print(f"📊 Таблица: {spreadsheet.title}")
        print(f"🔗 Ссылка: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")

        return sheet

    except Exception as e:
        print(f"❌ Ошибка подключения к Google Sheets: {e}")
        print(f"\n🔧 Проверьте доступ для сервисного аккаунта")
        print(f"📊 Ссылка на таблицу: https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}")
        return None

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦЕЙ ==========
def save_reminder_with_datetime(sheet, text, date, time, repeat, username="Неизвестно"):
    """Сохраняет напоминание с вычислением datetime для планировщика"""
    try:
        # Текущее время в UTC+3
        now_utc3 = datetime.now(TIMEZONE)
        created = now_utc3.strftime("%d.%m.%Y %H:%M")

        # Преобразуем дату и время из пользовательского ввода
        try:
            # Пользователь вводит только день и месяц, используем текущий год
            reminder_date_str = f"{date}.{now_utc3.year}"
            reminder_datetime_naive = datetime.strptime(f"{reminder_date_str} {time}", "%d.%m.%Y %H:%M")

            # Привязываем часовой пояс +3
            reminder_datetime = TIMEZONE.localize(reminder_datetime_naive)

            # Если время уже прошло сегодня, планируем на следующий год
            if reminder_datetime <= now_utc3:
                reminder_date_str = f"{date}.{now_utc3.year + 1}"
                reminder_datetime_naive = datetime.strptime(f"{reminder_date_str} {time}", "%d.%m.%Y %H:%M")
                reminder_datetime = TIMEZONE.localize(reminder_datetime_naive)

            # Форматируем для хранения
            reminder_datetime_str = reminder_datetime.strftime("%d.%m.%Y %H:%M")

        except Exception as e:
            print(f"⚠️ Ошибка преобразования даты: {e}")
            reminder_datetime = None
            reminder_datetime_str = f"{date}.{now_utc3.year} {time}"

        # Добавляем строку в таблицу (8 колонок!)
        row_data = [
            text,               # A: Текст
            date,               # B: Дата (ДД.ММ)
            time,               # C: Время (ЧЧ:ММ)
            repeat,             # D: Повторение
            username,           # E: Кто добавил
            created,            # F: Когда добавлено
            reminder_datetime_str,  # G: Время напоминания (полная дата)
            "❌ Не отправлено"  # H: Статус отправки
        ]
        sheet.append_row(row_data)

        # Получаем номер строки
        all_data = sheet.get_all_values()
        row_number = len(all_data)

        print(f"📝 Сохранено в строку #{row_number}: {text} на {date} {time} (UTC+3)")
        return row_number, reminder_datetime

    except Exception as e:
        print(f"❌ Ошибка сохранения в таблицу: {e}")
        return None, None

def get_all_reminders(sheet):
    """Получает все напоминания из таблицы"""
    try:
        data = sheet.get_all_values()
        if len(data) > 1:
            return data[1:]
        return []
    except Exception as e:
        print(f"❌ Ошибка чтения из таблицы: {e}")
        return []

def delete_from_sheets(sheet, row_number):
    """Удаляет напоминание из таблицы"""
    try:
        sheet.update(f'A{row_number}:H{row_number}', [['', '', '', '', '', '', '', '']])
        print(f"🗑️ Удалена строка #{row_number}")
        return True
    except Exception as e:
        print(f"❌ Ошибка удаления из таблицы: {e}")
        return False

def update_reminder_status(sheet, row_number, status):
    """Обновляет статус отправки напоминания"""
    try:
        sheet.update(f'H{row_number}', [[status]])
        return True
    except Exception as e:
        print(f"❌ Ошибка обновления статуса: {e}")
        return False

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ГРУППОЙ ==========
def parse_bot_command(text: str) -> Optional[str]:
    """Парсит обращение к боту в группе"""
    # Убираем знаки препинания и приводим к нижнему регистру
    clean_text = re.sub(r'[^\w\s]', '', text.lower())
    
    # Паттерны обращения к боту
    patterns = [
        r'бот\s+(.+)',           # "бот помощь", "бот напоминание"
        r'бот[!,.?]?\s*(.+)',    # "бот, помощь", "бот! помощь"
    ]
    
    for pattern in patterns:
        match = re.match(pattern, clean_text)
        if match:
            return match.group(1).strip()
    
    return None

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка сообщений в группе с обращениями к боту"""
    if update.message.chat.id != GROUP_CHAT_ID:
        return
    
    text = update.message.text
    if not text:
        return
    
    # Парсим команду
    command = parse_bot_command(text)
    if not command:
        return
    
    print(f"📨 Команда из группы: {command}")
    
    # Обработка команд
    if command in ['помощь', 'help']:
        await help_command(update, context)
    elif command in ['список', 'list']:
        await list_command(update, context)
    elif command in ['напоминание', 'добавить', 'add']:
        # Запрашиваем данные для добавления напоминания
        await update.message.reply_text(
            "📝 Для добавления напоминания напишите в формате:\n"
            "бот напоминание Текст Дата(ДД.ММ) Время(ЧЧ:ММ) [Повторение]\n\n"
            "Пример: бот напоминание Совещание 25.12 14:30"
        )
    elif 'напоминание' in command:
        # Парсим напоминание из текста
        await add_reminder_from_group(update, context, command)
    else:
        await update.message.reply_text(f"🤔 Не понял команду: {command}")

async def add_reminder_from_group(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str):
    """Добавляет напоминание из команды в группе"""
    try:
        # Парсим команду: "бот напоминание Текст Дата Время"
        parts = command.replace('напоминание', '').strip().split()
        
        if len(parts) < 3:
            await update.message.reply_text(
                "❌ Неправильный формат. Используйте:\n"
                "бот напоминание Текст Дата(ДД.ММ) Время(ЧЧ:ММ)\n"
                "Пример: бот напоминание Совещание 25.12 14:30"
            )
            return
        
        text = parts[0]
        date = parts[1]
        time = parts[2]
        
        # Проверки формата
        if len(date) != 5 or date[2] != '.':
            await update.message.reply_text("❌ Неправильный формат даты. Используйте ДД.ММ")
            return
        
        if len(time) != 5 or time[2] != ':':
            await update.message.reply_text("❌ Неправильный формат времени. Используйте ЧЧ:ММ")
            return
        
        # Проверяем, что время еще не прошло
        now_utc3 = datetime.now(TIMEZONE)
        try:
            reminder_date_str = f"{date}.{now_utc3.year}"
            reminder_datetime_naive = datetime.strptime(f"{reminder_date_str} {time}", "%d.%m.%Y %H:%M")
            reminder_datetime = TIMEZONE.localize(reminder_datetime_naive)
            
            if reminder_datetime <= now_utc3:
                await update.message.reply_text("❌ Время для этой даты уже прошло. Укажите будущую дату.")
                return
        except:
            pass
        
        # Сохраняем для быстрого добавления
        context.user_data['quick_add'] = {
            'text': text,
            'date': date,
            'time': time
        }
        
        # Показываем варианты повторения
        keyboard = []
        for i in range(0, len(REPEAT_OPTIONS), 2):
            row = []
            if i < len(REPEAT_OPTIONS):
                row.append(InlineKeyboardButton(REPEAT_OPTIONS[i], callback_data=f'repeat_{i}'))
            if i+1 < len(REPEAT_OPTIONS):
                row.append(InlineKeyboardButton(REPEAT_OPTIONS[i+1], callback_data=f'repeat_{i+1}'))
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Напоминание:\n"
            f"📝 Текст: {text}\n"
            f"📅 Дата: {date}\n"
            f"⏰ Время: {time}\n\n"
            f"📌 Выберите тип повторения:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при добавлении напоминания: {e}")

# ========== КОМАНДЫ БОТА ==========
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - приветствие"""
    try:
        welcome_text = f"""
👋 Привет! Я бот для напоминаний.

✨ Что я умею:
• Сохранять напоминания в Google Таблицу
• Отправлять напоминания в группу
• Напоминать о событиях вовремя

📋 Доступные команды:
/start - показать это сообщение
/add - добавить новое напоминание
/list - посмотреть все напоминания
/del - удалить напоминание
/help - помощь
/test - тестовая отправка в группу

📊 Google Таблица:
https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}

💬 Группа для напоминаний:
ID: {GROUP_CHAT_ID}

➕ Быстрое добавление:
/add Текст Дата(ДД.ММ) Время(ЧЧ:ММ)

🎯 Пример:
/add Совещание 25.12 14:30

🎉 Напоминание будет сохранено в таблицу и отправлено в группу!
"""
        await update.message.reply_text(welcome_text)
        print(f"✅ Отправлен ответ на /start пользователю {update.effective_user.id}")
        
        # Отправляем приветствие в группу
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text="👋 Привет, я включился и готов напоминать вам о ваших забытых событиях!"
            )
            print("✅ Приветствие отправлено в группу")
        except Exception as e:
            print(f"❌ Ошибка отправки приветствия в группу: {e}")
            
    except Exception as e:
        print(f"❌ Ошибка в start_command: {e}")
        import traceback
        traceback.print_exc()
        await update.message.reply_text("❌ Внутренняя ошибка бота")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help - помощь"""
    help_text = """
ℹ️ **Помощь по использованию бота**

📝 **Формат даты:** ДД.ММ (например: 25.12)
⏰ **Формат времени:** ЧЧ:ММ (например: 14:30)

🔁 **Типы повторения:**
• ❌ Не повторять - одноразовое напоминание
• 🔄 Каждый день - каждый день в это время
• 📅 Каждую неделю - каждую неделю
• 🎄 Каждый год - каждый год
• 📆 Дни недели - каждый указанный день

📌 **Советы:**
• Для быстрого добавления: /add Текст Дата Время
• Пример: /add Встреча 25.12 14:30
• Все данные сохраняются в Google Таблицу

👥 **Команды в группе:**
• "бот помощь" - показать справку
• "бот список" - показать все напоминания
• "бот напоминание Текст Дата Время" - добавить напоминание

🛠️ **Проблемы?**
Если что-то не работает, просто перезапустите бота.
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления напоминания"""
    # Проверяем, есть ли аргументы в команде
    if context.args and len(context.args) >= 3:
        # Быстрое добавление: /add Текст Дата Время
        try:
            text = context.args[0]
            date = context.args[1]
            time = context.args[2]

            # Простые проверки формата
            if len(date) != 5 or date[2] != '.':
                await update.message.reply_text("❌ Неправильный формат даты. Используйте ДД.ММ (например: 25.12)")
                return
            if len(time) != 5 or time[2] != ':':
                await update.message.reply_text("❌ Неправильный формат времени. Используйте ЧЧ:ММ (например: 14:30)")
                return
            
            # Проверяем, что время еще не прошло
            now_utc3 = datetime.now(TIMEZONE)
            try:
                reminder_date_str = f"{date}.{now_utc3.year}"
                reminder_datetime_naive = datetime.strptime(f"{reminder_date_str} {time}", "%d.%m.%Y %H:%M")
                reminder_datetime = TIMEZONE.localize(reminder_datetime_naive)
                
                if reminder_datetime <= now_utc3:
                    await update.message.reply_text("❌ Время для этой даты уже прошло. Укажите будущую дату.")
                    return
            except:
                pass

            # Сохраняем для быстрого добавления
            context.user_data['quick_add'] = {
                'text': text,
                'date': date,
                'time': time
            }

            # Показываем варианты повторения
            keyboard = []
            for i in range(0, len(REPEAT_OPTIONS), 2):
                row = []
                if i < len(REPEAT_OPTIONS):
                    row.append(InlineKeyboardButton(REPEAT_OPTIONS[i], callback_data=f'repeat_{i}'))
                if i+1 < len(REPEAT_OPTIONS):
                    row.append(InlineKeyboardButton(REPEAT_OPTIONS[i+1], callback_data=f'repeat_{i+1}'))
                keyboard.append(row)

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"✅ Быстрое добавление:\n"
                f"📝 Текст: {text}\n"
                f"📅 Дата: {date}\n"
                f"⏰ Время: {time}\n\n"
                f"📌 Выберите тип повторения:",
                reply_markup=reply_markup
            )

            return WAITING_REPEAT

        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
            return ConversationHandler.END

    # Если нет аргументов, начинаем обычный диалог
    await update.message.reply_text("📝 Введите текст напоминания:")
    return WAITING_TEXT

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста напоминания"""
    text = update.message.text
    context.user_data['text'] = text
    await update.message.reply_text("📅 Теперь введите дату в формате ДД.ММ (например: 25.12):")
    return WAITING_DATE

async def handle_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка даты"""
    date = update.message.text
    if len(date) != 5 or date[2] != '.':
        await update.message.reply_text("❌ Неправильный формат. Используйте ДД.ММ (например: 25.12)")
        return WAITING_DATE

    context.user_data['date'] = date
    await update.message.reply_text("⏰ Теперь введите время в формате ЧЧ:ММ (например: 14:30):")
    return WAITING_TIME

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка времени"""
    time = update.message.text
    if len(time) != 5 or time[2] != ':':
        await update.message.reply_text("❌ Неправильный формат. Используйте ЧЧ:ММ (например: 14:30)")
        return WAITING_TIME
    
    # Проверяем, что время еще не прошло
    date = context.user_data['date']
    now_utc3 = datetime.now(TIMEZONE)
    try:
        reminder_date_str = f"{date}.{now_utc3.year}"
        reminder_datetime_naive = datetime.strptime(f"{reminder_date_str} {time}", "%d.%m.%Y %H:%M")
        reminder_datetime = TIMEZONE.localize(reminder_datetime_naive)
        
        if reminder_datetime <= now_utc3:
            await update.message.reply_text("❌ Время для этой даты уже прошло. Укажите будущую дату.")
            return WAITING_DATE
    except:
        pass

    context.user_data['time'] = time

    # Показываем варианты повторения
    keyboard = []
    for i in range(0, len(REPEAT_OPTIONS), 2):
        row = []
        if i < len(REPEAT_OPTIONS):
            row.append(InlineKeyboardButton(REPEAT_OPTIONS[i], callback_data=f'repeat_{i}'))
        if i+1 < len(REPEAT_OPTIONS):
            row.append(InlineKeyboardButton(REPEAT_OPTIONS[i+1], callback_data=f'repeat_{i+1}'))
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📝 Текст: {context.user_data['text']}\n"
        f"📅 Дата: {context.user_data['date']}\n"
        f"⏰ Время: {context.user_data['time']}\n\n"
        f"📌 Выберите тип повторения:",
        reply_markup=reply_markup
    )

    return WAITING_REPEAT

async def show_repeat_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает варианты повторения"""
    query = update.callback_query
    await query.answer()

    keyboard = []
    for i in range(0, len(REPEAT_OPTIONS), 2):
        row = []
        if i < len(REPEAT_OPTIONS):
            row.append(InlineKeyboardButton(REPEAT_OPTIONS[i], callback_data=f'repeat_{i}'))
        if i+1 < len(REPEAT_OPTIONS):
            row.append(InlineKeyboardButton(REPEAT_OPTIONS[i+1], callback_data=f'repeat_{i+1}'))
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text="📌 Выберите тип повторения:",
        reply_markup=reply_markup
    )

    return WAITING_REPEAT

async def handle_repeat_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора повторения"""
    query = update.callback_query
    await query.answer()

    repeat_index = int(query.data.split('_')[1])
    repeat_text = REPEAT_OPTIONS[repeat_index]

    # Получаем данные
    if 'quick_add' in context.user_data:
        # Быстрое добавление
        data = context.user_data['quick_add']
        text = data['text']
        date = data['date']
        time = data['time']
        del context.user_data['quick_add']
    else:
        # Обычное добавление
        text = context.user_data['text']
        date = context.user_data['date']
        time = context.user_data['time']

    # Получаем имя пользователя
    username = update.effective_user.username
    if not username:
        username = update.effective_user.first_name or "Неизвестно"

    # Получаем объект таблицы
    sheet = context.application.bot_data.get('sheet')
    if not sheet:
        await query.edit_message_text("❌ Не удалось подключиться к Google Sheets")
        return ConversationHandler.END

    # Сохраняем напоминание в таблицу
    row_number, reminder_datetime = save_reminder_with_datetime(
        sheet, text, date, time, repeat_text, username
    )

    if not row_number:
        await query.edit_message_text("❌ Не удалось сохранить напоминание в таблицу")
        return ConversationHandler.END

    # Отправляем подтверждение
    await query.edit_message_text(
        f"✅ Напоминание сохранено!\n\n"
        f"📝 Текст: {text}\n"
        f"📅 Дата: {date}\n"
        f"⏰ Время: {time}\n"
        f"🔁 Повторение: {repeat_text}\n"
        f"👤 Добавил: {username}\n\n"
        f"📊 Сохранено в строку #{row_number}\n\n"
        f"⚠️  Внимание: JobQueue не настроен.\n"
        f"📌 Напоминания будут сохраняться в таблицу,\n"
        f"но автоматическая отправка в группу не работает.\n"
        f"🔧 Для включения автоматической отправки\n"
        f"нужно настроить JobQueue."
    )

    return ConversationHandler.END

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /list - список всех напоминаний"""
    sheet = context.application.bot_data.get('sheet')
    if not sheet:
        await update.message.reply_text("❌ Не удалось подключиться к Google Sheets")
        return

    reminders = get_all_reminders(sheet)

    if not reminders:
        await update.message.reply_text("📭 Напоминаний пока нет")
        return

    response = "📋 Все напоминания:\n\n"
    for i, reminder in enumerate(reminders, 1):
        if len(reminder) >= 4:
            response += f"{i}. {reminder[0]} | {reminder[1]} {reminder[2]} | {reminder[3]}\n"
            if len(reminder) >= 6:
                response += f"   👤 {reminder[4]} | 📅 {reminder[5]}\n"
            response += "\n"

    # Разбиваем сообщение, если оно слишком длинное
    if len(response) > 4000:
        parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(response)

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /del - очистка напоминания БЕЗ подтверждения"""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите номер строки для очистки\nПример: `/del 2`",
            parse_mode='Markdown'
        )
        return

    try:
        # 1. Получаем номер строки из аргумента
        row_number = int(context.args[0])

        # 2. Проверяем, что номер положительный (можно удалять с 1)
        if row_number < 1:
            await update.message.reply_text("❌ Номер строки должен быть положительным")
            return

        # 3. Получаем объект таблицы
        sheet = context.application.bot_data.get('sheet')
        if not sheet:
            await update.message.reply_text("❌ Не удалось подключиться к Google Sheets")
            return

        # 4. Получаем все данные для проверки
        all_data = sheet.get_all_values()

        # Строка 0 в all_data = Заголовок (строка 1 в таблице). Поэтому проверяем row_number > len(all_data)-1
        if row_number > (len(all_data) - 1):  # -1, так как заголовок не считаем
            await update.message.reply_text(f"❌ Строка #{row_number} не найдена")
            return

        # 5. ОЧИСТКА СТРОКИ (БЕЗ УДАЛЕНИЯ ИЗ ТАБЛИЦЫ)
        sheet_row_to_clear = row_number + 1  # Преобразуем ввод пользователя (начиная с 1) в номер строки в таблице

        # Обновляем строку в Google Sheets, затирая данные в столбцах A-F
        empty_row = ['', '', '', '', '', '', '', '']
        sheet.update(f'A{sheet_row_to_clear}:H{sheet_row_to_clear}', [empty_row])

        # 6. Отправляем сообщение об успехе
        await update.message.reply_text(f"✅ Напоминание в строке #{row_number} очищено")

    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Используйте: `/del номер_строки`", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Произошла ошибка при очистке: `{e}`")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /test - тестовая отправка в группу"""
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="🧪 Тестовое сообщение от бота!\n"
                 f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                 f"👤 От: {update.effective_user.username or update.effective_user.first_name}"
        )
        await update.message.reply_text("✅ Тестовое сообщение отправлено в группу")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена диалога"""
    await update.message.reply_text("❌ Диалог отменен")
    context.user_data.clear()
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    print(f"⚠️ Ошибка: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text("❌ Произошла ошибка при обработке команды")

# ========== ОСНОВНАЯ ФУНКЦИЯ ==========
def main():
    """Основная функция для запуска бота"""
    print("🤖 Запуск Telegram бота напоминаний...")
    print(f"📅 Дата запуска: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"🌍 Часовой пояс: {TIMEZONE}")
    
    # Проверяем обязательные переменные окружения
    required_env_vars = ['BOT_TOKEN', 'GOOGLE_CREDENTIALS_JSON']
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"❌ ОШИБКА: Не установлены обязательные переменные окружения:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nℹ️  Установите переменные окружения:")
        print("   export BOT_TOKEN='ваш_токен'")
        print("   export GOOGLE_CREDENTIALS_JSON='ваш_json'")
        return

    # Настраиваем подключение к Google Sheets
    sheet = setup_google_sheets()
    if not sheet:
        print("⚠️  Предупреждение: Не удалось подключиться к Google Sheets")
        print("ℹ️  Бот будет работать, но без сохранения в таблицу")

    # Создаем приложение бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Сохраняем объект sheet в данные бота
    application.bot_data['sheet'] = sheet

    # Создаем ConversationHandler для диалога добавления
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_command)],
        states={
            WAITING_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)],
            WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date)],
            WAITING_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time)],
            WAITING_REPEAT: [
                CallbackQueryHandler(handle_repeat_selection, pattern='^repeat_'),
                CallbackQueryHandler(show_repeat_options, pattern='^show_repeat$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_chat=True,
        per_user=True,
        per_message=False
    )

    # Регистрируем обработчики команд
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("del", delete_command))
    application.add_handler(CommandHandler("test", test_command))

    # Обработчик сообщений в группе
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Chat(chat_id=GROUP_CHAT_ID),
        handle_group_message
    ))

    # Регистрируем обработчик ошибок
    application.add_error_handler(error_handler)

    print("✅ Бот инициализирован. Запускаю...")
    print("⚠️  JobQueue не настроен. Напоминания сохраняются в таблицу,")
    print("   но автоматическая отправка в группу не работает.")

    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

# ========== ТОЧКА ВХОДА ==========
if __name__ == "__main__":
    main()
