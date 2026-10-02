from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    # Plain string: login only looks up an existing account, so format validation adds nothing.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str
