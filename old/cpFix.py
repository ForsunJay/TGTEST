import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import BadRequest
from database import (
    init_db, get_or_create_user, create_request, update_request_status,
    add_comment, RequestStatus, get_requests, get_request, get_request_comments,
    update_request, User
)

# Load environment variables
load_dotenv('bot.env')

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
    ADDING_COMMENT
) = range(15)

class BotConfig:
    """Configuration class for the bot."""
    def __init__(self):
        self.TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
        if not self.TELEGRAM_TOKEN:
            raise ValueError("No TELEGRAM_TOKEN found in environment variables!")
        
        self.ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS', '').split(',') if admin_id]
        self.ALLOW_ADMIN_CREATE = os.getenv('ALLOW_ADMIN_CREATE', 'false').lower() == 'true'
        
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
        
        self.SOURCES = {
            'rs_rf': 'РС РФ Сервис+ Точкабанк',
            'rs_too_kz': 'РС ТОО КЗ',
            'rs_ip_kz': 'РС ИП КЗ',
            'card_too_kz': 'Карта ТОО КЗ',
            'card_ip_kz': 'Карта ИП КЗ',
            'rs_ooo_am': 'РС ООО АМ',
            'rs_ooo_am_eur': 'РС ООО АМ EUR',
            'card_ooo_am': 'Карта ООО АМ',
            'crypto': 'Крипта',
            'cash': 'Наличные'
        }
        
        self.NOTES = [
            'Реклама',
            'Сопровождение РК',
            'Ком-ции. СМС',
            'Ком-ции. АВТОДОЗВОНЫ',
            'Ком-ции. РАССЫЛКИ',
            'Ком-ции. ТЕЛЕФОНИЯ',
            'Ком-ции. ОНЛАЙН'
        ]

class BotHandlers:
    """Class containing all bot handlers."""
    def __init__(self, config: BotConfig, db_session):
        self.config = config
        self.db_session = db_session
        self.status_emoji = {
            RequestStatus.PENDING: "⏳",
            RequestStatus.WAITING_PAYMENT: "💰",
            RequestStatus.PAID: "✅",
            RequestStatus.REJECTED: "❌"
        }

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start the conversation and ask for project selection."""
        try:
            user = get_or_create_user(
                self.db_session,
                update.effective_user.id,
                update.effective_user.username
            )
            
            if update.effective_user.id in self.config.ADMIN_IDS:
                keyboard = [
                    [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
                    [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
                    [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "👋 Добро пожаловать в панель администратора! Выберите действие:",
                    reply_markup=reply_markup
                )
                logger.info(f"Admin menu shown to user {update.effective_user.id}")
                return ADMIN_MENU
            
            keyboard = [
                [InlineKeyboardButton(project, callback_data=project_id)]
                for project_id, project in self.config.PROJECTS.items()
            ]
            keyboard.append([InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "👋 Добро пожаловать! Выберите проект:",
                reply_markup=reply_markup
            )
            return CHOOSING_PROJECT
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
            elif query.data == "back_to_menu":
                return await self.back_to_admin_menu(query, context)
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
        context.user_data['request_filter'] = None
        context.user_data['request_page'] = 0
        
        keyboard = [
            [InlineKeyboardButton("⏳ Ожидающие", callback_data="filter_pending")],
            [InlineKeyboardButton("💰 Ожидают оплаты", callback_data="filter_waiting_payment")],
            [InlineKeyboardButton("✅ Оплаченные", callback_data="filter_paid")],
            [InlineKeyboardButton("❌ Отклоненные", callback_data="filter_rejected")],
            [InlineKeyboardButton("📋 Все заявки", callback_data="filter_all")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        logger.info("Showing request filter keyboard")
        await query.edit_message_text(
            "📋 Выберите статус заявок для просмотра:",
            reply_markup=reply_markup
        )
        return VIEWING_REQUESTS

    async def back_to_admin_menu(self, query, context) -> int:
        """Возврат в главное меню администратора."""
        keyboard = [
            [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
            [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
            [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👋 Панель администратора! Выберите действие:",
            reply_markup=reply_markup
        )
        logger.info(f"Returned to admin menu")
        return ADMIN_MENU

    async def project_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle project selection."""
        try:
            query = update.callback_query
            await query.answer()
            
            # Обработка кнопки "Назад"
            if query.data == "back_to_menu":
                if update.effective_user.id in self.config.ADMIN_IDS:
                    return await self.back_to_admin_menu(query, context)
                else:
                    # Для обычных пользователей - перезапуск диалога
                    await query.edit_message_text("👋 Добро пожаловать! Для начала работы используйте /start")
                    return ConversationHandler.END
            
            project_id = query.data
            context.user_data['project'] = project_id
            
            # Create keyboard for currency selection
            keyboard = [
                [InlineKeyboardButton(currency, callback_data=currency_id)]
                for currency_id, currency in self.config.CURRENCIES.items()
            ]
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
            context.user_data['currency'] = currency_id
            
            # Ask for amount
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
                amount = float(amount_text)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
                
                context.user_data['amount'] = amount
                
                # Create keyboard for source selection
                keyboard = [
                    [InlineKeyboardButton(source, callback_data=source_id)]
                    for source_id, source in self.config.SOURCES.items()
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"Сумма: {amount}\n\n"
                    "Выберите источник средств:",
                    reply_markup=reply_markup
                )
                return CHOOSING_SOURCE
                
            except ValueError:
                await update.message.reply_text(
                    "❌ Пожалуйста, введите корректную сумму (только цифры и точка для дробной части)."
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
            
            # Create keyboard for document attachment
            keyboard = [
                [InlineKeyboardButton("📎 Прикрепить документ", callback_data="attach")],
                [InlineKeyboardButton("💳 Указать счет партнера", callback_data="partner")],
                [InlineKeyboardButton("⏩ Пропустить", callback_data="skip")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Выбран источник: {self.config.SOURCES[source_id]}\n\n"
                "Выберите действие:",
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
            elif update.message.photo:
                file = update.message.photo[-1]  # Get the largest photo
            else:
                await update.message.reply_text(
                    "❌ Пожалуйста, отправьте документ или фото.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
                )
                return ATTACHING_DOCUMENT
            
            # Save file info in context
            context.user_data['document'] = {
                'file_id': file.file_id,
                'file_name': getattr(file, 'file_name', 'photo.jpg')
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
            note = update.message.text.strip()
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
            
            await update.message.reply_text(
                f"Примечание: {note}\n\n"
                "Выберите периодичность:",
                reply_markup=reply_markup
            )
            return CHOOSING_PERIOD
            
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
            
            # Ask for date
            await query.edit_message_text(
                f"Периодичность: {period}\n\n"
                "Укажите дату или период в формате ДД.ММ.ГГГГ или ДД.ММ.ГГГГ - ДД.ММ.ГГГГ:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
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
            period = update.message.text.strip()
            context.user_data['period'] = period
            
            # Ask for date
            await update.message.reply_text(
                f"Периодичность: {period}\n\n"
                "Укажите дату или период в формате ДД.ММ.ГГГГ или ДД.ММ.ГГГГ - ДД.ММ.ГГГГ:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
            )
            return CHOOSING_DATE
            
        except Exception as e:
            logger.error(f"Error in handle_period_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке периодичности. Пожалуйста, попробуйте еще раз."
            )
            return CHOOSING_PERIOD

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle date input."""
        try:
            date_text = update.message.text.strip()
            context.user_data['date'] = date_text
            
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
                
            if 'document' in context.user_data:
                summary += f"Документ: {context.user_data['document']['file_name']}\n"
                
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
            
        except Exception as e:
            logger.error(f"Error in handle_date: {e}")
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
            
            # Create request in database
            request_data = {
                'user_id': update.effective_user.id,
                'project': context.user_data.get('project', ''),
                'amount': context.user_data.get('amount', 0),
                'currency': context.user_data.get('currency', ''),
                'source': context.user_data.get('source', ''),
                'note': context.user_data.get('note', ''),
                'period': context.user_data.get('period', ''),
                'date': context.user_data.get('date', ''),
                'partner_account': context.user_data.get('partner_account', None),
                'document_id': context.user_data.get('document', {}).get('file_id', None),
                'document_name': context.user_data.get('document', {}).get('file_name', None)
            }
            
            request = create_request(self.db_session, **request_data)
            
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

    async def _handle_filter(self, query, context):
        """Handle filter selection."""
        try:
            filter_type = query.data.split('_')[1]
            context.user_data['request_filter'] = filter_type
            context.user_data['request_page'] = 0
            
            logger.info(f"Filtering requests by: {filter_type}")
            
            status_filter = None
            if filter_type != "all":
                status_filter = getattr(RequestStatus, filter_type.upper(), None)
            
            limit = 5
            offset = 0
            
            # Получаем заявки
            requests = get_requests(self.db_session, status=status_filter, limit=limit, offset=offset)
            # Сортируем по возрастанию ID (если есть заявки)
            if requests:
                requests = sorted(requests, key=lambda x: x.id)
                
            message, keyboard = self._create_request_list_message(requests, context)
            
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
            page = context.user_data.get('request_page', 0)
            
            if query.data == "next_page":
                page += 1
            elif query.data == "prev_page" and page > 0:
                page -= 1
                
            context.user_data['request_page'] = page
            
            limit = 5
            offset = page * limit
            
            filter_type = context.user_data.get('request_filter')
            status_filter = None
            if filter_type and filter_type != "all":
                status_filter = getattr(RequestStatus, filter_type.upper(), None)
            
            # Получаем заявки
            requests = get_requests(self.db_session, status=status_filter, limit=limit, offset=offset)
            # Сортируем по возрастанию ID (если есть заявки)
            if requests:
                requests = sorted(requests, key=lambda x: x.id)
                
            message, keyboard = self._create_request_list_message(requests, context)
            
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

    def _create_request_list_message(self, requests, context):
        """Create message and keyboard for request list."""
        if not requests:
            message = "📋 Заявки не найдены."
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]]
            return message, keyboard
        
        filter_type = context.user_data.get('request_filter', 'all')
        filter_name = {
            'pending': '⏳ Ожидающие',
            'waiting_payment': '💰 Ожидают оплаты',
            'paid': '✅ Оплаченные',
            'rejected': '❌ Отклоненные',
            'all': '📋 Все заявки'
        }.get(filter_type, '📋 Заявки')
        
        message = f"{filter_name}:\n\n"
        
        for req in requests:
            emoji = self.status_emoji.get(req.status, "")
            message += f"{emoji} #{req.id} - {self.config.PROJECTS[req.project]} - "
            message += f"{req.amount} {self.config.CURRENCIES[req.currency]} - "
            message += f"{req.created_at.strftime('%d/%m/%Y')}\n"
        
        keyboard = []
        for req in requests:
            keyboard.append([InlineKeyboardButton(
                f"{self.status_emoji.get(req.status, '')} #{req.id} - {req.amount} {req.currency}",
                callback_data=f"request_{req.id}"
            )])
        
        page = context.user_data.get('request_page', 0)
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Пред.", callback_data="prev_page"))
        
        if len(requests) == 5:  # If we have a full page, assume there might be more
            nav_buttons.append(InlineKeyboardButton("След. ➡️", callback_data="next_page"))
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
        
        return message, keyboard

    async def view_request_details_by_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int) -> int:
        """Просмотр деталей заявки по ID."""
        try:
            query = update.callback_query
            await query.answer()
            
            request = get_request(self.db_session, request_id)
            
            if not request:
                await query.edit_message_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            user = self.db_session.query(User).filter_by(id=request.user_id).first()
            emoji = self.status_emoji.get(request.status, "")
            
            message = f"{emoji} Заявка #{request.id}\n\n"
            message += f"Проект: {self.config.PROJECTS[request.project]}\n"
            message += f"Сумма: {request.amount} {self.config.CURRENCIES[request.currency]}\n"
            message += f"Источник: {self.config.SOURCES[request.source]}\n"
            
            # Безопасно обрабатываем информацию о пользователе
            user_info = "Неизвестный пользователь"
            if user:
                user_info = user.username or f'user_{user.telegram_id}' if hasattr(user, 'telegram_id') else f'user_{user.id}'
                
            message += f"От: {user_info}\n"
            message += f"Статус: {request.status.value}\n"
            message += f"Дата: {request.created_at.strftime('%d/%m/%Y %H:%M')}\n"
            
            if request.note:
                message += f"\nПримечание: {request.note}\n"
            
            # Get comments
            comments = get_request_comments(self.db_session, request_id)
            if comments:
                message += "\nКомментарии:\n"
                for comment in comments:
                    # Безопасно обрабатываем информацию о пользователе, оставившем комментарий
                    comment_user = self.db_session.query(User).filter_by(id=comment.user_id).first()
                    commenter_info = "Неизвестный пользователь"
                    if comment_user:
                        commenter_info = comment_user.username or f'user_{comment_user.telegram_id}' if hasattr(comment_user, 'telegram_id') else f'user_{comment_user.id}'
                    
                    message += f"- {commenter_info}: {comment.text}\n"
            
            keyboard = []
            
            # Add action buttons based on request status
            if request.status == RequestStatus.PENDING:
                keyboard.extend([
                    [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{request_id}")],
                    [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{request_id}")]
                ])
            elif request.status == RequestStatus.WAITING_PAYMENT:
                keyboard.append([InlineKeyboardButton("✅ Отметить как оплаченную", callback_data=f"approve_{request_id}")])
            
            # Add common buttons
            keyboard.extend([
                [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{request_id}")],
                [InlineKeyboardButton("💬 Добавить комментарий", callback_data=f"comment_{request_id}")],
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup)
            return VIEWING_REQUEST_DETAILS
            
        except Exception as e:
            logger.error(f"Error in view_request_details_by_id: {e}")
            if 'query' in locals():
                await self._handle_error(query, "viewing request details")
            return VIEWING_REQUESTS

    async def view_request_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик просмотра деталей заявки (для совместимости)."""
        try:
            query = update.callback_query
            request_id = int(query.data.split('_')[1])
            return await self.view_request_details_by_id(update, context, request_id)
        except Exception as e:
            logger.error(f"Error in view_request_details: {e}")
            if 'query' in locals():
                await self._handle_error(query, "viewing request details")
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
            elif query.data == "back_to_menu":
                return await self._handle_back_to_menu(query)
            elif query.data == "back_to_list":
                # Reset filter and page
                context.user_data['request_filter'] = None
                context.user_data['request_page'] = 0
                
                # Get requests, sorted by ID
                requests = get_requests(self.db_session, limit=5, offset=0)
                # Сортируем по возрастанию ID (если есть заявки)
                if requests:
                    requests = sorted(requests, key=lambda x: x.id)
                    
                message, keyboard = self._create_request_list_message(requests, context)
                
                await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
                return VIEWING_REQUESTS
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
            
            if action == "approve":
                if request.status == RequestStatus.PENDING:
                    update_request_status(self.db_session, request_id, RequestStatus.WAITING_PAYMENT)
                    await query.edit_message_text(
                        "✅ Заявка одобрена и ожидает оплаты.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                    )
                elif request.status == RequestStatus.WAITING_PAYMENT:
                    update_request_status(self.db_session, request_id, RequestStatus.PAID)
                    await query.edit_message_text(
                        "✅ Заявка отмечена как оплаченная.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                    )
                return VIEWING_REQUESTS
                
            elif action == "reject":
                update_request_status(self.db_session, request_id, RequestStatus.REJECTED)
                await query.edit_message_text(
                    "❌ Заявка отклонена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
                
            elif action == "edit":
                context.user_data['editing_request_id'] = request_id
                keyboard = [
                    [InlineKeyboardButton("📝 Изменить сумму", callback_data=f"edit_amount_{request_id}")],
                    [InlineKeyboardButton("💱 Изменить валюту", callback_data=f"edit_currency_{request_id}")],
                    [InlineKeyboardButton("🏦 Изменить источник", callback_data=f"edit_source_{request_id}")],
                    [InlineKeyboardButton("📋 Изменить примечание", callback_data=f"edit_note_{request_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"view_{request_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "Выберите, что хотите изменить:",
                    reply_markup=reply_markup
                )
                return EDITING_REQUEST
                
            elif action == "comment":
                context.user_data['commenting_request_id'] = request_id
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
            
            if field == "amount":
                await query.edit_message_text(
                    f"Текущая сумма: {request.amount}\n\n"
                    "Введите новую сумму:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                )
            elif field == "currency":
                keyboard = [
                    [InlineKeyboardButton(currency, callback_data=f"set_{currency_id}")]
                    for currency_id, currency in self.config.CURRENCIES.items()
                ]
                keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"Текущая валюта: {self.config.CURRENCIES[request.currency]}\n\n"
                    "Выберите новую валюту:",
                    reply_markup=reply_markup
                )
                return EDITING_REQUEST
            elif field == "source":
                keyboard = [
                    [InlineKeyboardButton(source, callback_data=f"set_{source_id}")]
                    for source_id, source in self.config.SOURCES.items()
                ]
                keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"Текущий источник: {self.config.SOURCES[request.source]}\n\n"
                    "Выберите новый источник:",
                    reply_markup=reply_markup
                )
                return EDITING_REQUEST
            elif field == "note":
                await query.edit_message_text(
                    f"Текущее примечание: {request.note or 'Нет примечания'}\n\n"
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
            
            if field == "amount":
                try:
                    new_amount = float(value)
                    if new_amount <= 0:
                        raise ValueError("Amount must be positive")
                    
                    update_request(self.db_session, request_id, amount=new_amount)
                    
                    await update.message.reply_text(
                        f"✅ Сумма заявки #{request_id} изменена на {new_amount}.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
                    )
                except ValueError:
                    await update.message.reply_text(
                        "❌ Некорректная сумма. Введите число больше нуля.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                    )
                    return EDITING_REQUEST
            elif field == "note":
                update_request(self.db_session, request_id, note=value)
                
                await update.message.reply_text(
                    f"✅ Примечание заявки #{request_id} изменено.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
                )
            
            return VIEWING_REQUEST_DETAILS
            
        except Exception as e:
            logger.error(f"Error in handle_edit_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при редактировании заявки.",
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
            
            add_comment(self.db_session, request_id, update.effective_user.id, comment_text)
            
            await update.message.reply_text(
                f"✅ Комментарий к заявке #{request_id} добавлен.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Детали заявки", callback_data=f"view_{request_id}")]])
            )
            
            return VIEWING_REQUEST_DETAILS
            
        except Exception as e:
            logger.error(f"Error in handle_comment: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при добавлении комментария.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

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
                f"❌ Произошла ошибка при {action_type}. Пожалуйста, попробуйте позже или обратитесь в техподдержку @butterglobe",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Перезапустить", callback_data="restart")]])
            )
        except Exception as e:
            logger.error(f"Error in error handler: {e}")

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
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_date)
                    ],
                    CONFIRMING_REQUEST: [
                        CallbackQueryHandler(self.handlers.confirm_request)
                    ],
                    VIEWING_REQUESTS: [
                        CallbackQueryHandler(self.handlers.handle_request_navigation)
                    ],
                    VIEWING_REQUEST_DETAILS: [
                        CallbackQueryHandler(self.handlers.handle_request_action, pattern="^(approve|reject|edit|comment)_"),
                        CallbackQueryHandler(self.handlers.handle_request_navigation, pattern="^back_to_list$"),
                        CallbackQueryHandler(self.handlers.view_request_details, pattern="^view_")
                    ],
                    EDITING_REQUEST: [
                        CallbackQueryHandler(self.handlers.handle_edit_choice, pattern="^(edit_|view_)"),
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_edit_input)
                    ],
                    ADDING_COMMENT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_comment)
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