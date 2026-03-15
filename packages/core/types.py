"""
Shared types for the Norm platform.
"""

from dataclasses import dataclass


@dataclass
class Venue:
    id: str
    name: str
    location: str


@dataclass
class Supplier:
    id: str
    name: str


@dataclass
class Product:
    id: str
    name: str
    supplier: str
    category: str
    unit: str
    aliases: list[str]


@dataclass
class DraftOrder:
    id: str
    venue_id: str
    product_id: str
    quantity: int
    supplier: str
    status: str  # draft | approved | submitted
