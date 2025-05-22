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
            
            if update.effective_user.id in self.config.ADMIN_IDS and self.config.ALLOW_ADMIN_CREATE:
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

    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle admin menu selection."""
        query = update.callback_query
        await query.answer()
        
        logger.info(f"Admin menu action: {query.data}")
        
        if query.data == "create_request":
            keyboard = [
                [InlineKeyboardButton(project, callback_data=project_id)]
                for project_id, project in self.config.PROJECTS.items()
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "📝 Выберите проект:",
                reply_markup=reply_markup
            )
            return CHOOSING_PROJECT
        elif query.data == "view_requests":
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

    async def project_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle project selection."""
        try:
            query = update.callback_query
            await query.answer()
            
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
            await self._handle_error(query, "project selection")
            return ConversationHandler.END

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
                
                # Get requests
                requests = get_requests(self.db_session, limit=5, offset=0)
                message, keyboard = self._create_request_list_message(requests, context)
                
                await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
                return VIEWING_REQUESTS
            
            return VIEWING_REQUESTS
                
        except Exception as e:
            logger.error(f"Error in handle_request_navigation: {e}")
            await self._handle_error(query, "navigation")
            return ConversationHandler.END

    async def view_request_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle viewing request details."""
        try:
            query = update.callback_query
            await query.answer()
            
            request_id = int(query.data.split('_')[1])
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
            message += f"От: {user.username or f'user_{user.telegram_id}'}\n"
            message += f"Статус: {request.status.value}\n"
            message += f"Дата: {request.created_at.strftime('%d/%m/%Y %H:%M')}\n"
            
            if request.note:
                message += f"\nПримечание: {request.note}\n"
            
            # Get comments
            comments = get_request_comments(self.db_session, request_id)
            if comments:
                message += "\nКомментарии:\n"
                for comment in comments:
                    comment_user = self.db_session.query(User).filter_by(id=comment.user_id).first()
                    message += f"- {comment_user.username or f'user_{comment_user.telegram_id}'}: {comment.text}\n"
            
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
            logger.error(f"Error in view_request_details: {e}")
            await self._handle_error(query, "viewing request details")
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
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                    )
                elif request.status == RequestStatus.WAITING_PAYMENT:
                    update_request_status(self.db_session, request_id, RequestStatus.PAID)
                    await query.edit_message_text(
                        "✅ Заявка отмечена как оплаченная.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                    )
                return VIEWING_REQUESTS
                
            elif action == "reject":
                update_request_status(self.db_session, request_id, RequestStatus.REJECTED)
                await query.edit_message_text(
                    "❌ Заявка отклонена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
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
            
            context.user_data['editing_field'] = field
            
            if field == "currency":
                keyboard = [
                    [InlineKeyboardButton(currency, callback_data=currency_id)]
                    for currency_id, currency in self.config.CURRENCIES.items()
                ]
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "Выберите новую валюту:",
                    reply_markup=reply_markup
                )
            elif field == "source":
                keyboard = [
                    [InlineKeyboardButton(source, callback_data=source_id)]
                    for source_id, source in self.config.SOURCES.items()
                ]
                keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=f"view_{request_id}")])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    "Выберите новый источник:",
                    reply_markup=reply_markup
                )
            else:
                await query.edit_message_text(
                    f"Введите новое значение для {field}:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data=f"view_{request_id}")]])
                )
            
            return EDITING_REQUEST
            
        except Exception as e:
            logger.error(f"Error in handle_edit_choice: {e}")
            await self._handle_error(query, "handling edit choice")
            return VIEWING_REQUESTS

    async def handle_edit_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle edit input."""
        try:
            request_id = context.user_data.get('editing_request_id')
            field = context.user_data.get('editing_field')
            
            if not request_id or not field:
                await update.message.reply_text(
                    "❌ Ошибка: не найдены данные для редактирования.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            request = get_request(self.db_session, request_id)
            if not request:
                await update.message.reply_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            new_value = update.message.text
            
            if field == "amount":
                try:
                    new_value = float(new_value)
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат суммы. Пожалуйста, введите число.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"view_{request_id}")]])
                    )
                    return EDITING_REQUEST
            
            update_request(self.db_session, request_id, {field: new_value})
            
            await update.message.reply_text(
                "✅ Изменения сохранены.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к заявке", callback_data=f"view_{request_id}")]])
            )
            return VIEWING_REQUEST_DETAILS
            
        except Exception as e:
            logger.error(f"Error in handle_edit_input: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при сохранении изменений.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

    async def handle_comment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle comment input."""
        try:
            request_id = context.user_data.get('commenting_request_id')
            if not request_id:
                await update.message.reply_text(
                    "❌ Ошибка: не найдена заявка для комментария.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            request = get_request(self.db_session, request_id)
            if not request:
                await update.message.reply_text(
                    "❌ Заявка не найдена.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
                )
                return VIEWING_REQUESTS
            
            comment_text = update.message.text
            add_comment(self.db_session, request_id, update.effective_user.id, comment_text)
            
            await update.message.reply_text(
                "✅ Комментарий добавлен.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к заявке", callback_data=f"view_{request_id}")]])
            )
            return VIEWING_REQUEST_DETAILS
            
        except Exception as e:
            logger.error(f"Error in handle_comment: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при добавлении комментария.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]])
            )
            return VIEWING_REQUESTS

    async def amount_entered(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle amount input."""
        try:
            amount_text = update.message.text.replace(',', '.')
            try:
                amount = float(amount_text)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
            except ValueError:
                await update.message.reply_text(
                    "❌ Пожалуйста, введите корректную сумму (положительное число).",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])
                )
                return ENTERING_AMOUNT
            
            context.user_data['amount'] = amount
            
            # Create keyboard for source selection
            keyboard = [
                [InlineKeyboardButton(source, callback_data=source_id)]
                for source_id, source in self.config.SOURCES.items()
            ]
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Сумма: {amount}\n\n"
                "Выберите источник:",
                reply_markup=reply_markup
            )
            return CHOOSING_SOURCE
            
        except Exception as e:
            logger.error(f"Error in amount_entered: {e}")
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке суммы.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]])
            )
            return ConversationHandler.END

    async def currency_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle currency selection."""
        try:
            query = update.callback_query
            await query.answer()
            
            currency_id = query.data
            context.user_data['currency'] = currency_id
            
            await query.edit_message_text(
                f"Выбрана валюта: {self.config.CURRENCIES[currency_id]}\n\n"
                "Введите сумму:"
            )
            return ENTERING_AMOUNT
            
        except Exception as e:
            logger.error(f"Error in currency_selected: {e}")
            await self._handle_error(query, "currency selection")
            return ConversationHandler.END

    async def source_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle source selection."""
        try:
            query = update.callback_query
            await query.answer()
            
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
                "✅ Документ сохранен.\n\n"
                "Выберите примечание или введите свой вариант:",
                reply_markup=reply_markup
            )
            return ENTERING_NOTE
            
        except Exception as e:
            logger.error(f"Error in handle_document: {e}")
            await self._handle_error(update, "document handling")
            return ConversationHandler.END

    async def handle_partner_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle partner account selection."""
        try:
            query = update.callback_query
            await query.answer()
            
            await query.edit_message_text(
                "Введите номер счета партнера:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
            )
            return ENTERING_PARTNER_ACCOUNT
            
        except Exception as e:
            logger.error(f"Error in handle_partner_account: {e}")
            await self._handle_error(query, "partner account selection")
            return ConversationHandler.END

    async def handle_partner_account_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle partner account input."""
        try:
            account = update.message.text.strip()
            if not account:
                await update.message.reply_text(
                    "❌ Пожалуйста, введите корректный номер счета.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="skip")]])
                )
                return ENTERING_PARTNER_ACCOUNT
            
            context.user_data['partner_account'] = account
            
            # Create keyboard for note selection
            keyboard = [
                [InlineKeyboardButton(note, callback_data=f"note_{i}")]
                for i, note in enumerate(self.config.NOTES)
            ]
            keyboard.append([InlineKeyboardButton("📝 Свой вариант", callback_data="custom_note")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Счет партнера сохранен: {account}\n\n"
                "Выберите примечание или введите свой вариант:",
                reply_markup=reply_markup
            )
            return ENTERING_NOTE
            
        except Exception as e:
            logger.error(f"Error in handle_partner_account_input: {e}")
            await self._handle_error(update, "partner account input")
            return ConversationHandler.END

    async def handle_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle note selection."""
        try:
            query = update.callback_query
            await query.answer()
            
            if query.data == "custom_note":
                await query.edit_message_text(
                    "Введите свое примечание:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return ENTERING_NOTE
            
            note_index = int(query.data.split('_')[1])
            note = self.config.NOTES[note_index]
            context.user_data['note'] = note
            
            # Create keyboard for period selection
            keyboard = [
                [InlineKeyboardButton("📅 Указать дату", callback_data="custom_date")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Выбрано примечание: {note}\n\n"
                "Выберите действие:",
                reply_markup=reply_markup
            )
            return CHOOSING_PERIOD
            
        except Exception as e:
            logger.error(f"Error in handle_note: {e}")
            await self._handle_error(query, "note selection")
            return ConversationHandler.END

    async def handle_custom_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle custom note input."""
        try:
            note = update.message.text.strip()
            if not note:
                await update.message.reply_text(
                    "❌ Пожалуйста, введите примечание.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return ENTERING_NOTE
            
            context.user_data['note'] = note
            
            # Create keyboard for period selection
            keyboard = [
                [InlineKeyboardButton("📅 Указать дату", callback_data="custom_date")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Примечание сохранено: {note}\n\n"
                "Выберите действие:",
                reply_markup=reply_markup
            )
            return CHOOSING_PERIOD
            
        except Exception as e:
            logger.error(f"Error in handle_custom_note: {e}")
            await self._handle_error(update, "custom note input")
            return ConversationHandler.END

    async def handle_period(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle period selection."""
        try:
            query = update.callback_query
            await query.answer()
            
            if query.data == "custom_date":
                await query.edit_message_text(
                    "Введите дату в формате ДД.ММ.ГГГГ:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return CHOOSING_DATE
            
            return ConversationHandler.END
            
        except Exception as e:
            logger.error(f"Error in handle_period: {e}")
            await self._handle_error(query, "period selection")
            return ConversationHandler.END

    async def handle_period_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle period input."""
        try:
            period = update.message.text.strip()
            if not period:
                await update.message.reply_text(
                    "❌ Пожалуйста, введите период.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return CHOOSING_PERIOD
            
            context.user_data['period'] = period
            
            # Create keyboard for confirmation
            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
                [InlineKeyboardButton("❌ Отменить", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Период сохранен: {period}\n\n"
                "Подтвердите создание заявки:",
                reply_markup=reply_markup
            )
            return CONFIRMING_REQUEST
            
        except Exception as e:
            logger.error(f"Error in handle_period_input: {e}")
            await self._handle_error(update, "period input")
            return ConversationHandler.END

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle date input."""
        try:
            date_text = update.message.text.strip()
            try:
                date = datetime.strptime(date_text, "%d.%m.%Y")
            except ValueError:
                await update.message.reply_text(
                    "❌ Неверный формат даты. Пожалуйста, используйте формат ДД.ММ.ГГГГ",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Отмена", callback_data="back_to_menu")]])
                )
                return CHOOSING_DATE
            
            context.user_data['date'] = date
            
            # Create keyboard for confirmation
            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
                [InlineKeyboardButton("❌ Отменить", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Дата сохранена: {date.strftime('%d.%m.%Y')}\n\n"
                "Подтвердите создание заявки:",
                reply_markup=reply_markup
            )
            return CONFIRMING_REQUEST
            
        except Exception as e:
            logger.error(f"Error in handle_date: {e}")
            await self._handle_error(update, "date input")
            return ConversationHandler.END

    async def confirm_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle request confirmation."""
        try:
            query = update.callback_query
            await query.answer()
            
            if query.data != "confirm":
                return ConversationHandler.END
            
            # Prepare additional data
            additional_data = {}
            if 'note' in context.user_data:
                additional_data['note'] = context.user_data['note']
            if 'document' in context.user_data:
                additional_data['document_path'] = context.user_data['document']['file_id']
            if 'partner_account' in context.user_data:
                additional_data['partner_account'] = context.user_data['partner_account']
            if 'date' in context.user_data:
                additional_data['expense_date'] = context.user_data['date']
            
            # Create request in database
            request = create_request(
                self.db_session,
                update.effective_user.id,
                context.user_data['project'],
                context.user_data['amount'],
                context.user_data['currency'],
                context.user_data['source'],
                **additional_data
            )
            
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
            
        except Exception as e:
            logger.error(f"Error in confirm_request: {e}")
            await self._handle_error(query, "request confirmation")
            return ConversationHandler.END

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        try:
            logger.error(f"Update {update} caused error {context.error}")
            
            if update and update.effective_message:
                await update.effective_message.reply_text(
                    "❌ Произошла ошибка при обработке запроса.\n"
                    "Пожалуйста, попробуйте еще раз или обратитесь в техподдержку @butterglobe"
                )
        except Exception as e:
            logger.error(f"Error in error handler: {e}")

    async def _handle_filter(self, query, context):
        """Handle filter selection."""
        status = query.data.split("_")[1]
        context.user_data['request_filter'] = status if status != "all" else None
        context.user_data['request_page'] = 0
        
        status_enum = None
        if context.user_data['request_filter']:
            try:
                status_enum = RequestStatus[context.user_data['request_filter'].upper()]
            except KeyError:
                logger.error(f"Invalid status filter: {context.user_data['request_filter']}")
        
        requests = get_requests(self.db_session, status=status_enum, limit=5, offset=0)
        message, keyboard = self._create_request_list_message(requests, context)
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        return VIEWING_REQUESTS

    async def _handle_page_navigation(self, query, context):
        """Handle page navigation."""
        current_page = context.user_data.get('request_page', 0)
        new_page = current_page - 1 if query.data == "prev_page" else current_page + 1
        context.user_data['request_page'] = new_page
        
        status_enum = None
        if context.user_data.get('request_filter'):
            try:
                status_enum = RequestStatus[context.user_data['request_filter'].upper()]
            except KeyError:
                logger.error(f"Invalid status filter: {context.user_data['request_filter']}")
        
        requests = get_requests(self.db_session, status=status_enum, limit=5, offset=new_page * 5)
        message, keyboard = self._create_request_list_message(requests, context)
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
        return VIEWING_REQUESTS

    async def _handle_back_to_menu(self, query):
        """Handle back to menu action."""
        keyboard = [
            [InlineKeyboardButton("📝 Создать заявку", callback_data="create_request")],
            [InlineKeyboardButton("📋 Просмотр заявок", callback_data="view_requests")],
            [InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "👋 Выберите действие:",
            reply_markup=reply_markup
        )
        return ADMIN_MENU

    def _create_request_list_message(self, requests, context):
        """Create message and keyboard for request list."""
        message = "📋 Список заявок:\n\n"
        if not requests:
            message = "❌ Нет заявок для просмотра."
        else:
            for request in requests:
                user = self.db_session.query(User).filter_by(id=request.user_id).first()
                emoji = self.status_emoji.get(request.status, "")
                message += f"{emoji} #{request.id} - {self.config.PROJECTS[request.project]}\n"
                message += f"Сумма: {request.amount} {self.config.CURRENCIES[request.currency]}\n"
                message += f"От: {user.username or f'user_{user.telegram_id}'}\n"
                message += f"Статус: {request.status.value}\n"
                message += f"Дата: {request.created_at.strftime('%d/%m/%Y')}\n\n"
        
        keyboard = []
        
        # Add request buttons
        for request in requests:
            keyboard.append([InlineKeyboardButton(f"Заявка #{request.id}", callback_data=f"view_{request.id}")])
        
        # Add navigation buttons
        nav_buttons = []
        if context.user_data['request_page'] > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data="prev_page"))
        if len(requests) == 5:
            nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data="next_page"))
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Add filter buttons
        filter_buttons = [
            ("⏳ Ожидающие", "filter_pending"),
            ("💰 Ожидают оплаты", "filter_waiting_payment"),
            ("✅ Оплаченные", "filter_paid"),
            ("❌ Отклоненные", "filter_rejected"),
            ("📋 Все", "filter_all")
        ]
        
        for label, callback_data in filter_buttons:
            if callback_data == f"filter_{context.user_data.get('request_filter')}":
                label = f"✓ {label}"
            keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
        
        # Add back button
        keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")])
        
        return message, keyboard

    async def _handle_error(self, query, action_type):
        """Handle errors in handlers."""
        keyboard = [[InlineKeyboardButton("🆘 Техподдержка", url="https://t.me/butterglobe")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"❌ Произошла ошибка при обработке {action_type}.\n"
            "Пожалуйста, попробуйте еще раз или обратитесь в техподдержку @butterglobe",
            reply_markup=reply_markup
        )

class Bot:
    """Main bot class."""
    def __init__(self):
        self.config = BotConfig()
        self.db_session = init_db()
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
                        CallbackQueryHandler(self.handlers.admin_menu, pattern="^(create_request|view_requests)$"),
                        CallbackQueryHandler(self.handlers.project_selected, pattern="^(mf_rf|mf_kz|mf_am|mf_world)$")
                    ],
                    VIEWING_REQUESTS: [
                        CallbackQueryHandler(self.handlers.handle_request_navigation, pattern="^(filter_|prev_page|next_page|back_to_menu)$"),
                        CallbackQueryHandler(self.handlers.view_request_details, pattern="^view_")
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
                    ],
                    CHOOSING_PROJECT: [CallbackQueryHandler(self.handlers.project_selected)],
                    ENTERING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.amount_entered)],
                    CHOOSING_CURRENCY: [CallbackQueryHandler(self.handlers.currency_selected)],
                    CHOOSING_SOURCE: [CallbackQueryHandler(self.handlers.source_selected)],
                    ATTACHING_DOCUMENT: [
                        CallbackQueryHandler(self.handlers.handle_document, pattern="^attach$"),
                        CallbackQueryHandler(self.handlers.handle_partner_account, pattern="^partner$"),
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
                    CHOOSING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_date)],
                    CONFIRMING_REQUEST: [CallbackQueryHandler(self.handlers.confirm_request)]
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