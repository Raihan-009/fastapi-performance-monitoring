from sqlalchemy.orm import Session
from . import models, schemas

def get_user_data(db: Session, skip: int = 0, limit: int = 100):
    return (
        db.query(models.UserData)
          .order_by(models.UserData.created_at.desc())
          .offset(skip)
          .limit(limit)
          .all()
    )

def create_user_data(db: Session, data: schemas.UserDataCreate):
    item = models.UserData(**data.dict())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item

def update_user_data(db: Session, item_id: int, data: schemas.UserDataCreate):
    item = db.query(models.UserData).filter(models.UserData.id == item_id).first()
    if not item:
        return None
    for field, value in data.dict().items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item

def delete_user_data(db: Session, item_id: int):
    item = db.query(models.UserData).filter(models.UserData.id == item_id).first()
    if not item:
        return None
    db.delete(item)
    db.commit()
    return item
