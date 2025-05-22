import random
from datetime import datetime, timedelta
from faker import Faker
from database import init_db, User, Request, Comment, RequestStatus

fake = Faker('ru_RU')
session = init_db()

PROJECTS = ['mf_rf', 'mf_kz', 'mf_am', 'mf_world']
CURRENCIES = ['RUB', 'KZT', 'AMD', 'USD', 'EUR', 'USDT']
SOURCES = [
    'rs_rf', 'rs_too_kz', 'rs_ip_kz', 'card_too_kz', 'card_ip_kz',
    'rs_ooo_am', 'rs_ooo_am_eur', 'card_ooo_am', 'crypto', 'cash'
]
NOTES = [
    'Реклама', 'Сопровождение РК', 'Ком-ции. СМС', 'Ком-ции. АВТОДОЗВОНЫ',
    'Ком-ции. РАССЫЛКИ', 'Ком-ции. ТЕЛЕФОНИЯ', 'Ком-ции. ОНЛАЙН'
]
STATUSES = list(RequestStatus)

NUM_USERS = 10
NUM_REQUESTS = 50
NUM_COMMENTS = 100

# Создание пользователей
def create_users():
    users = []
    for _ in range(NUM_USERS):
        user = User(
            telegram_id=fake.unique.random_int(10000, 99999),
            username=fake.user_name(),
            role=random.choice(['user', 'admin', 'fincontrol']),
            created_at=fake.date_time_this_year()
        )
        session.add(user)
        users.append(user)
    session.commit()
    return users

# Создание заявок
def create_requests(users):
    requests = []
    for _ in range(NUM_REQUESTS):
        user = random.choice(users)
        project = random.choice(PROJECTS)
        amount = round(random.uniform(1000, 100000), 2)
        currency = random.choice(CURRENCIES)
        source = random.choice(SOURCES)
        note = random.choice(NOTES)
        status = random.choice(STATUSES)
        expense_date = fake.date_time_this_year()
        created_at = expense_date - timedelta(days=random.randint(0, 10))
        updated_at = created_at + timedelta(days=random.randint(0, 5))
        req = Request(
            user_id=user.id,
            project=project,
            amount=amount,
            currency=currency,
            source=source,
            document_path=None,
            partner_account=fake.iban(),
            note=note,
            service_period_start=None,
            service_period_end=None,
            expense_date=expense_date,
            status=status,
            created_at=created_at,
            updated_at=updated_at
        )
        session.add(req)
        requests.append(req)
    session.commit()
    return requests

# Создание комментариев
def create_comments(users, requests):
    for _ in range(NUM_COMMENTS):
        user = random.choice(users)
        req = random.choice(requests)
        comment = Comment(
            request_id=req.id,
            user_id=user.id,
            text=fake.sentence(nb_words=10),
            created_at=fake.date_time_between(start_date=req.created_at, end_date=req.updated_at)
        )
        session.add(comment)
    session.commit()

def main():
    users = create_users()
    requests = create_requests(users)
    create_comments(users, requests)
    print('База данных успешно заполнена случайными данными!')

if __name__ == '__main__':
    main()
