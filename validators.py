import re
from decimal import Decimal, InvalidOperation
from datetime import datetime


def validate_amount(amount_str: str) -> float:
    """Validate and sanitize amount input. Returns float if valid, raises ValueError otherwise."""
    try:
        amount = Decimal(amount_str.replace(',', '.'))
        if amount <= 0:
            raise ValueError("Сумма должна быть больше нуля")
        if amount > 1000000000:  # Максимальная сумма 1 миллиард
            raise ValueError("Сумма слишком большая")
        return amount
    except (InvalidOperation, ValueError):
        raise ValueError("Неверный формат суммы. Используйте числа и точку/запятую")


def validate_currency(currency: str, allowed_currencies: list) -> str:
    """Validate and sanitize currency input. Returns sanitized string if valid, raises ValueError otherwise."""
    if currency not in allowed_currencies:
        raise ValueError(f"Неверная валюта. Доступные валюты: {', '.join(allowed_currencies)}")
    return currency


def validate_project(project: str, allowed_projects: list) -> str:
    """Validate and sanitize project input. Returns sanitized string if valid, raises ValueError otherwise."""
    if project not in allowed_projects:
        raise ValueError(f"Неверный проект. Доступные проекты: {', '.join(allowed_projects)}")
    return project


def validate_source(source: str, allowed_sources: list) -> str:
    """Validate and sanitize source input. Returns sanitized string if valid, raises ValueError otherwise."""
    if source not in allowed_sources:
        raise ValueError(f"Неверный источник. Доступные источники: {', '.join(allowed_sources)}")
    return source


def validate_date(date_str: str) -> str:
    """Validate and sanitize date input. Accepts YYYY-MM-DD or DD.MM.YYYY. Returns ISO format if valid, raises ValueError otherwise."""
    date_str = date_str.strip()
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        elif re.match(r'^\d{2}\.\d{2}\.\d{4}$', date_str):
            dt = datetime.strptime(date_str, '%d.%m.%Y')
        else:
            raise ValueError
        if dt < datetime.now():
            raise ValueError("Дата не может быть в прошлом")
        return dt.date().isoformat()
    except Exception:
        raise ValueError("Некорректный формат даты. Используйте ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.")


def validate_period(period: str) -> str:
    """Validate and sanitize period input. Returns sanitized string if valid, raises ValueError otherwise.
    
    Accepts:
    - Predefined periods: "Единоразово", "Ежемесячно", "Еженедельно"
    - Date ranges in format DD.MM.YYYY-DD.MM.YYYY
    - Single dates in format DD.MM.YYYY
    """
    period = period.strip()
    
    # Проверяем предопределенные периоды
    allowed_periods = ["Единоразово", "Ежемесячно", "Еженедельно"]
    if period in allowed_periods:
        return period
        
    # Проверяем формат даты или периода
    try:
        # Проверяем формат периода (DD.MM.YYYY-DD.MM.YYYY)
        if '-' in period:
            start_date, end_date = period.split('-')
            start_date = start_date.strip()
            end_date = end_date.strip()
            
            # Проверяем формат обеих дат
            datetime.strptime(start_date, '%d.%m.%Y')
            datetime.strptime(end_date, '%d.%m.%Y')
            
            # Проверяем, что начальная дата не позже конечной
            start = datetime.strptime(start_date, '%d.%m.%Y')
            end = datetime.strptime(end_date, '%d.%m.%Y')
            if start > end:
                raise ValueError("Начальная дата не может быть позже конечной")
                
            return period
            
        # Проверяем формат одиночной даты (DD.MM.YYYY)
        else:
            datetime.strptime(period, '%d.%m.%Y')
            return period
            
    except ValueError as e:
        if "не может быть позже" in str(e):
            raise
        raise ValueError("Некорректный формат периода. Используйте ДД.ММ.ГГГГ или ДД.ММ.ГГГГ-ДД.ММ.ГГГГ")


def validate_note(note: str, max_length: int = 1000) -> str:
    """Validate and sanitize note input. Returns sanitized string if valid, raises ValueError otherwise."""
    note = note.strip()
    if not (2 <= len(note) <= max_length):
        raise ValueError(f"Примечание должно содержать от 2 до {max_length} символов.")
    # Remove dangerous characters
    note = re.sub(r'[<>"\'`]', '', note)
    return note


def validate_comment(comment: str, max_length: int = 500) -> str:
    """Validate and sanitize comment input. Returns sanitized string if valid, raises ValueError otherwise."""
    comment = comment.strip()
    if not (1 <= len(comment) <= max_length):
        raise ValueError(f"Комментарий должен содержать от 1 до {max_length} символов.")
    # Remove dangerous characters
    comment = re.sub(r'[<>"\'`]', '', comment)
    return comment


def validate_rejection_reason(reason: str) -> str:
    """Validate and sanitize rejection reason input. Returns sanitized string if valid, raises ValueError otherwise."""
    reason = reason.strip()
    if not (2 <= len(reason) <= 200):
        raise ValueError("Причина отклонения должна содержать от 2 до 200 символов.")
    # Remove dangerous characters
    reason = re.sub(r'[<>"\'`]', '', reason)
    return reason


def validate_edit_value(value: str) -> str:
    """Validate and sanitize generic edit input. Returns sanitized string if valid, raises ValueError otherwise."""
    value = value.strip()
    if not (1 <= len(value) <= 100):
        raise ValueError("Значение должно содержать от 1 до 100 символов.")
    value = re.sub(r'[<>"\'`]', '', value)
    return value


def validate_partner_account(account: str) -> str:
    """Validate and sanitize partner account input. Returns sanitized string if valid, raises ValueError otherwise."""
    account = account.strip()
    if not (2 <= len(account) <= 100):
        raise ValueError("Счет партнера должен содержать от 2 до 100 символов.")
    # Remove dangerous characters
    account = re.sub(r'[<>"\'`]', '', account)
    return account


def sanitize_input(text: str) -> str:
    """Очистка пользовательского ввода."""
    if not text:
        return text
    # Удаляем HTML-теги
    text = re.sub(r'<[^>]+>', '', text)
    # Заменяем множественные пробелы на один
    text = re.sub(r'\s+', ' ', text)
    # Удаляем начальные и конечные пробелы
    text = text.strip()
    return text
