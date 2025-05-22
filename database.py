from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import enum
import json

Base = declarative_base()

class RequestStatus(enum.Enum):
    PENDING = "pending"
    WAITING = "waiting"
    PAID = "paid"
    REJECTED = "rejected"

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String)
    role = Column(String)  # user, admin, fincontrol
    created_at = Column(DateTime, default=datetime.utcnow)
    
    requests = relationship("Request", back_populates="user")

class Request(Base):
    __tablename__ = 'requests'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    project = Column(String)
    amount = Column(Float)
    currency = Column(String)
    source = Column(String)
    document_path = Column(String, nullable=True)
    partner_account = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    service_period_start = Column(DateTime, nullable=True)
    service_period_end = Column(DateTime, nullable=True)
    expense_date = Column(DateTime)
    status = Column(Enum(RequestStatus), default=RequestStatus.PENDING)
    status_history = Column(Text, nullable=True)  # Хранение истории статусов в формате JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="requests")
    comments = relationship("Comment", back_populates="request")

class Comment(Base):
    __tablename__ = 'comments'
    
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('requests.id'))
    user_id = Column(Integer, ForeignKey('users.id'))
    telegram_id = Column(Integer)  # Новый столбец для хранения telegram_id пользователя
    text = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    request = relationship("Request", back_populates="comments")
    user = relationship("User")

def init_db(db_url="sqlite:///bot.db"):
    """Initialize database connection and create tables."""
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

def get_or_create_user(session, telegram_id, username, role="user"):
    """Get existing user or create new one."""
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, username=username, role=role)
        session.add(user)
        session.commit()
    return user

def create_request(session, user_id, project, amount, currency, source, **kwargs):
    """Create new expense request."""
    request = Request(
        user_id=user_id,
        project=project,
        amount=amount,
        currency=currency,
        source=source,
        **kwargs
    )
    session.add(request)
    session.commit()
    return request

def update_request_status(session, request_id, status, user_id=None):
    """Update request status and add to history."""
    request = session.query(Request).get(request_id)
    if request:
        # Получаем текущую историю статусов
        history = []
        if request.status_history:
            try:
                history = json.loads(request.status_history)
            except:
                history = []
        
        # Добавляем новую запись в историю
        history.append({
            'status': status.value,
            'timestamp': datetime.utcnow().isoformat(),
            'user_id': user_id
        })
        
        # Обновляем статус и историю
        request.status = status
        request.status_history = json.dumps(history)
        session.commit()
    return request

def add_comment(session, request_id, user_id, text, telegram_id=None):
    """Add comment to request."""
    if telegram_id is None:
        # Get telegram_id from user if not provided
        user = session.query(User).filter_by(id=user_id).first()
        if user:
            telegram_id = user.telegram_id
    
    comment = Comment(request_id=request_id, user_id=user_id, telegram_id=telegram_id, text=text)
    session.add(comment)
    session.commit()
    return comment

def get_requests(session, user_id=None, status=None, limit=10, offset=0):
    query = session.query(Request)
    if user_id:
        query = query.filter_by(user_id=user_id)
    if status:
        query = query.filter_by(status=status)
    return query.order_by(Request.created_at.asc()).limit(limit).offset(offset).all()

def get_requests_paginated(session, status=None, page=0, page_size=5):
    query = session.query(Request)
    if status:
        query = query.filter(Request.status == status)
    total = query.count()
    requests = query.order_by(Request.id).offset(page * page_size).limit(page_size).all()
    return requests, total


def get_request(session, request_id):
    return session.query(Request).filter_by(id=request_id).first()

def get_request_comments(session, request_id):
    return session.query(Comment).filter_by(request_id=request_id).order_by(Comment.created_at).all()

def update_request(session, request_id, **kwargs):
    request = get_request(session, request_id)
    if request:
        for key, value in kwargs.items():
            if hasattr(request, key):
                setattr(request, key, value)
        session.commit()
        return True
    return False 