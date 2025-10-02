# backend/app/schemas/user.py
from pydantic import BaseModel, Field

class UserRegisterIn(BaseModel):
    telegram_id: int = Field(..., ge=1)
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

class UserOut(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
