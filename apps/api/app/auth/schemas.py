"""Pydantic models for auth endpoints."""

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str
    #: Signing up makes your organisation and its first venue (Oct 2026): an
    #: account with neither saw nothing until someone added it by hand.
    organization_name: str
    venue_name: str
    venue_location: str | None = None
    venue_timezone: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
