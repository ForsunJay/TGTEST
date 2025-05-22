# Telegram Expense Tracking Bot

Бот для учета расходов с разделением ролей пользователей и администраторов.

## Установка

1. Клонируйте репозиторий:
```bash
git clone <repository-url>
cd <repository-directory>
```

2. Создайте виртуальное окружение и активируйте его:
```bash
python -m venv venv
source venv/bin/activate  # для Linux/Mac
venv\Scripts\activate     # для Windows
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Создайте файл `bot.env` и настройте его:
```bash
# Telegram Bot Token
TELEGRAM_TOKEN=your_bot_token_here

# Administrator Configuration
# Format: ADMIN_IDS=123456789,987654321
ADMIN_IDS=878537184

# Allow administrators to create requests (true/false)
ALLOW_ADMIN_CREATE=true

# Administrator Source Mapping
# Format: ADMIN_SOURCE_<SOURCE_ID>=<ADMIN_ID>,<ADMIN_ID>
ADMIN_SOURCE_RS_RF=878537184
ADMIN_SOURCE_RS_TOO_KZ=878537184
ADMIN_SOURCE_RS_IP_KZ=878537184
ADMIN_SOURCE_RS_OOO_AM=878537184,987654321
ADMIN_SOURCE_RS_OOO_AM_EUR=878537184,987654321
ADMIN_SOURCE_CARD_TOO_KZ=878537184
ADMIN_SOURCE_CARD_IP_KZ=878537184
ADMIN_SOURCE_CARD_OOO_AM=878537184,987654321
ADMIN_SOURCE_CASH=987654321

# Crypto Project Administrator Mapping
# Format: ADMIN_CRYPTO_<PROJECT_ID>=<ADMIN_ID>,<ADMIN_ID>
ADMIN_CRYPTO_MF_RF=878537184
ADMIN_CRYPTO_MF_KZ=878537184
ADMIN_CRYPTO_MF_WORLD=878537184
ADMIN_CRYPTO_MF_AM=878537184,987654321
```

## Запуск

```bash
python bot.py
```

## Функциональность

### Для пользователей:
- Создание заявок на расходы
- Выбор проекта
- Ввод суммы и валюты
- Выбор источника средств
- Прикрепление документов
- Добавление примечаний
- Указание периода оказания услуг
- Просмотр статуса заявок

### Для администраторов:
- Доступ к административной панели
- Создание заявок (если включено в настройках)
- Просмотр новых заявок
- Подтверждение/отклонение заявок
- Редактирование заявок
- Комментирование заявок
- Экспорт данных

## Настройка администраторов

### Добавление администратора

1. Получите ID пользователя в Telegram (можно через @userinfobot)
2. Откройте файл `bot.env`
3. Добавьте ID в список `ADMIN_IDS`:
```
ADMIN_IDS=123456789,987654321
```

### Настройка прав администратора

1. Для настройки прав на источники средств используйте формат:
```
ADMIN_SOURCE_<SOURCE_ID>=<ADMIN_ID>,<ADMIN_ID>
```
Например:
```
ADMIN_SOURCE_RS_RF=123456789
ADMIN_SOURCE_RS_TOO_KZ=123456789,987654321
```

2. Для настройки прав на криптовалютные проекты используйте формат:
```
ADMIN_CRYPTO_<PROJECT_ID>=<ADMIN_ID>,<ADMIN_ID>
```
Например:
```
ADMIN_CRYPTO_MF_RF=123456789
ADMIN_CRYPTO_MF_KZ=123456789,987654321
```

3. Для разрешения администраторам создавать заявки установите:
```
ALLOW_ADMIN_CREATE=true
```

После внесения изменений необходимо перезапустить бота для применения новых настроек.

## Структура хранения документов

Документы хранятся в следующей структуре:
```
documents/
    ├── username1/
    │   ├── request_20240321_123456/
    │   │   └── document1.pdf
    │   └── request_20240321_123789/
    │       └── document2.pdf
    └── username2/
        └── request_20240321_124012/
            └── document3.pdf
```

Каждый пользователь имеет свою папку, а внутри неё создаются подпапки для каждой заявки с уникальным временным идентификатором. 