"""
Управление правами пользователей в боте

Система прав доступа настраивается через переменные окружения в файле .env:

1. ADMIN_IDS - список ID администраторов через запятую
   Пример: ADMIN_IDS=123456789,987654321

2. Права доступа для различных действий:
   PERMISSION_CREATE - создание заявок
   PERMISSION_APPROVE - одобрение заявок
   PERMISSION_REJECT - отклонение заявок
   PERMISSION_EDIT - редактирование заявок
   PERMISSION_VIEW_ALL - просмотр всех заявок

   Возможные значения для каждого права:
   - "all" - доступно всем пользователям
   - "admins" - доступно только администраторам
   - "none" - недоступно никому

Пример конфигурации в .env:
ADMIN_IDS=123456789,987654321
PERMISSION_CREATE=all
PERMISSION_APPROVE=admins
PERMISSION_REJECT=admins
PERMISSION_EDIT=admins
PERMISSION_VIEW_ALL=admins

Примечания:
1. Администраторы всегда имеют доступ ко всем функциям
2. Обычные пользователи могут видеть только свои заявки
3. Для изменения прав требуется редактирование файла .env
"""

import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import BadRequest
from database import (
    init_db, get_or_create_user, create_request, update_request_status,
    add_comment, RequestStatus, get_requests, get_request, get_requests_paginated, get_request_comments,
    update_request, User
)
from validators import (
    validate_amount, validate_partner_account, validate_note,
    validate_period, validate_date, validate_comment,
    validate_rejection_reason, validate_edit_value
)
import json
import tempfile
import shutil
from typing import List, Dict
from sqlalchemy import select, func
import pandas as pd

# --- Отключение HTTP логов ---
for noisy_logger in ['httpx', 'urllib3', 'telegram.vendor.ptb_urllib3.urllib3.connectionpool']:
    logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# Load environment variables
load_dotenv('bot.env')
timeDelta=3 #Delta for UTC+3

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s\n'
           'Function: %(funcName)s\n'
           'Line: %(lineno)d\n'
           'Message: %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# States for conversation handler
(
    CHOOSING_PROJECT,
    ENTERING_AMOUNT,
    CHOOSING_CURRENCY,
    CHOOSING_SOURCE,
    ATTACHING_DOCUMENT,
    ENTERING_NOTE,
    CHOOSING_PERIOD,
    CHOOSING_DATE,
    CONFIRMING_REQUEST,
    ENTERING_PARTNER_ACCOUNT,
    ADMIN_MENU,
    VIEWING_REQUESTS,
    VIEWING_REQUEST_DETAILS,
    EDITING_REQUEST,
    ADDING_COMMENT,
    ADDING_REJECTION_REASON,
    EDITING_SOURCE,
    EXPORTING_DATA,
    EXPORT_MENU,
    EXPORT_FORMAT
) = range(20)

class BotConfig:
    """Configuration class for the bot."""
    # Define SOURCES before __init__
    SOURCES = {
        'rs_rf': '🏦 РС РФ Сервис+ Точкабанк',
        'rs_too_kz': '🏦 РС ТОО КЗ',
        'rs_ip_kz': '🏦 РС ИП КЗ',
        'card_too_kz': '💳 Карта ТОО КЗ',
        'card_ip_kz': '💳 Карта ИП КЗ',
        'rs_ooo_am': '🏦 РС ООО АМ',
        'rs_ooo_am_eur': '🏦 РС ООО АМ EUR',
        'card_ooo_am': '💳 Карта ООО АМ',
        'crypto': '💰 Крипта',
        'cash': '💵 Наличные'
    }

    def __init__(self):
        self.TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
        if not self.TELEGRAM_TOKEN:
            raise ValueError("No TELEGRAM_TOKEN found in environment variables!")

        # Определяем проекты до их использования
        self.PROJECTS = {
            'mf_rf': '🇷🇺 МФ РФ',
            'mf_kz': '🇰🇿 МФ КЗ',
            'mf_am': '🇦🇲 МФ АМ',
            'mf_world': '🌐 МФ ВОРЛД'
        }

        self.CURRENCIES = {
            'RUB': 'Рубль',
            'KZT': 'Тенге',
            'AMD': 'Драм',
            'USD': 'USD',
            'EUR': 'EUR',
            'USDT': 'USDT'
        }

        # Словарь символов валют
        self.CURRENCY_SYMBOLS = {
            'RUB': '₽',
            'KZT': '₸',
            'AMD': '֏',
            'USD': '$',
            'EUR': '€',
            'USDT': '₮',
            'BTC': '₿',
            'DEFAULT': '💱'
        }
        
        # Базовый список администраторов из переменной окружения
        self.ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS', '').split(',') if admin_id]
        
        # Список пользователей финконтроля
        self.FINCONTROL_IDS = [int(fc_id) for fc_id in os.getenv('FINCONTROL_IDS', '').split(',') if fc_id]
        
        # Список администраторов с полным доступом
        self.ALL_ACCESS_ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ALL_ACCESS_ADMIN_IDS', '').split(',') if admin_id]
        
        # Маппинг администраторов к источникам
        self.ADMIN_SOURCE_MAPPING = {}
        for source in self.SOURCES.keys():
            env_var = f'ADMIN_SOURCE_{source.upper()}'
            admin_ids = os.getenv(env_var, '')
            if admin_ids:
                self.ADMIN_SOURCE_MAPPING[source] = [int(admin_id) for admin_id in admin_ids.split(',') if admin_id]
        
        # Маппинг администраторов к криптовалютным проектам
        self.ADMIN_CRYPTO_MAPPING = {}
        for project in ['mf_rf', 'mf_kz', 'mf_am', 'mf_world']:
            env_var = f'ADMIN_CRYPTO_{project.upper()}'
            admin_ids = os.getenv(env_var, '')
            if admin_ids:
                self.ADMIN_CRYPTO_MAPPING[project] = [int(admin_id) for admin_id in admin_ids.split(',') if admin_id]
        
        # Простая система прав доступа через переменные окружения
        # Возможные значения: "all" - все пользователи, "admins" - только администраторы, "none" - никто
        self.PERMISSION_CREATE = os.getenv('PERMISSION_CREATE', 'all')
        self.PERMISSION_APPROVE = os.getenv('PERMISSION_APPROVE', 'admins') 
        self.PERMISSION_REJECT = os.getenv('PERMISSION_REJECT', 'admins')
        self.PERMISSION_EDIT = os.getenv('PERMISSION_EDIT', 'admins')
        self.PERMISSION_VIEW_ALL = os.getenv('PERMISSION_VIEW_ALL', 'admins')
            
        # Старая настройка для совместимости
        self.ALLOW_ADMIN_CREATE = os.getenv('ALLOW_ADMIN_CREATE', 'false').lower() == 'true'

        self.NOTES = [
            'Реклама',
            'Сопровождение РК',
            'Ком-ции. СМС',
            'Ком-ции. АВТОДОЗВОНЫ',
            'Ком-ции. РАССЫЛКИ',
            'Ком-ции. ТЕЛЕФОНИЯ',
            'Ком-ции. ОНЛАЙН'
        ]

    def can_access_source(self, user_id: int, source: str, project: str = None) -> bool:
        """Проверяет, имеет ли пользователь доступ к указанному источнику."""
        # Администраторы с полным доступом имеют доступ ко всем источникам
        if user_id in self.ALL_ACCESS_ADMIN_IDS:
            return True
            
        # Для криптовалютных источников проверяем доступ по проекту
        if source == 'crypto' and project:
            return user_id in self.ADMIN_CRYPTO_MAPPING.get(project, [])
            
        # Для остальных источников проверяем доступ по источнику
        return user_id in self.ADMIN_SOURCE_MAPPING.get(source, [])

class BotHandlers:
    """Class containing all bot handlers."""
    STATUS_DISPLAY = {
        'pending': 'Ожидает подтверждения',
        'waiting': 'Одобрено/Ожидает оплаты',
        'paid': 'Оплачена',
        'rejected': 'Отклонена'
    }
    def __init__(self, config: BotConfig, db_session):
        self.config = config
        self.db_session = db_session
        self.status_emoji = {
            RequestStatus.PENDING: "⏳",
            RequestStatus.WAITING: "💰",
            RequestStatus.PAID: "✅",
            RequestStatus.REJECTED: "❌"
        }
        # Cache for user info to reduce database queries
        self._user_cache = {}
        # Cache for request details
        self._request_cache = {}
        # Cache timeout in seconds
        self._cache_timeout = 300  # 5 minutes

    def _get_cached_user(self, user_id):
        """Get user from cache or database with caching."""
        cache_key = f"user_{user_id}"
        if cache_key in self._user_cache:
            cache_time, user = self._user_cache[cache_key]
            if (datetime.now() - cache_time).total_seconds() < self._cache_timeout:
                return user
        
        user = self.db_session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            self._user_cache[cache_key] = (datetime.now(), user)
        return user

    def _get_cached_request(self, request_id):
        """Get request from cache or database with caching."""
        cache_key = f"request_{request_id}"
        if cache_key in self._request_cache:
            cache_time, request = self._request_cache[cache_key]
            if (datetime.now() - cache_time).total_seconds() < self._cache_timeout:
                return request
        
        request = get_request(self.db_session, request_id)
        if request:
            self._request_cache[cache_key] = (datetime.now(), request)
        return request

    def _get_main_menu_keyboard(self, user_id: int) -> list:
        """Generate main menu keyboard based on user role."""
        keyboard = []
        
        # Check if user has full access
        if user_id in self.config.ALL_ACCESS_ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
                [InlineKeyboardButton("📊 Экспорт данных", callback_data="export_data")],
                [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
            ]
        # Check if user is admin
        elif user_id in self.config.ADMIN_IDS:
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                [InlineKeyboardButton("📊 Экспорт данных", callback_data="export_data")],
                [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
            ]
        # Check if user is in fincontrol group
        elif user_id in self.config.FINCONTROL_IDS:
            keyboard = [
                [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                [InlineKeyboardButton("📊 Экспорт данных", callback_data="export_data")],
                [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
            ]
        # Regular user
        else:
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
                [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
            ]
        
        return keyboard

    def _get_main_menu_message(self, user_id: int) -> str:
        """Generate main menu message based on user role."""
        if user_id in self.config.ALL_ACCESS_ADMIN_IDS:
            return "👋 Панель администратора. Выберите действие:"
        elif user_id in self.config.ADMIN_IDS:
            return "👋 Панель администратора. Выберите действие:"
        elif user_id in self.config.FINCONTROL_IDS:
            return "👋 Панель финконтроля. Выберите действие:"
        else:
            return "👋 Добро пожаловать! Выберите действие:"

    async def _show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str = None) -> int:
        """Show main menu based on user role."""
        try:
            user_id = update.effective_user.id
            
            # Get keyboard and message based on user role
            keyboard = self._get_main_menu_keyboard(user_id)
            default_message = self._get_main_menu_message(user_id)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    message or default_message,
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    message or default_message,
                    reply_markup=reply_markup
                )
            
            return ADMIN_MENU
            
        except Exception as e:
            logger.error(f"Error in _show_main_menu: {e}")
            if update.callback_query:
                await self._handle_error(update.callback_query, "showing main menu")
            return ADMIN_MENU

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start the conversation and ask for project selection."""
        try:
            user = get_or_create_user(
                self.db_session,
                update.effective_user.id,
                update.effective_user.username
            )
            
            return await self._show_main_menu(update, context)

        except Exception as e:
            logger.error(f"Error in start command: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при запуске бота. Пожалуйста, попробуйте позже или обратитесь в техподдержку @butterglobe"
            )
            return ConversationHandler.END

    async def handle_admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик всех кнопок в админ-меню."""
        try:
            query = update.callback_query
            await query.answer()

            logger.info(f"Admin callback received: {query.data}")

            # Маршрутизация запросов на основе callback data
            if query.data == "create_request":
                return await self.admin_create_request(query, context)
            elif query.data == "view_requests":
                return await self.admin_view_requests(query, context)
            elif query.data == "my_requests":
                return await self.view_my_requests(query, context)
            elif query.data == "admin_settings":
                # Проверяем права доступа
                if update.effective_user.id not in self.config.ALL_ACCESS_ADMIN_IDS:
                    await query.answer("У вас нет прав для доступа к настройкам", show_alert=True)
                    return ADMIN_MENU
                return await self.admin_settings(query, context)
            elif query.data == "export_data":
                # Проверяем права доступа
                if not (update.effective_user.id in self.config.ADMIN_IDS or 
                       update.effective_user.id in self.config.FINCONTROL_IDS or 
                       update.effective_user.id in self.config.ALL_ACCESS_ADMIN_IDS):
                    await query.answer("У вас нет прав для экспорта данных", show_alert=True)
                    return ADMIN_MENU
                return await self.handle_export_data(update, context)
            elif query.data.startswith("export_requests") or query.data.startswith("export_users"):
                return await self.handle_export_format(update, context)
            elif query.data.endswith("_excel") or query.data.endswith("_csv"):
                return await self.process_export(update, context)
            elif query.data.startswith("setting_"):
                return await self.handle_settings_option(query, context)
            elif query.data == "back_to_menu":
                return await self._show_main_menu(Update(update_id=0, callback_query=query), context)
            elif query.data.startswith("filter_"):
                return await self._handle_filter(query, context)
            elif query.data in ["prev_page", "next_page"]:
                return await self._handle_page_navigation(query, context)
            elif query.data.startswith("request_"):
                # Обработка нажатия на заявку в списке
                request_id = int(query.data.split('_')[1])
                return await self.view_request_details_by_id(update, context, request_id)
            else:
                logger.warning(f"Unhandled admin callback: {query.data}")
                return ADMIN_MENU

        except Exception as e:
            logger.error(f"Error in handle_admin_callback: {e}")
            if 'query' in locals():
                await self._handle_error(query, "admin callback")
            return ADMIN_MENU

    async def admin_create_request(self, query, context) -> int:
        """Обработка создания заявки администратором."""
        keyboard = [
            [InlineKeyboardButton(project, callback_data=project_id)]
            for project_id, project in self.config.PROJECTS.items()
        ]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "📝 Выберите проект для новой заявки:",
            reply_markup=reply_markup
        )
        logger.info(f"Admin creating new request, showing project selection")
        return CHOOSING_PROJECT

    async def admin_view_requests(self, query, context) -> int:
        """Обработка просмотра заявок администратором."""
        # Сбрасываем флаг просмотра своих заявок
        context.user_data['viewing_my_requests'] = False
        context.user_data['request_filter'] = None
        context.user_data['request_page'] = 0

        keyboard = [
            [InlineKeyboardButton("⏳ Ожидают подтверждения", callback_data="filter_pending")],
            [InlineKeyboardButton("💰 Одобрено/Ожидают оплаты", callback_data="filter_waiting")],
            [InlineKeyboardButton("✅ Оплаченные", callback_data="filter_paid")],
            [InlineKeyboardButton("❌ Отклоненные", callback_data="filter_rejected")],
            [InlineKeyboardButton("📋 Все заявки", callback_data="filter_all")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        logger.info("Showing request filter keyboard")
        await query.edit_message_text(
            "Выберите категорию заявок для просмотра (либо отправьте id заявки в чате):",
            reply_markup=reply_markup
        )
        return VIEWING_REQUESTS

    async def admin_settings(self, query, context) -> int:
        """Отображение меню настроек администратора."""
        user_id = query.from_user.id

        # Создаем клавиатуру с текущими настройками из переменных окружения
        keyboard = []

        # Создание/редактирование заявок
        can_create = self.config.PERMISSION_CREATE == 'all' or (self.config.PERMISSION_CREATE == 'admins' and user_id in self.config.ADMIN_IDS)
        keyboard.append([InlineKeyboardButton(
            f"{'✅' if can_create else '❌'} Создание заявок", 
            callback_data="setting_toggle_create"
        )])

        # Доступ к просмотру всех заявок
        can_view_all = self.config.PERMISSION_VIEW_ALL == 'all' or (self.config.PERMISSION_VIEW_ALL == 'admins' and user_id in self.config.ADMIN_IDS)
        keyboard.append([InlineKeyboardButton(
            f"{'✅' if can_view_all else '❌'} Доступ ко всем проектам", 
            callback_data="setting_toggle_view_all"
        )])

        # Возможность одобрять заявки
        can_approve = self.config.PERMISSION_APPROVE == 'all' or (self.config.PERMISSION_APPROVE == 'admins' and user_id in self.config.ADMIN_IDS)
        keyboard.append([InlineKeyboardButton(
            f"{'✅' if can_approve else '❌'} Одобрение заявок", 
            callback_data="setting_toggle_approve"
        )])

        # Возможность отклонять заявки
        can_reject = self.config.PERMISSION_REJECT == 'all' or (self.config.PERMISSION_REJECT == 'admins' and user_id in self.config.ADMIN_IDS)
        keyboard.append([InlineKeyboardButton(
            f"{'✅' if can_reject else '❌'} Отклонение заявок", 
            callback_data="setting_toggle_reject"
        )])

        # Возможность редактировать заявки
        can_edit = self.config.PERMISSION_EDIT == 'all' or (self.config.PERMISSION_EDIT == 'admins' and user_id in self.config.ADMIN_IDS)
        keyboard.append([InlineKeyboardButton(
            f"{'✅' if can_edit else '❌'} Редактирование заявок", 
            callback_data="setting_toggle_edit"
        )])

        # Кнопка возврата в главное меню
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "⚙️ Настройки администратора\n\n"
            "Текущие права доступа:",
            reply_markup=reply_markup
        )

        logger.info(f"Admin settings displayed for user {user_id}")
        return ADMIN_MENU

    async def handle_settings_option(self, query, context) -> int:
        """Обработчик изменения настроек администратора."""
        try:
            user_id = query.from_user.id
            setting_action = query.data.replace("setting_toggle_", "")

            # Информируем пользователя о том, что настройки нельзя изменить через бота
            await query.answer(
                "Настройки можно изменить только в файле конфигурации (.env).\n"
                "Обратитесь к администратору системы.", 
                show_alert=True
            )

            # Показываем меню настроек без изменений
            return await self.admin_settings(query, context)

        except Exception as e:
            logger.error(f"Error in handle_settings_option: {e}")
            if 'query' in locals():
                await self._handle_error(query, "settings update")
            return ADMIN_MENU

    async def back_to_admin_menu(self, query, context) -> int:
        """Возврат в главное меню администратора."""
        return await self._show_main_menu(Update(update_id=0, callback_query=query), context)

    async def project_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle project selection."""
        try:
            query = update.callback_query
            await query.answer()

            # Обработка кнопки "Назад"
            if query.data == "back_to_menu":
                # Проверяем, является ли пользователь администратором
                if update.effective_user.id in self.config.ADMIN_IDS:
                    keyboard = [
                        [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                        [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                        [InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
                        [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.edit_message_text(
                        "👋 Панель администратора. Выберите действие:",
                        reply_markup=reply_markup
                    )
                else:
                    keyboard = [
                        [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                        [InlineKeyboardButton("📋 Мои заявки", callback_data="my_requests")],
                        [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.edit_message_text(
                        "👋 Добро пожаловать! Выберите действие:",
                        reply_markup=reply_markup
                    )
                return ADMIN_MENU

            project_id = query.data
            context.user_data['project'] = project_id

            # Create keyboard for currency selection with emojis
            keyboard = []
            for currency_id, currency in self.config.CURRENCIES.items():
                symbol = self.config.CURRENCY_SYMBOLS.get(currency_id, self.config.CURRENCY_SYMBOLS['DEFAULT'])
                button_text = f"{symbol} {currency}"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=currency_id)])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"Выбран проект: {self.config.PROJECTS[project_id]}\n\n"
                "Выберите валюту:",
                reply_markup=reply_markup
            )
            return CHOOSING_CURRENCY

        except Exception as e:
            logger.error(f"Error in project_selected: {e}")
            if 'query' in locals():
                await self._handle_error(query, "project selection")
            return ConversationHandler.END

    async def currency_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle currency selection."""
        try:
            query = update.callback_query
            await query.answer()

            # Обработка кнопки "Назад"
            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Для обычных пользователей - возврат к выбору проекта
                    keyboard = [
                        [InlineKeyboardButton(project, callback_data=project_id)]
                        for project_id, project in self.config.PROJECTS.items()
                    ]
                    keyboard.append([InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")])
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        "👋 Выберите проект:",
                        reply_markup=reply_markup
                    )
                    return CHOOSING_PROJECT

            currency_id = query.data
            context.user_data['currency'] = currency_id      # Ask for amount
            await query.edit_message_text(
                f"Выбрана валюта: {self.config.CURRENCIES[currency_id]}\n\n"
                "Введите сумму (только цифры):"
            )
            return ENTERING_AMOUNT

        except Exception as e:
            logger.error(f"Error in currency_selected: {e}")
            if 'query' in locals():
                await self._handle_error(query, "currency selection")
            return ConversationHandler.END

    async def amount_entered(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle amount input."""
        try:
            amount_text = update.message.text.strip()
            
            try:
                amount = validate_amount(amount_text)
                context.user_data['amount'] = amount

                # Create keyboard for source selection with emojis
                keyboard = []
                for source_id, source in self.config.SOURCES.items():
                    if 'crypto' in source_id.lower():
                        button_text = f"{source}"
                    elif 'bank' in source_id.lower():
                        button_text = f"{source}"
                    else:
                        button_text = f"{source}"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=source_id)])
                
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"Сумма: {amount}{self.config.CURRENCIES[context.user_data['currency']]}\n\n"
                    "Выберите источник средств:",
                    reply_markup=reply_markup
                )
                return CHOOSING_SOURCE

            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return ENTERING_AMOUNT

        except Exception as e:
            logger.error(f"Error in amount_entered: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке суммы. Пожалуйста, попробуйте еще раз."
            )
            return ENTERING_AMOUNT

    async def source_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle source selection."""
        try:
            query = update.callback_query
            await query.answer()

            # Обработка кнопки "Назад"
            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Возврат к выбору валюты
                    keyboard = [
                        [InlineKeyboardButton(currency, callback_data=currency_id)]
                        for currency_id, currency in self.config.CURRENCIES.items()
                    ]
                    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        f"Выберите валюту:",
                        reply_markup=reply_markup
                    )
                    return CHOOSING_CURRENCY

            source_id = query.data
            context.user_data['source'] = source_id

            # Сразу переходим к запросу документа
            keyboard = [
                [InlineKeyboardButton("⏩ Пропустить", callback_data="skip")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"Выбран источник: {self.config.SOURCES[source_id]}\n\n"
                "Пожалуйста, отправьте документ (чек или квитанцию):\n"
                "(или нажмите «Пропустить», если документа нет)",
                reply_markup=reply_markup
            )
            return ATTACHING_DOCUMENT

        except Exception as e:
            logger.error(f"Error in source_selected: {e}")
            if 'query' in locals():
                await self._handle_error(query, "source selection")
            return ConversationHandler.END

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle document attachment."""
        try:
            if update.callback_query:
                query = update.callback_query
                await query.answer()

                if query.data == "skip":
                    # Create keyboard for note selection
                    keyboard = [
                        [InlineKeyboardButton(note, callback_data=f"note_{i}")]
                        for i, note in enumerate(self.config.NOTES)
                    ]
                    keyboard.append([InlineKeyboardButton("📝 Свой вариант", callback_data="custom_note")])
                    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        "Выберите примечание или введите свой вариант:",
                        reply_markup=reply_markup
                    )
                    return ENTERING_NOTE

                await query.edit_message_text(
                    "Отправьте документ или фото:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
                )
                return ATTACHING_DOCUMENT

            # Handle actual document/photo
            if update.message.document:
                file = update.message.document
                file_name = file.file_name
            elif update.message.photo:
                file = update.message.photo[-1]  # Get the largest photo
                file_name = f"photo_{file.file_id}.jpg"
            else:
                await update.message.reply_text(
                    "❌ Пожалуйста, отправьте документ или фото.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
                )
                return ATTACHING_DOCUMENT

            # Save file info in context
            context.user_data['document'] = {
                'file_id': file.file_id,
                'file_name': file_name
            }

            # Create keyboard for note selection
            keyboard = [
                [InlineKeyboardButton(note, callback_data=f"note_{i}")]
                for i, note in enumerate(self.config.NOTES)
            ]
            keyboard.append([InlineKeyboardButton("📝 Свой вариант", callback_data="custom_note")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "Документ прикреплен. Выберите примечание или введите свой вариант:",
                reply_markup=reply_markup
            )
            return ENTERING_NOTE

        except Exception as e:
            logger.error(f"Error in handle_document: {e}")
            if update.callback_query:
                query = update.callback_query
                await self._handle_error(query, "document attachment")
            else:
                await update.message.reply_text(
                    "❌ Произошла ошибка при прикреплении документа. Пожалуйста, попробуйте позже."
                )
            return ATTACHING_DOCUMENT

    async def handle_partner_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle partner account selection."""
        try:
            query = update.callback_query
            await query.answer()

            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Return to source selection
                    keyboard = [
                        [InlineKeyboardButton(source, callback_data=source_id)]
                        for source_id, source in self.config.SOURCES.items()
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        "Выберите источник средств:",
                        reply_markup=reply_markup
                    )
                    return CHOOSING_SOURCE

            await query.edit_message_text(
                "Введите счет партнера:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
            )
            return ENTERING_PARTNER_ACCOUNT

        except Exception as e:
            logger.error(f"Error in handle_partner_account: {e}")
            if 'query' in locals():
                await self._handle_error(query, "partner account selection")
            return ATTACHING_DOCUMENT

    async def handle_partner_account_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle partner account input."""
        try:
            partner_account = update.message.text.strip()
            
            try:
                partner_account = validate_partner_account(partner_account)
                context.user_data['partner_account'] = partner_account

                # Create keyboard for note selection
                keyboard = [
                    [InlineKeyboardButton(note, callback_data=f"note_{i}")]
                    for i, note in enumerate(self.config.NOTES)
                ]
                keyboard.append([InlineKeyboardButton("📝 Свой вариант", callback_data="custom_note")])
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"Счет партнера: {partner_account}\n\n"
                    "Выберите примечание или введите свой вариант:",
                    reply_markup=reply_markup
                )
                return ENTERING_NOTE

            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return ENTERING_PARTNER_ACCOUNT

        except Exception as e:
            logger.error(f"Error in handle_partner_account_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке счета партнера. Пожалуйста, попробуйте еще раз."
            )
            return ENTERING_PARTNER_ACCOUNT

    async def handle_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle note selection."""
        try:
            query = update.callback_query
            await query.answer()

            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Return to document attachment
                    keyboard = [
                        [InlineKeyboardButton("📎 Прикрепить документ", callback_data="attach")],
                        [InlineKeyboardButton("💳 Указать счет партнера", callback_data="partner")],
                        [InlineKeyboardButton("⏩ Пропустить", callback_data="skip")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        "Выберите действие:",
                        reply_markup=reply_markup
                    )
                    return ATTACHING_DOCUMENT

            if query.data == "custom_note":
                await query.edit_message_text(
                    "Введите ваше примечание:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return ENTERING_NOTE

            note_index = int(query.data.split('_')[1])
            note = self.config.NOTES[note_index]
            context.user_data['note'] = note

            # Create keyboard for period selection
            keyboard = [
                [InlineKeyboardButton("Единоразово", callback_data="single")],
                [InlineKeyboardButton("Ежемесячно", callback_data="monthly")],
                [InlineKeyboardButton("Еженедельно", callback_data="weekly")],
                [InlineKeyboardButton("Свой вариант", callback_data="custom_period")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"Примечание: {note}\n\n"
                "Выберите периодичность:",
                reply_markup=reply_markup
            )
            return CHOOSING_PERIOD

        except Exception as e:
            logger.error(f"Error in handle_note: {e}")
            if 'query' in locals():
                await self._handle_error(query, "note selection")
            return ENTERING_NOTE

    async def handle_custom_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle custom note input."""
        try:
            note_text = update.message.text.strip()
            
            try:
                note = validate_note(note_text)
                context.user_data['note'] = note

                # Create keyboard for period selection
                keyboard = [
                    [InlineKeyboardButton("Единоразово", callback_data="period_once")],
                    [InlineKeyboardButton("Ежемесячно", callback_data="period_monthly")],
                    [InlineKeyboardButton("Ежеквартально", callback_data="period_quarterly")],
                    [InlineKeyboardButton("Ежегодно", callback_data="period_yearly")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"Примечание: {note}\n\n"
                    "Выберите периодичность:",
                    reply_markup=reply_markup
                )
                return CHOOSING_PERIOD

            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return ENTERING_NOTE

        except Exception as e:
            logger.error(f"Error in handle_custom_note: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке примечания. Пожалуйста, попробуйте еще раз."
            )
            return ENTERING_NOTE

    async def handle_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle period selection."""
        try:
            query = update.callback_query
            await query.answer()

            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Return to note selection
                    keyboard = [
                        [InlineKeyboardButton(note, callback_data=f"note_{i}")]
                        for i, note in enumerate(self.config.NOTES)
                    ]
                    keyboard.append([InlineKeyboardButton("📝 Свой вариант", callback_data="custom_note")])
                    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        "Выберите примечание или введите свой вариант:",
                        reply_markup=reply_markup
                    )
                    return ENTERING_NOTE

            if query.data == "custom_period":
                await query.edit_message_text(
                    "Введите свой вариант периодичности:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return CHOOSING_PERIOD

            period_map = {
                "single": "Единоразово",
                "monthly": "Ежемесячно",
                "weekly": "Еженедельно"
            }

            period = period_map.get(query.data, query.data)
            context.user_data['period'] = period

            # Получаем текущую дату для кнопки "Сегодня"
            current_date = datetime.now().strftime('%d.%m.%Y')
            
            # Создаем клавиатуру с кнопкой "Сегодня"
            keyboard = [
                [InlineKeyboardButton(f"📅 Сегодня ({current_date})", callback_data=f"date_{current_date}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_period")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"Периодичность: {period}\n\n"
                "Укажите дату или период в формате ДД.ММ.ГГГГ или ДД.ММ.ГГГГ - ДД.ММ.ГГГГ:\n"
                "(или выберите сегодняшнюю дату кнопкой ниже)",
                reply_markup=reply_markup
            )
            return CHOOSING_DATE

        except Exception as e:
            logger.error(f"Error in handle_period: {e}")
            if 'query' in locals():
                await self._handle_error(query, "period selection")
            return CHOOSING_PERIOD

    async def handle_period_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle period input."""
        try:
            period_text = update.message.text.strip()
            
            try:
                period = validate_period(period_text)
                context.user_data['period'] = period

                # Create keyboard for date selection
                keyboard = [
                    [InlineKeyboardButton("Сегодня", callback_data=f"date_{datetime.now().strftime('%d.%m.%Y')}")],
                    [InlineKeyboardButton("Завтра", callback_data=f"date_{(datetime.now() + timedelta(days=1)).strftime('%d.%m.%Y')}")],
                    [InlineKeyboardButton("Ввести дату", callback_data="custom_date")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    f"Период: {period}\n\n"
                    "Выберите дату:",
                    reply_markup=reply_markup
                )
                return CHOOSING_DATE

            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return CHOOSING_PERIOD

        except Exception as e:
            logger.error(f"Error in handle_period_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке периода. Пожалуйста, попробуйте еще раз."
            )
            return CHOOSING_PERIOD

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle date input."""
        try:
            # Check if this is a callback query (button press) or text input
            if update.callback_query:
                query = update.callback_query
                await query.answer()
                
                if query.data.startswith("date_"):
                    # Extract date from callback data (format: date_DD.MM.YYYY)
                    date_text = query.data.replace("date_", "")
                    try:
                        date = validate_date(date_text)
                        context.user_data['date'] = date
                        
                        # Create summary for confirmation
                        project = context.user_data.get('project', '')
                        amount = context.user_data.get('amount', 0)
                        currency = context.user_data.get('currency', '')
                        source = context.user_data.get('source', '')
                        note = context.user_data.get('note', '')
                        period = context.user_data.get('period', '')

                        summary = "📋 Проверьте данные заявки:\n\n"
                        summary += f"Проект: {self.config.PROJECTS.get(project, project)}\n"
                        summary += f"Сумма: {amount}{self.config.CURRENCIES.get(currency, currency)}\n"
                        summary += f"Источник: {self.config.SOURCES.get(source, source)}\n"

                        if 'partner_account' in context.user_data:
                            summary += f"Счет партнера: {context.user_data['partner_account']}\n"

                        doc_path = context.user_data.get('document', {}).get('path')
                        if doc_path:
                            summary += f"Документ: {doc_path}\n"

                        summary += f"Примечание: {note}\n"
                        summary += f"Периодичность: {period}\n"
                        summary += f"Дата/период: {date_text}\n"

                        keyboard = [
                            [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
                            [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)

                        await query.edit_message_text(
                            summary,
                            reply_markup=reply_markup
                        )
                        return CONFIRMING_REQUEST
                    except ValueError as e:
                        await query.edit_message_text(
                            f"❌ {str(e)}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])
                        )
                        return CHOOSING_DATE
                
            else:
                # Handle text input for date
                date_text = update.message.text.strip()
                try:
                    date = validate_date(date_text)
                    context.user_data['date'] = date

                    # Create summary for confirmation
                    project = context.user_data.get('project', '')
                    amount = context.user_data.get('amount', 0)
                    currency = context.user_data.get('currency', '')
                    source = context.user_data.get('source', '')
                    note = context.user_data.get('note', '')
                    period = context.user_data.get('period', '')

                    summary = "📋 Проверьте данные заявки:\n\n"
                    summary += f"Проект: {self.config.PROJECTS.get(project, project)}\n"
                    summary += f"Сумма: {amount} {self.config.CURRENCIES.get(currency, currency)}\n"
                    summary += f"Источник: {self.config.SOURCES.get(source, source)}\n"

                    if 'partner_account' in context.user_data:
                        summary += f"Счет партнера: {context.user_data['partner_account']}\n"

                    doc_path = context.user_data.get('document', {}).get('path')
                    if doc_path:
                        summary += f"Документ: {doc_path}\n"

                    summary += f"Примечание: {note}\n"
                    summary += f"Периодичность: {period}\n"
                    summary += f"Дата/период: {date_text}\n"

                    keyboard = [
                        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
                        [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await update.message.reply_text(
                        summary,
                        reply_markup=reply_markup
                    )
                    return CONFIRMING_REQUEST
                except ValueError as e:
                    await update.message.reply_text(
                        f"❌ {str(e)}"
                    )
                    return CHOOSING_DATE

        except Exception as e:
            logger.error(f"Error in handle_date: {e}")
            if update.callback_query:
                query = update.callback_query
                await self._handle_error(query, "date input")
            else:
                await update.message.reply_text(
                    "❌ Произошла ошибка при обработке даты. Пожалуйста, попробуйте еще раз."
                )
            return CHOOSING_DATE

    async def confirm_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle request confirmation."""
        try:
            query = update.callback_query
            await query.answer()

            if query.data == "cancel":
                await query.edit_message_text(
                    "❌ Заявка отменена. Для создания новой заявки используйте /start"
                )
                return ConversationHandler.END

            # Create request in database first to get request ID
            request_data = {
                'user_id': update.effective_user.id,
                'project': context.user_data.get('project', ''),
                'amount': context.user_data.get('amount', 0),
                'currency': context.user_data.get('currency', ''),
                'source': context.user_data.get('source', ''),
                'note': context.user_data.get('note', ''),
                'partner_account': context.user_data.get('partner_account', None)
            }
            
            # Добавляем запись о периодичности платежа и дате в поле note
            note_additions = []
            
            if context.user_data.get('period'):
                period_text = context.user_data.get('period', '')
                note_additions.append(f"Периодичность: {period_text}")
                
            if context.user_data.get('date'):
                date_text = context.user_data.get('date', '')
                note_additions.append(f"Дата/период: {date_text}")
                
            if note_additions:
                if request_data['note']:
                    request_data['note'] += "\n" + "\n".join(note_additions)
                else:
                    request_data['note'] = "\n".join(note_additions)

            # Создаем заявку в базе данных
            request = create_request(self.db_session, **request_data)

            # Логируем создание заявки
            self._log_request_creation(request.id, update.effective_user.id, request_data)

            # Если есть документ, скачиваем его в папку заявки
            if 'document' in context.user_data:
                try:
                    # Создаем директорию для документов заявки
                    request_dir = f"documents/{request.id}"
                    os.makedirs(request_dir, exist_ok=True)

                    # Получаем информацию о файле
                    file_id = context.user_data['document']['file_id']
                    file_name = context.user_data['document']['file_name']
                    file_path = f"{request_dir}/{file_name}"

                    # Скачиваем файл
                    file_obj = await context.bot.get_file(file_id)
                    await file_obj.download_to_drive(file_path)

                    # Обновляем путь к файлу в базе данных
                    update_request(self.db_session, request.id, document_path=file_path)
                except Exception as e:
                    logger.error(f"Error downloading document: {e}")
                    # Продолжаем выполнение даже если не удалось скачать файл

            # Check if user is admin
            if update.effective_user.id in self.config.ADMIN_IDS:
                # Create keyboard for admin menu
                keyboard = [
                    [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                    [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                    [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"✅ Заявка #{request.id} успешно создана!\n\n"
                    "Выберите действие:",
                    reply_markup=reply_markup
                )
                return ADMIN_MENU
            else:
                # Regular user
                await query.edit_message_text(
                    f"✅ Заявка #{request.id} успешно создана!\n\n"
                    "Вы получите уведомление при изменении статуса заявки.\n\n"
                    "Для создания новой заявки используйте /start"
                )
                return ConversationHandler.END

        except Exception as e:
            logger.error(f"Error in confirm_request: {e}")
            if 'query' in locals():
                await self._handle_error(query, "request confirmation")
            return CONFIRMING_REQUEST

    def _get_requests_for_user(self, user_id: int, status_filter=None, page=0, page_size=5):
        """Get requests based on user's access rights."""
        try:
            # Базовый запрос
            query = select(Request)
            
            # Если пользователь не админ и не финконтроль, показываем только его заявки
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                query = query.where(Request.user_id == user_id)
            else:
                # Для админов и финконтроля фильтруем по доступным источникам
                allowed_sources = []
                
                # Проверяем доступ к источникам
                for source in self.config.SOURCES.keys():
                    if self.config.can_access_source(user_id, source):
                        allowed_sources.append(source)
                
                # Если есть разрешенные источники, применяем фильтр
                if allowed_sources:
                    query = query.where(Request.source.in_(allowed_sources))
            
            # Применяем фильтр по статусу если указан
            if status_filter:
                query = query.where(Request.status == status_filter)
            
            # Сортируем по дате создания (новые сверху)
            query = query.order_by(Request.created_at.desc())
            
            # Применяем пагинацию
            total = self.db_session.execute(select(func.count()).select_from(query.subquery())).scalar()
            query = query.offset(page * page_size).limit(page_size)
            
            # Выполняем запрос
            requests = self.db_session.execute(query).scalars().all()
            
            return requests, total
            
        except Exception as e:
            logger.error(f"Error in _get_requests_for_user: {e}")
            return [], 0

    async def _show_filter_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Show filter menu for request viewing."""
        try:
            user_id = update.effective_user.id
            
            # Проверяем права доступа
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                # Для обычных пользователей показываем только их заявки
                return await self.view_my_requests(update.callback_query, context)
            
            keyboard = self._get_filter_keyboard()
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    "Выберите категорию заявок для просмотра (либо отправьте id заявки в чате):",
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_text(
                    "Выберите категорию заявок для просмотра (либо отправьте id заявки в чат):",
                    reply_markup=reply_markup
                )
            
            return VIEWING_REQUESTS
            
        except Exception as e:
            logger.error(f"Error in _show_filter_menu: {e}")
            if update.callback_query:
                await self._handle_error(update.callback_query, "showing filter menu")
            return VIEWING_REQUESTS

    async def _handle_filter(self, query, context):
        """Handle filter selection."""
        try:
            user_id = query.from_user.id
            
            # Проверяем права доступа
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                await query.answer("У вас нет прав для просмотра всех заявок", show_alert=True)
                return await self.view_my_requests(query, context)
            
            filter_type = query.data.split('_')[1]
            context.user_data['request_filter'] = filter_type
            context.user_data['request_page'] = 0

            logger.info(f"Filtering requests by: {filter_type}")

            status_filter = None
            if filter_type != "all":
                status_filter = getattr(RequestStatus, filter_type.upper(), None)

            # Получаем заявки с учетом прав доступа
            requests, total = self._get_requests_for_user(user_id, status_filter, 0, 5)
            total_pages = (total + 5 - 1) // 5

            message, keyboard = self._create_request_list_message(requests, context)
            message += f"\nСтраница 1 из {total_pages} | Всего заявок: {total}"

            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
            return VIEWING_REQUESTS
            
        except Exception as e:
            logger.error(f"Error in _handle_filter: {e}")
            if 'query' in locals():
                await self._handle_error(query, "filtering requests")
            return ADMIN_MENU

    async def _handle_page_navigation(self, query, context):
        """Handle page navigation."""
        try:
            user_id = query.from_user.id
            
            # Проверяем права доступа
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                await query.answer("У вас нет прав для просмотра всех заявок", show_alert=True)
                return await self.view_my_requests(query, context)
            
            page = context.user_data.get('request_page', 0)

            if query.data == "next_page":
                page += 1
            elif query.data == "prev_page" and page > 0:
                page -= 1

            context.user_data['request_page'] = page

            filter_type = context.user_data.get('request_filter')
            status_filter = None
            if filter_type and filter_type != "all":
                status_filter = getattr(RequestStatus, filter_type.upper(), None)

            # Получаем заявки с учетом прав доступа
            requests, total = self._get_requests_for_user(user_id, status_filter, page, 5)
            total_pages = (total + 5 - 1) // 5

            message, keyboard = self._create_request_list_message(requests, context)
            message += f"\nСтраница {page+1} из {total_pages} | Всего заявок: {total}"
            await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
            return VIEWING_REQUESTS
            
        except Exception as e:
            logger.error(f"Error in _handle_page_navigation: {e}")
            if 'query' in locals():
                await self._handle_error(query, "page navigation")
            return ADMIN_MENU

    async def _handle_back_to_menu(self, query):
        """Handle back to menu action."""
        try:
            keyboard = [
                [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                "👋 Панель администратора. Выберите действие:",
                reply_markup=reply_markup
            )
            return ADMIN_MENU
        except Exception as e:
            logger.error(f"Error in _handle_back_to_menu: {e}")
            if 'query' in locals():
                await self._handle_error(query, "back to menu")
            return ADMIN_MENU

    def _format_user_info(self, user):
        """Format user information for display.
        
        Priority order:
        1. First name (if available)
        2. Username (if available)
        3. Telegram ID (if available)
        4. User ID (as last resort)
        """
        if not user:
            return "Неизвестный пользователь"
            
        # Сначала проверяем имя
        if hasattr(user, 'first_name') and user.first_name:
            return user.first_name
        
        # Затем проверяем username
        if hasattr(user, 'username') and user.username:
            return f"@{user.username}"
        
        # Если есть telegram_id, используем его
        if hasattr(user, 'telegram_id'):
            return f"user_{user.telegram_id}"
        
        # В последнюю очередь используем id
        return f"user_{user.id}"

    def _create_request_list_message(self, requests, context):
        """Create message and keyboard for request list."""
        if not requests:
            message = "📋 Заявки не найдены."
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_filters")]]
            return message, keyboard

        filter_type = context.user_data.get('request_filter', 'all')
        filter_name = {
            'pending': '⏳ Ожидают подтверждения',
            'waiting': '💰 Одобрено/Ожидают оплаты',
            'paid': '✅ Оплаченные',
            'rejected': '❌ Отклоненные',
            'all': '📋 Все заявки'
        }.get(filter_type, '📋 Заявки')

        message = f"{filter_name}:\n\n"

        for req in requests:
            emoji = self.status_emoji.get(req.status, "")
            user = self.db_session.query(User).filter_by(telegram_id=req.user_id).first()
            user_info = self._format_user_info(user)
            
            # Получаем название валюты и символ из словарей
            currency_name = self.config.CURRENCIES.get(req.currency, req.currency)
            currency_symbol = self.config.CURRENCY_SYMBOLS.get(req.currency, self.config.CURRENCY_SYMBOLS['DEFAULT'])
            
            message += f"{emoji} #{req.id} - {self.config.PROJECTS[req.project]} - "
            message += f"{req.amount} {currency_symbol} {currency_name} - {user_info}\n"
            message += f"Создано: {(req.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')}  "
            message += f"Изменено: {(req.updated_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')}\n\n"

        keyboard = []
        for req in requests:
            # Получаем название валюты и символ для кнопок
            currency_name = self.config.CURRENCIES.get(req.currency, req.currency)
            currency_symbol = self.config.CURRENCY_SYMBOLS.get(req.currency, self.config.CURRENCY_SYMBOLS['DEFAULT'])
            keyboard.append([InlineKeyboardButton(
                f"{self.status_emoji.get(req.status, '')} #{req.id} - {req.amount} {currency_symbol} {currency_name}",
                callback_data=f"request_{req.id}"
            )])

        page = context.user_data.get('request_page', 0)
        nav_buttons = []

        # Добавляем кнопку "Пред." только если мы не на первой странице
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Пред.", callback_data="prev_page"))

        # Получаем общее количество заявок для расчета общего количества страниц
        filter_type = context.user_data.get('request_filter')
        status_filter = None
        if filter_type and filter_type != "all":
            status_filter = getattr(RequestStatus, filter_type.upper(), None)

        # Получаем общее количество заявок
        _, total = get_requests_paginated(self.db_session, status=status_filter, page=0, page_size=1)
        total_pages = (total + 5 - 1) // 5  # 5 - размер страницы

        # Добавляем кнопку "След." только если есть следующая страница
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("След. ➡️", callback_data="next_page"))

        if nav_buttons:
            keyboard.append(nav_buttons)

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_filters")])

        return message, keyboard

    def _format_request_details(self, request, context):
        """Форматирование деталей заявки в сообщение."""
        user = self.db_session.query(User).filter_by(telegram_id=request.user_id).first()
        emoji = self.status_emoji.get(request.status, "")

        message = f"{emoji} Заявка #{request.id}\n\n"
        message += f"Проект: {self.config.PROJECTS.get(request.project, request.project)}\n"
        # Добавляем символ валюты к сумме
        currency_symbol = self.config.CURRENCY_SYMBOLS.get(request.currency, self.config.CURRENCY_SYMBOLS['DEFAULT'])
        message += f"Сумма: {request.amount} {currency_symbol} {self.config.CURRENCIES.get(request.currency, request.currency)}\n"
        message += f"Источник: {self.config.SOURCES.get(request.source, request.source)}\n"
        message += f"От: {self._format_user_info(user)}\n"
        
        # Добавляем поле "Кому" если указан счет партнера
        if request.partner_account:
            message += f"Кому: {request.partner_account}\n"
            
        message += f"Статус: {self.STATUS_DISPLAY.get(request.status.value, request.status.value)}\n"
        message += f"Дата: {(request.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')}\n"

        # Формируем блок с деталями заявки в нужном порядке
        period_text = getattr(request, 'period', None) or context.user_data.get('period', None)
        date_period_text = None
        note_text = None
        if request.note:
            lines = request.note.split('\n')
            for line in lines:
                if line.startswith('Дата/период:'):
                    date_period_text = line
                elif line.startswith('Периодичность:'):
                    period_text = line.replace('Периодичность:', '').strip()
                elif line.strip():
                    note_text = line
        if period_text:
            message += f"\nПериодичность: {period_text}"
        if date_period_text:
            message += f"\n{date_period_text}"
        if note_text:
            message += f"\nПримечание: {note_text}"

        # Показываем историю статусов
        if hasattr(request, 'status_history') and request.status_history:
            try:
                history = json.loads(request.status_history)
                message += "\n\nИстория статусов:"
                for entry in history:
                    status = entry['status'].upper()
                    timestamp = datetime.fromisoformat(entry['timestamp'])
                    user_id = entry.get('user_id')
                    user = self.db_session.query(User).filter_by(telegram_id=user_id).first() if user_id else None
                    user_info = self._format_user_info(user) if user else "Система"
                    status_display = self.STATUS_DISPLAY.get(status.lower(), status)
                    formatted_date = (timestamp + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')
                    message += f"\n- Изменён статус на {status_display} \n {formatted_date} - {user_info}"
            except Exception as e:
                logger.error(f"Error parsing status history: {e}")
                message += f"\n\nТекущий статус: {self.STATUS_DISPLAY.get(request.status.value.lower(), request.status.value)}"
        else:
            message += f"\n\nТекущий статус: {self.STATUS_DISPLAY.get(request.status.value.lower(), request.status.value)}"

        # Показываем комментарии
        comments = get_request_comments(self.db_session, request.id)
        if comments:
            message += "\n\nКомментарии:"
            for comment in comments:
                # Ищем пользователя по telegram_id из комментария
                comment_user = self.db_session.query(User).filter_by(telegram_id=comment.telegram_id).first()
                user_info = self._format_user_info(comment_user) if comment_user else "Неизвестный пользователь"
                formatted_date = (comment.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M:%S')
                message += f"\n\n💬 {comment.text}\n👤 {user_info}\n🕒 {formatted_date}"

        return message

    def _create_request_actions_keyboard(self, request, request_id):
        """Создание клавиатуры с действиями для заявки."""
        keyboard = []
        
        # Add action buttons based on request status
        if request.status == RequestStatus.PENDING:
            keyboard.extend([
                [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{request_id}")],
                [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{request_id}")]
            ])
        elif request.status == RequestStatus.WAITING:
            keyboard.append([InlineKeyboardButton("✅ Отметить как оплаченную", callback_data=f"approve_{request_id}")])

        # Add common buttons
        keyboard.extend([
            [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{request_id}")],
            [InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment_{request_id}")],
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]
        ])
        
        return keyboard

    async def view_request_details_by_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int = None) -> int:
        """Просмотр деталей заявки по ID."""
        try:
            query = update.callback_query
            await query.answer()

            # Если request_id не передан, извлекаем его из callback_data
            if request_id is None and query.data.startswith('view_'):
                request_id = int(query.data.split('_')[1])

            request = get_request(self.db_session, request_id)

            if not request:
                await query.edit_message_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS

            # Проверяем права доступа
            user_id = update.effective_user.id
            is_admin = user_id in self.config.ADMIN_IDS
            is_owner = request.user_id == user_id

            if not (is_admin or is_owner):
                await query.edit_message_text(
                    "❌ У вас нет прав для просмотра этой заявки.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS

            message = self._format_request_details(request, context)
            keyboard = self._create_request_actions_keyboard(request, request_id)
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Если есть документ, отправляем сообщение с документом
            if request.document_path and os.path.exists(request.document_path):
                try:
                    # Отправляем сообщение о загрузке
                    loading_message = await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="⏳ Идёт загрузка документа..."
                    )

                    # Загружаем документ
                    with open(request.document_path, 'rb') as doc:
                        # Отправляем сообщение с документом
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=doc,
                            caption=message,
                            reply_markup=reply_markup
                        )
                        
                        # Удаляем сообщение о загрузке
                        await loading_message.delete()
                        
                        # Удаляем предыдущее сообщение с деталями
                        await query.message.delete()
                except Exception as e:
                    logger.error(f"Error sending document: {e}")
                    # Обновляем сообщение о загрузке на сообщение об ошибке
                    await loading_message.edit_text(
                        f"❌ Не удалось отправить документ к заявке #{request_id}"
                    )
                    # Отправляем сообщение с деталями без документа
                    await query.edit_message_text(message, reply_markup=reply_markup)
            else:
                # Если документа нет, просто отправляем сообщение с деталями
                await query.edit_message_text(message, reply_markup=reply_markup)

            return VIEWING_REQUEST_DETAILS

        except Exception as e:
            logger.error(f"Error in view_request_details_by_id: {e}")
            if 'query' in locals():
                await self._handle_error(query, "viewing request details")
            return VIEWING_REQUESTS

    async def handle_request_id_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода ID заявки в меню просмотра заявок."""
        text = update.message.text.strip()
        if text.isdigit():
            request_id = int(text)
            request = get_request(self.db_session, request_id)
            if request:
                # Проверяем права доступа
                user_id = update.effective_user.id
                is_admin = user_id in self.config.ADMIN_IDS
                is_owner = request.user_id == user_id

                if not (is_admin or is_owner):
                    await update.message.reply_text(
                        "❌ У вас нет прав для просмотра этой заявки.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                    )
                    return VIEWING_REQUESTS

                message = self._format_request_details(request, context)
                keyboard = self._create_request_actions_keyboard(request, request_id)
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Если есть документ, отправляем сообщение с документом
                if request.document_path and os.path.exists(request.document_path):
                    try:
                        # Отправляем сообщение о загрузке
                        loading_message = await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="⏳ Идёт загрузка документа..."
                        )

                        # Загружаем документ
                        with open(request.document_path, 'rb') as doc:
                            # Отправляем сообщение с документом
                            await context.bot.send_document(
                                chat_id=update.effective_chat.id,
                                document=doc,
                                caption=message,
                                reply_markup=reply_markup
                            )
                            
                            # Удаляем сообщение о загрузке
                            await loading_message.delete()
                    except Exception as e:
                        logger.error(f"Error sending document: {e}")
                        # Обновляем сообщение о загрузке на сообщение об ошибке
                        await loading_message.edit_text(
                            f"❌ Не удалось отправить документ к заявке #{request_id}"
                        )
                        # Отправляем сообщение с деталями без документа
                        await update.message.reply_text(message, reply_markup=reply_markup)
                else:
                    # Если документа нет, просто отправляем сообщение с деталями
                    await update.message.reply_text(message, reply_markup=reply_markup)

                return VIEWING_REQUEST_DETAILS
            else:
                await update.message.reply_text(f"Заявка с ID {request_id} не найдена.")
                return VIEWING_REQUESTS
        else:
            await update.message.reply_text("Пожалуйста, введите корректный ID заявки (число).")
            return VIEWING_REQUESTS

    async def handle_request_navigation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle request list navigation."""
        try:
            query = update.callback_query
            await query.answer()

            logger.info(f"=== Navigation Handler Start ===")
            logger.info(f"Callback data: {query.data}")

            if query.data.startswith("filter_"):
                return await self._handle_filter(query, context)
            elif query.data in ["prev_page", "next_page"]:
                return await self._handle_page_navigation(query, context)
            elif query.data == "back_to_filters":
                # Проверяем, просматривает ли пользователь свои заявки
                if context.user_data.get('viewing_my_requests'):
                    # Возвращаемся к просмотру своих заявок
                    return await self.view_my_requests(query, context)
                else:
                    # Показываем список фильтров
                    return await self._show_filter_menu(Update(update_id=0, callback_query=query), context)
            elif query.data == "back_to_menu":
                return await self._show_main_menu(Update(update_id=0, callback_query=query), context)
            elif query.data == "back_to_list":
                # Проверяем, как пользователь попал на просмотр заявки
                if context.user_data.get('viewing_my_requests'):
                    # Возвращаемся к просмотру своих заявок
                    return await self.view_my_requests(query, context)
                elif context.user_data.get('request_filter'):
                    # Возвращаемся к текущему фильтру
                    filter_type = context.user_data['request_filter']
                    page = context.user_data.get('request_page', 0)
                    
                    # Получаем заявки для текущего фильтра
                    status_filter = None
                    if filter_type != "all":
                        status_filter = getattr(RequestStatus, filter_type.upper(), None)
                    
                    requests, total = get_requests_paginated(self.db_session, status=status_filter, page=page, page_size=5)
                    total_pages = (total + 5 - 1) // 5
                    
                    message, keyboard = self._create_request_list_message(requests, context)
                    message += f"\nСтраница {page+1} из {total_pages} | Всего заявок: {total}"
                    
                    # Проверяем, содержит ли текущее сообщение документ
                    if query.message.document:
                        # Если сообщение содержит документ, отправляем новое сообщение
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=message,
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        # Удаляем старое сообщение с документом
                        await query.message.delete()
                    else:
                        # Если сообщение не содержит документ, редактируем его
                        await query.edit_message_text(
                            text=message,
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    
                    return VIEWING_REQUESTS
                else:
                    # Если нет контекста фильтра, возвращаемся к выбору категорий
                    return await self._show_filter_menu(Update(update_id=0, callback_query=query), context)
            elif query.data.startswith("request_"):
                # Обработка нажатия на заявку
                request_id = int(query.data.split('_')[1])
                return await self.view_request_details_by_id(update, context, request_id)

            return VIEWING_REQUESTS

        except Exception as e:
            logger.error(f"Error in handle_request_navigation: {e}")
            if 'query' in locals():
                await self._handle_error(query, "navigation")
            return VIEWING_REQUESTS

    async def _handle_message_with_document(self, query, text, reply_markup):
        """Helper method to handle messages that might contain documents."""
        if query.message.document:
            # If message contains document, send new message
            await query.message.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=reply_markup
            )
            # Delete old message with document
            await query.message.delete()
        else:
            # If no document, just edit the message
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup
            )

    async def handle_request_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle request actions (approve, reject, edit, comment)."""
        try:
            query = update.callback_query
            await query.answer()

            action, request_id = query.data.split('_')
            request_id = int(request_id)
            request = get_request(self.db_session, request_id)

            if not request:
                await query.edit_message_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS

            user_id = update.effective_user.id
            
            if action == "approve":
                # Проверка прав на одобрение заявки
                can_approve = self.config.PERMISSION_APPROVE == 'all' or (self.config.PERMISSION_APPROVE == 'admins' and user_id in self.config.ADMIN_IDS)
                
                if not can_approve:
                    await query.answer("У вас нет прав для одобрения заявок", show_alert=True)
                    return await self.view_request_details_by_id(update, context, request_id)
                
                if request.status == RequestStatus.PENDING:
                    # Добавляем метку времени при изменении статуса
                    timestamp = (datetime.now() + timedelta(hours=timeDelta)).strftime('%d.%m.%Y %H:%M')
                    update_request_status(self.db_session, request_id, RequestStatus.WAITING, user_id)
                    
                    # Логируем изменение статуса
                    self._log_status_change(request_id, user_id, RequestStatus.PENDING, RequestStatus.WAITING)
                    
                    # Проверяем, содержит ли сообщение документ
                    if query.message.document:
                        # Если есть документ, отправляем новое сообщение
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"✅ Заявка одобрена и ожидает оплаты. {timestamp}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К заявке", callback_data=f"view_{request_id}")]])
                        )
                        # Удаляем старое сообщение с документом
                        await query.message.delete()
                    else:
                        # Если документа нет, редактируем существующее сообщение
                        await query.edit_message_text(
                            f"✅ Заявка одобрена и ожидает оплаты. {timestamp}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К заявке", callback_data=f"view_{request_id}")]])
                        )
                elif request.status == RequestStatus.WAITING:
                    # Добавляем метку времени при изменении статуса
                    timestamp = datetime.now().strftime('%d.%m.%Y %H:%M')
                    update_request_status(self.db_session, request_id, RequestStatus.PAID, user_id)
                    
                    # Логируем изменение статуса
                    self._log_status_change(request_id, user_id, RequestStatus.WAITING, RequestStatus.PAID)
                    
                    # Проверяем, содержит ли сообщение документ
                    if query.message.document:
                        # Если есть документ, отправляем новое сообщение
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"✅ Заявка отмечена как оплаченная. {timestamp}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К заявке", callback_data=f"view_{request_id}")]])
                        )
                        # Удаляем старое сообщение с документом
                        await query.message.delete()
                    else:
                        # Если документа нет, редактируем существующее сообщение
                        await query.edit_message_text(
                            f"✅ Заявка отмечена как оплаченная. {timestamp}",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К заявке", callback_data=f"view_{request_id}")]])
                        )

            elif action == "reject":
                # Проверка прав на отклонение заявки
                can_reject = self.config.PERMISSION_REJECT == 'all' or (self.config.PERMISSION_REJECT == 'admins' and user_id in self.config.ADMIN_IDS)
                
                if not can_reject:
                    await query.answer("У вас нет прав для отклонения заявок", show_alert=True)
                    return await self.view_request_details_by_id(update, context, request_id)
                
                # Запрашиваем обязательную причину отклонения
                context.user_data['rejecting_request_id'] = request_id
                
                # Проверяем, содержит ли сообщение документ
                if query.message.document:
                    # Если есть документ, отправляем новое сообщение
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="❗ Укажите причину отклонения заявки:\n(обязательное поле)",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                    )
                    # Удаляем старое сообщение с документом
                    await query.message.delete()
                else:
                    # Если документа нет, редактируем существующее сообщение
                    await query.edit_message_text(
                        "❗ Укажите причину отклонения заявки:\n(обязательное поле)",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                    )
                return ADDING_REJECTION_REASON

            elif action == "edit":
                # Проверка прав на редактирование заявки
                can_edit = self.config.PERMISSION_EDIT == 'all' or (self.config.PERMISSION_EDIT == 'admins' and user_id in self.config.ADMIN_IDS)
                
                if not can_edit:
                    await query.answer("У вас нет прав для редактирования заявок", show_alert=True)
                    return await self.view_request_details_by_id(update, context, request_id)
                
                context.user_data['editing_request_id'] = request_id
                keyboard = [
                    [InlineKeyboardButton("📝 Изменить сумму", callback_data=f"edit_amount_{request_id}")],
                    [InlineKeyboardButton("💱 Изменить валюту", callback_data=f"edit_currency_{request_id}")],
                    [InlineKeyboardButton("🏦 Изменить источник", callback_data=f"edit_source_{request_id}")],
                    [InlineKeyboardButton("📋 Изменить примечание", callback_data=f"edit_note_{request_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"view_{request_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Проверяем, содержит ли сообщение документ
                if query.message.document:
                    # Если есть документ, отправляем новое сообщение
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Выберите, что хотите изменить:",
                        reply_markup=reply_markup
                    )
                    # Удаляем старое сообщение с документом
                    await query.message.delete()
                else:
                    # Если документа нет, редактируем существующее сообщение
                    await query.edit_message_text(
                        "Выберите, что хотите изменить:",
                        reply_markup=reply_markup
                    )
                return EDITING_REQUEST

            elif action == "comment":
                # Комментарии можно добавлять всем пользователям, отдельная проверка прав не требуется
                context.user_data['commenting_request_id'] = request_id
                
                # Проверяем, содержит ли сообщение документ
                if query.message.document:
                    # Если есть документ, отправляем новое сообщение
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Введите ваш комментарий:",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                    )
                    # Удаляем старое сообщение с документом
                    await query.message.delete()
                else:
                    # Если документа нет, редактируем существующее сообщение
                    await query.edit_message_text(
                        "Введите ваш комментарий:",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                    )
                return ADDING_COMMENT

            return VIEWING_REQUEST_DETAILS

        except Exception as e:
            logger.error(f"Error in handle_request_action: {e}")
            if 'query' in locals():
                await self._handle_error(query, "handling request action")
            return VIEWING_REQUESTS

    async def handle_edit_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle edit choice selection."""
        try:
            query = update.callback_query
            await query.answer()

            # Обработка установки валюты
            if query.data.startswith("set_currency_"):
                _, _, currency_id, request_id = query.data.split('_')
                request_id = int(request_id)
                
                # Обновляем валюту в базе данных
                update_request(self.db_session, request_id, currency=currency_id)
                
                # Возвращаемся к просмотру деталей заявки
                return await self.view_request_details_by_id(update, context, request_id)

            action, field, request_id = query.data.split('_')
            request_id = int(request_id)
            request = get_request(self.db_session, request_id)

            if not request:
                await query.edit_message_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS

            if action == "view":
                return await self.view_request_details_by_id(update, context, request_id)

            context.user_data['editing_field'] = field

            # Получаем основной текст заявки для использования при редактировании любого поля
            request_text = f"Заявка №{request.id}\n"
            request_text += f"Проект: {self.config.PROJECTS.get(request.project, 'Undefined')}\n"
            request_text += f"Сумма: {request.amount}{self.config.CURRENCIES.get(request.currency, 'Undefined')}\n"
            request_text += f"Источник: {self.config.SOURCES.get(request.source, 'Undefined')}\n"
            
            # Разделяем примечание и периодичность
            note_text = None
            period_text = None
            date_text = None
            
            if request.note:
                lines = request.note.split('\n')
                for line in lines:
                    if line.startswith('Периодичность:'):
                        period_text = line.replace('Периодичность:', '').strip()
                    elif line.startswith('Дата/период:'):
                        date_text = line.replace('Дата/период:', '').strip()
                    elif line.strip():
                        note_text = line.strip()
            
            if period_text:
                request_text += f"Периодичность: {period_text}\n"
            if date_text:
                request_text += f"Дата/период: {date_text}\n"
            if note_text:
                request_text += f"Примечание: {note_text}\n"
                
            request_text += f"Статус: {self.status_emoji[request.status]} {request.status.value}\n"
            request_text += f"Создана: {request.created_at.strftime('%d.%m.%Y %H:%M')}\n"
                
            if field == "amount":
                await query.edit_message_text(
                    f"{request_text}\n"
                    f"Текущая сумма: {request.amount}\n\n"
                    "Введите новую сумму:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                )
            elif field == "currency":
                keyboard = []
                for currency_id, currency in self.config.CURRENCIES.items():
                    symbol = self.config.CURRENCY_SYMBOLS.get(currency_id, self.config.CURRENCY_SYMBOLS['DEFAULT'])
                    button_text = f"{symbol} {currency}"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_currency_{currency_id}_{request_id}")])
                keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                # Проверяем, содержит ли сообщение документ
                if query.message.document:
                    # Если есть документ, отправляем новое сообщение
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=f"{request_text}\n"
                             f"Текущая валюта: {self.config.CURRENCIES.get(request.currency, 'Undefined')} ({request.currency})\n\n"
                             "Выберите новую валюту:",
                        reply_markup=reply_markup
                    )
                    # Удаляем старое сообщение с документом
                    await query.message.delete()
                else:
                    # Если документа нет, редактируем существующее сообщение
                    await query.edit_message_text(
                        f"{request_text}\n"
                        f"Текущая валюта: {self.config.CURRENCIES.get(request.currency, 'Undefined')} ({request.currency})\n\n"
                        "Выберите новую валюту:",
                        reply_markup=reply_markup
                    )
            elif field == "source":
                keyboard = []
                for source_id, source in self.config.SOURCES.items():
                    if 'crypto' in source_id.lower():
                        button_text = f"₿ {source}"
                    elif 'bank' in source_id.lower():
                        button_text = f"🏦 {source}"
                    else:
                        button_text = f"💰 {source}"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=f"set_source_{source_id}_{request_id}")])
                keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"{request_text}\n"
                    f"Текущий источник: {self.config.SOURCES.get(request.source, 'Undefined')}\n\n"
                    "Выберите новый источник:",
                    reply_markup=reply_markup
                )
            elif field == "note":
                # Сохраняем текущие значения периодичности и даты в контексте
                context.user_data['current_period'] = period_text
                context.user_data['current_date'] = date_text
                
                await query.edit_message_text(
                    f"{request_text}\n"
                    f"Текущее примечание: {note_text or 'Нет примечания'}\n\n"
                    "Введите новое примечание:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                )

            return EDITING_REQUEST

        except Exception as e:
            logger.error(f"Error in handle_edit_choice: {e}")
            if 'query' in locals():
                await self._handle_error(query, "edit choice selection")
            request_id = context.user_data.get('editing_request_id')
            if request_id:
                return await self.view_request_details_by_id(update, context, request_id)
            return VIEWING_REQUESTS

    async def handle_edit_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        request_id = context.user_data.get('editing_request_id')
        if query.data.startswith("set_"):
            source_id = query.data.replace("set_", "")
            update_request(self.db_session, request_id, source=source_id)
            await query.edit_message_text(f"Источник успешно изменен на: {self.config.SOURCES[source_id]}")
            return await self.view_request_details_by_id(update, context, request_id)
        elif query.data.startswith("view_"):
            return await self.view_request_details_by_id(update, context, request_id)
        else:
            await query.edit_message_text("Некорректный выбор источника.")
            return EDITING_SOURCE
    
    async def handle_edit_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle edit input."""
        try:
            request_id = context.user_data.get('editing_request_id')
            field = context.user_data.get('editing_field')
            
            if not request_id or not field:
                await update.message.reply_text(
                    "❌ Произошла ошибка при редактировании заявки.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            request = get_request(self.db_session, request_id)
            if not request:
                await update.message.reply_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            value = update.message.text.strip()
            
            try:
                # Validate based on field type
                if field == 'amount':
                    validated_value = validate_amount(value)
                    update_request(self.db_session, request_id, amount=validated_value)
                elif field == 'partner_account':
                    validated_value = validate_partner_account(value)
                    update_request(self.db_session, request_id, partner_account=validated_value)
                elif field == 'note':
                    validated_value = validate_note(value)
                    # Сохраняем периодичность и дату при обновлении примечания
                    note_parts = []
                    if context.user_data.get('current_period'):
                        note_parts.append(f"Периодичность: {context.user_data['current_period']}")
                    if context.user_data.get('current_date'):
                        note_parts.append(f"Дата/период: {context.user_data['current_date']}")
                    if validated_value:
                        note_parts.append(validated_value)
                    final_note = "\n".join(note_parts)
                    update_request(self.db_session, request_id, note=final_note)
                elif field == 'period':
                    validated_value = validate_period(value)
                    update_request(self.db_session, request_id, period=validated_value)
                elif field == 'date':
                    validated_value = validate_date(value)
                    update_request(self.db_session, request_id, date=validated_value)
                else:
                    validated_value = validate_edit_value(value)
                    update_request(self.db_session, request_id, **{field: validated_value})
                
                await update.message.reply_text(
                    "✅ Изменения сохранены.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
                )
                return VIEWING_REQUEST_DETAILS
                
            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return EDITING_REQUEST
            
        except Exception as e:
            logger.error(f"Error in handle_edit_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при сохранении изменений.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

    async def handle_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle comment input."""
        try:
            request_id = context.user_data.get('commenting_request_id')
            
            if not request_id:
                await update.message.reply_text(
                    "❌ Произошла ошибка при добавлении комментария.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            comment_text = update.message.text.strip()
            
            try:
                comment = validate_comment(comment_text)
                # Получаем пользователя для получения его id
                user = self.db_session.query(User).filter_by(telegram_id=update.effective_user.id).first()
                if not user:
                    await update.message.reply_text(
                        "❌ Ошибка: пользователь не найден в базе данных.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                    )
                    return VIEWING_REQUESTS
                
                # Добавляем комментарий с правильными id
                add_comment(
                    self.db_session,
                    request_id,
                    user.id,  # id из таблицы users
                    comment,
                    telegram_id=update.effective_user.id  # telegram_id пользователя
                )
                
                await update.message.reply_text(
                    f"✅ Комментарий к заявке #{request_id} добавлен.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
                )
                
                return VIEWING_REQUEST_DETAILS
                
            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return ADDING_COMMENT
            
        except Exception as e:
            logger.error(f"Error in handle_comment: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при добавлении комментария.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

    async def handle_rejection_reason(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle rejection reason input."""
        try:
            request_id = context.user_data.get('rejecting_request_id')
            
            if not request_id:
                await update.message.reply_text(
                    "❌ Произошла ошибка при отклонении заявки.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            reason_text = update.message.text.strip()
            
            try:
                reason = validate_rejection_reason(reason_text)
                update_request_status(self.db_session, request_id, RequestStatus.REJECTED, reason)
                
                # Log status change
                self._log_status_change(
                    request_id,
                    update.effective_user.id,
                    RequestStatus.PENDING,
                    RequestStatus.REJECTED,
                    reason
                )
                
                await update.message.reply_text(
                    f"✅ Заявка #{request_id} отклонена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
                )
                
                return VIEWING_REQUEST_DETAILS
                
            except ValueError as e:
                await update.message.reply_text(
                    f"❌ {str(e)}"
                )
                return ADDING_REJECTION_REASON
            
        except Exception as e:
            logger.error(f"Error in handle_rejection_reason: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при отклонении заявки.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

    def _log_status_change(self, request_id: int, user_id: int, old_status: RequestStatus, new_status: RequestStatus, reason: str = None):
        """Логирование изменения статуса заявки."""
        try:
            timestamp = datetime.now()
            log_entry = {
                'timestamp': timestamp.isoformat(),
                'request_id': request_id,
                'user_id': user_id,
                'action': 'status_change',
                'old_status': old_status.value,
                'new_status': new_status.value,
                'reason': reason
            }
            
            with open('logDB.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                
        except Exception as e:
            logger.error(f"Error logging status change: {e}")

    def _log_comment(self, request_id: int, user_id: int, comment_text: str):
        """Логирование добавления комментария."""
        try:
            timestamp = datetime.now()
            log_entry = {
                'timestamp': timestamp.isoformat(),
                'request_id': request_id,
                'user_id': user_id,
                'action': 'comment',
                'comment': comment_text
            }
            
            with open('logDB.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                
        except Exception as e:
            logger.error(f"Error logging comment: {e}")

    def _log_request_creation(self, request_id: int, user_id: int, request_data: dict):
        """Логирование создания заявки."""
        try:
            timestamp = datetime.now()
            log_entry = {
                'timestamp': timestamp.isoformat(),
                'request_id': request_id,
                'user_id': user_id,
                'action': 'create',
                'request_data': request_data
            }
            
            with open('logDB.log', 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                
        except Exception as e:
            logger.error(f"Error logging request creation: {e}")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        logger.error(f"Update {update} caused error: {context.error}")

        try:
            # Send error message to user
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Произошла ошибка. Пожалуйста, попробуйте позже или обратитесь в техподдержку @butterglobe"
                )

            # Restart the conversation
            if update.callback_query:
                message = update.callback_query.message
                await message.reply_text("Для возобновления работы используйте /start")
            elif update.message:
                await update.message.reply_text("Для возобновления работы используйте /start")
        except Exception as e:
            logger.error(f"Error in error handler: {e}")

    async def _handle_error(self, query, action_type):
        """Handle errors in handlers."""
        try:
            await query.edit_message_text(
                f"❌ Произошла ошибка при {action_type}. Пожалуйста, попробуйте позже или обратитесь в техподдержку @butterglobe\n\n"
                "Для возобновления работы используйте команду /start",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]])
            )
        except Exception as e:
            logger.error(f"Error in error handler: {e}")

    async def view_my_requests(self, query, context) -> int:
        """Просмотр заявок пользователя."""
        try:
            user_id = query.from_user.id
            requests = get_requests(self.db_session, user_id=user_id)
            
            # Устанавливаем флаг, что пользователь просматривает свои заявки
            context.user_data['viewing_my_requests'] = True
            
            if not requests:
                keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
                await query.edit_message_text(
                    "У вас пока нет созданных заявок.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return ADMIN_MENU

            message = "📋 Ваши заявки:\n\n"
            keyboard = []

            for req in requests:
                emoji = self.status_emoji.get(req.status, "")
                user = self.db_session.query(User).filter_by(telegram_id=req.user_id).first()
                
                # Получаем название валюты и символ из словарей
                currency_name = self.config.CURRENCIES.get(req.currency, req.currency)
                currency_symbol = self.config.CURRENCY_SYMBOLS.get(req.currency, self.config.CURRENCY_SYMBOLS['DEFAULT'])
                
                message += f"{emoji} #{req.id} - {self.config.PROJECTS[req.project]} - "
                message += f"{req.amount} {currency_symbol} {currency_name} - {self._format_user_info(user)}\n"
                message += f"Создано: {(req.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')}  "
                message += f"Изменено: {(req.updated_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')}\n\n"
                
                keyboard.append([InlineKeyboardButton(
                    f"{self.status_emoji.get(req.status, '')} #{req.id} - {req.amount} {currency_symbol} {currency_name}",
                    callback_data=f"request_{req.id}"
                )])

            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(message, reply_markup=reply_markup)
            return VIEWING_REQUESTS

        except Exception as e:
            logger.error(f"Error in view_my_requests: {e}")
            if 'query' in locals():
                await self._handle_error(query, "viewing my requests")
            return ADMIN_MENU

    async def handle_export_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle data export functionality."""
        try:
            query = update.callback_query
            await query.answer()

            # Проверяем права доступа
            user_id = update.effective_user.id
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                await query.answer("У вас нет прав для экспорта данных", show_alert=True)
                return ADMIN_MENU

            keyboard = [
                [InlineKeyboardButton("📊 Экспорт по заявкам", callback_data="export_requests")],
                [InlineKeyboardButton("📈 Экспорт по пользователям", callback_data="export_users")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]

            await query.edit_message_text(
                "Выберите тип экспорта:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return EXPORT_MENU

        except Exception as e:
            logger.error(f"Error in handle_export_data: {e}")
            await self._handle_error(query, "export data")
            return ADMIN_MENU

    async def handle_export_format(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle export format selection."""
        try:
            query = update.callback_query
            await query.answer()

            # Проверяем права доступа
            user_id = update.effective_user.id
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                await query.answer("У вас нет прав для экспорта данных", show_alert=True)
                return ADMIN_MENU

            export_type = query.data.split('_')[1]  # requests or users
            context.user_data['export_type'] = export_type

            keyboard = [
                [InlineKeyboardButton("📄 Excel", callback_data=f"export_{export_type}_excel")],
                [InlineKeyboardButton("📄 CSV", callback_data=f"export_{export_type}_csv")],
                [InlineKeyboardButton("🔙 Назад", callback_data="export_data")]
            ]

            await query.edit_message_text(
                "Выберите формат экспорта:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return EXPORT_FORMAT

        except Exception as e:
            logger.error(f"Error in handle_export_format: {e}")
            await self._handle_error(query, "export format")
            return ADMIN_MENU

    async def process_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Process the actual export."""
        try:
            query = update.callback_query
            await query.answer()

            # Проверяем права доступа
            user_id = update.effective_user.id
            if not (user_id in self.config.ADMIN_IDS or 
                   user_id in self.config.FINCONTROL_IDS or 
                   user_id in self.config.ALL_ACCESS_ADMIN_IDS):
                await query.answer("У вас нет прав для экспорта данных", show_alert=True)
                return ADMIN_MENU

            export_type = context.user_data.get('export_type')
            format_type = query.data.split('_')[-1]  # excel or csv

            # Show loading message
            loading_message = await query.edit_message_text(
                "⏳ Подготовка данных для экспорта...\nЭто может занять некоторое время."
            )

            # Create temporary directory for export files
            temp_dir = tempfile.mkdtemp()
            try:
                if export_type == 'requests':
                    # Получаем заявки с учетом прав доступа
                    requests, _ = self._get_requests_for_user(user_id, page_size=1000)  # Большой размер страницы для получения всех данных
                    data = []
                    for req in requests:
                        req_dict = {
                            'ID': req.id,
                            'Проект': self.config.PROJECTS.get(req.project, req.project),
                            'Сумма': req.amount,
                            'Валюта': self.config.CURRENCIES.get(req.currency, req.currency),
                            'Источник': self.config.SOURCES.get(req.source, req.source),
                            'Статус': self.STATUS_DISPLAY.get(req.status.value, req.status.value),
                            'Создано': (req.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M'),
                            'Изменено': (req.updated_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')
                        }
                        if req.note:
                            req_dict['Примечание'] = req.note
                        if req.partner_account:
                            req_dict['Счет партнера'] = req.partner_account
                        data.append(req_dict)
                else:  # users
                    users = self.db_session.query(User).all()
                    data = []
                    for user in users:
                        user_dict = {
                            'ID': user.id,
                            'Telegram ID': user.telegram_id,
                            'Username': user.username,
                            'First Name': user.first_name,
                            'Created At': (user.created_at + timedelta(hours=timeDelta)).strftime('%d/%m/%Y %H:%M')
                        }
                        data.append(user_dict)

                if format_type == 'excel':
                    file_path = os.path.join(temp_dir, f"export_{export_type}.xlsx")
                    df = pd.DataFrame(data)
                    df.to_excel(file_path, index=False)
                else:  # csv
                    file_path = os.path.join(temp_dir, f"export_{export_type}.csv")
                    df = pd.DataFrame(data)
                    df.to_csv(file_path, index=False, encoding='utf-8-sig')

                # Send file
                with open(file_path, 'rb') as file:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=file,
                        filename=os.path.basename(file_path)
                    )

                # Update message
                await loading_message.edit_text(
                    "✅ Экспорт успешно завершен!\nФайл отправлен в чат.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад", callback_data="export_data")
                    ]])
                )

            finally:
                # Cleanup temporary files
                shutil.rmtree(temp_dir, ignore_errors=True)

            return EXPORT_MENU

        except Exception as e:
            logger.error(f"Error in process_export: {e}")
            await self._handle_error(query, "export process")
            return ADMIN_MENU

    def _format_user_info(self, user):
        """Optimized user info formatting with caching."""
        if not user:
            return "Неизвестный пользователь"
            
        cache_key = f"user_info_{user.id}"
        if cache_key in self._user_cache:
            cache_time, info = self._user_cache[cache_key]
            if (datetime.now() - cache_time).total_seconds() < self._cache_timeout:
                return info
        
        if hasattr(user, 'first_name') and user.first_name:
            info = user.first_name
        elif hasattr(user, 'username') and user.username:
            info = f"@{user.username}"
        elif hasattr(user, 'telegram_id'):
            info = f"user_{user.telegram_id}"
        else:
            info = f"user_{user.id}"
            
        self._user_cache[cache_key] = (datetime.now(), info)
        return info

    def _get_filter_keyboard(self) -> list:
        """Generate filter keyboard for request viewing."""
        return [
            [InlineKeyboardButton("⏳ Ожидают подтверждения", callback_data="filter_pending")],
            [InlineKeyboardButton("💰 Одобрено/Ожидают оплаты", callback_data="filter_waiting")],
            [InlineKeyboardButton("✅ Оплаченные", callback_data="filter_paid")],
            [InlineKeyboardButton("❌ Отклоненные", callback_data="filter_rejected")],
            [InlineKeyboardButton("📋 Все заявки", callback_data="filter_all")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]

class Bot:
    """Main bot class."""
    def __init__(self):
        # Initialize database
        self.db_session = init_db()

        # Initialize config
        self.config = BotConfig()

        # Initialize handlers
        self.handlers = BotHandlers(self.config, self.db_session)

    def run(self):
        """Start the bot."""
        try:
            application = Application.builder().token(self.config.TELEGRAM_TOKEN).build()

            # Add conversation handler
            conv_handler = ConversationHandler(
                entry_points=[CommandHandler('start', self.handlers.start)],
                states={
                    ADMIN_MENU: [
                        CallbackQueryHandler(self.handlers.handle_admin_callback)
                    ],
                    CHOOSING_PROJECT: [
                        CallbackQueryHandler(self.handlers.project_selected)
                    ],
                    ENTERING_AMOUNT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.amount_entered)
                    ],
                    CHOOSING_CURRENCY: [
                        CallbackQueryHandler(self.handlers.currency_selected)
                    ],
                    CHOOSING_SOURCE: [
                        CallbackQueryHandler(self.handlers.source_selected)
                    ],
                    ATTACHING_DOCUMENT: [
                        CallbackQueryHandler(self.handlers.handle_document, pattern="^attach$"),
                        CallbackQueryHandler(self.handlers.handle_partner_account, pattern="^partner$"),
                        CallbackQueryHandler(self.handlers.handle_document, pattern="^skip$"),
                        MessageHandler(filters.Document.ALL | filters.PHOTO, self.handlers.handle_document)
                    ],
                    ENTERING_PARTNER_ACCOUNT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_partner_account_input)
                    ],
                    ENTERING_NOTE: [
                        CallbackQueryHandler(self.handlers.handle_note),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_custom_note)
                    ],
                    CHOOSING_PERIOD: [
                        CallbackQueryHandler(self.handlers.handle_period),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_period_input)
                    ],
                    CHOOSING_DATE: [
                        CallbackQueryHandler(self.handlers.handle_date, pattern="^date_"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_date)
                    ],
                    CONFIRMING_REQUEST: [
                        CallbackQueryHandler(self.handlers.confirm_request)
                    ],
                    VIEWING_REQUESTS: [
                        CallbackQueryHandler(self.handlers.handle_request_navigation),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_request_id_input)
                    ],
                    VIEWING_REQUEST_DETAILS: [
                        CallbackQueryHandler(self.handlers.handle_request_action, pattern="^(approve|reject|edit|comment)_"),
                        CallbackQueryHandler(self.handlers.handle_request_navigation, pattern="^back_to_list$"),
                        CallbackQueryHandler(self.handlers.view_request_details_by_id, pattern="^view_")
                    ],
                    EDITING_REQUEST: [
                        CallbackQueryHandler(self.handlers.handle_edit_choice, pattern="^(edit_|view_)"),
                        CallbackQueryHandler(self.handlers.handle_edit_choice, pattern="^set_currency_"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_edit_input)
                    ],
                    EDITING_SOURCE: [
                        CallbackQueryHandler(self.handlers.handle_edit_source)
                    ],
                    ADDING_COMMENT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_comment)
                    ],
                    ADDING_REJECTION_REASON: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_rejection_reason)
                    ],
                    EXPORTING_DATA: [
                        CallbackQueryHandler(self.handlers.handle_export_format)
                    ]
                },
                fallbacks=[CommandHandler('start', self.handlers.start)],
                per_message=False,
                per_chat=True
            )

            application.add_handler(conv_handler)
            application.add_error_handler(self.handlers.error_handler)

            logger.info("Starting bot...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Error in main: {e}")
            raise

if __name__ == '__main__':
    bot = Bot()
    bot.run()
