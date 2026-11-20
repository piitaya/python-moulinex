"""Exceptions raised by aiomoulinex."""

from __future__ import annotations

from enum import StrEnum


class MoulinexError(Exception):
    """Base class for all errors raised by aiomoulinex."""


class MoulinexConnectionError(MoulinexError):
    """The SEB cloud could not be reached, or it did not answer in time."""


class MoulinexAuthenticationError(MoulinexError):
    """The SEB cloud refused the tokens: the account has to log in again."""


class LoginFailure(StrEnum):
    """Why the SEB cloud refused a login."""

    INVALID_AUTH = "invalid_auth"
    EMAIL_NOT_VERIFIED = "email_not_verified"
    ACCOUNT_LOCKED = "account_locked"
    PASSWORD_RESET_REQUIRED = "password_reset_required"  # noqa: S105


class LoginError(MoulinexAuthenticationError):
    """The SEB cloud refused the email and password."""

    def __init__(self, reason: LoginFailure) -> None:
        """Initialize the error with the reason of the refusal."""
        super().__init__(reason.value)
        self.reason = reason
