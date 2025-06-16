from pydantic import BaseModel, EmailStr
from datetime import datetime

class UserDataBase(BaseModel):
    name: str
    email: EmailStr
    message: str | None = None

class UserDataCreate(UserDataBase):
    pass

class UserData(UserDataBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True
