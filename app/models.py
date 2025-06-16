from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, func
from .database import Base

class UserData(Base):
    __tablename__ = "user_data"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    message = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
