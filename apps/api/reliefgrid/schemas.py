from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# Email validation uses a simple bounded string to avoid an additional runtime package.
class Register(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    organization: str = Field(min_length=3, max_length=120)

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.rsplit("@", 1)[-1] or " " in value:
            raise ValueError("Invalid email address")
        return value

class Login(BaseModel):
    email: str
    password: str

class LocationInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = Field(pattern="^(warehouse|distribution_center|hospital|shelter|emergency)$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    contact: str = Field(default="", max_length=250)
    capacity: Decimal | None = Field(default=None, ge=0, decimal_places=3)

class LotInput(BaseModel):
    location_id: UUID
    resource_id: UUID
    lot_code: str = Field(min_length=1, max_length=100)
    expires_on: date | None = None
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)

class Adjustment(BaseModel):
    delta: Decimal = Field(max_digits=16, decimal_places=3)
    reason: str = Field(min_length=5, max_length=200)

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("Delta cannot be zero")
        return value

class RequestItemInput(BaseModel):
    resource_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)

class RequestInput(BaseModel):
    delivery_location_id: UUID
    priority: str = Field(pattern="^(routine|high|critical)$")
    reason: str = Field(min_length=5, max_length=1000)
    notes: str = Field(default="", max_length=2000)
    required_by: datetime
    items: list[RequestItemInput] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_request(self):
        if self.required_by.tzinfo is None:
            raise ValueError("required_by needs a timezone")
        if len({item.resource_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate resource")
        return self

class AllocationInput(BaseModel):
    request_item_id: UUID
    lot_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)

class ShipmentInput(BaseModel):
    allocation_id: UUID
    reference: str = Field(min_length=2, max_length=100)

class DeliveryInput(BaseModel):
    recipient_name: str = Field(min_length=2, max_length=120)
    evidence_note: str = Field(default="", max_length=500)

class ResourceInput(BaseModel):
    category: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=150)
    unit: str = Field(pattern="^(piece|liter|kilogram|pack)$")
