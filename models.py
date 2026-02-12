"""Domain models — enums and dataclasses."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class EventStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    ACTIVE = "active"
    ARCHIVED = "archived"


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class User:
    telegram_id: int
    username: Optional[str]
    full_name: str
    phone: Optional[str] = None
    role: UserRole = UserRole.USER
    created_at: Optional[datetime] = None


@dataclass
class Event:
    id: int
    title: str
    date_start: date
    type: Optional[str] = None
    date_end: Optional[date] = None
    time: Optional[str] = None
    place: Optional[str] = None
    description: Optional[str] = None
    max_participants: int = 0
    status: EventStatus = EventStatus.PENDING
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class EventRegistration:
    id: int
    event_id: int
    full_name: str
    username: Optional[str] = None
    telegram_id: Optional[int] = None
    phone: Optional[str] = None
    level: Optional[str] = None
    comment: Optional[str] = None
    registered_at: Optional[datetime] = None


@dataclass
class AdminRequest:
    id: int
    username: str
    request_type: str
    telegram_id: Optional[int] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    requested_table: Optional[str] = None
    payload_json: Optional[dict] = field(default=None)
    comment: Optional[str] = None
    status: RequestStatus = RequestStatus.PENDING
    reviewed_by: Optional[str] = None
    created_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
