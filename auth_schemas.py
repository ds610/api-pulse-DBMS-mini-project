from pydantic import BaseModel, Field


class LoginPayload(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=256)