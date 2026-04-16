"""
SQLAlchemy models matching Prisma schema
Only include models needed for recommendations
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, JSON
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()


class User(Base):
    """User/Seller model"""
    __tablename__ = "User"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True)
    firstName = Column(String)
    lastName = Column(String)
    sellerType = Column(String)  # NORMAL, PRO, VIP
    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Product(Base):
    """Product model"""
    __tablename__ = "Product"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    code = Column(String, unique=True)
    categoryId = Column(Integer)
    sellingPrice = Column(Float)
    ratingStars = Column(Integer, default=0)
    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    status = Column(String, default="AVAILABLE")  # DRAFT, AVAILABLE, SOLD_OUT
    isPublic = Column(Boolean, default=True)
    allowedSellerIds = Column(JSON, default=[])


class Order(Base):
    """Order model"""
    __tablename__ = "Order"

    id = Column(Integer, primary_key=True)
    sellerId = Column(String)
    status = Column(String)  # PENDING, CONFIRMED, COMPLETED, CANCELLED
    createdAt = Column(DateTime, default=datetime.utcnow)
    updatedAt = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderItem(Base):
    """Individual item in order"""
    __tablename__ = "OrderItem"

    id = Column(Integer, primary_key=True)
    orderId = Column(Integer)
    productId = Column(Integer)
    quantity = Column(Integer)
    createdAt = Column(DateTime, default=datetime.utcnow)


class ProductReaction(Base):
    """Likes/favorites on products"""
    __tablename__ = "ProductReaction"

    id = Column(Integer, primary_key=True)
    productId = Column(Integer)
    userId = Column(String)
    createdAt = Column(DateTime, default=datetime.utcnow)


class ProductComment(Base):
    """Comments on products"""
    __tablename__ = "ProductComment"

    id = Column(Integer, primary_key=True)
    productId = Column(Integer)
    userId = Column(String)
    content = Column(Text)
    createdAt = Column(DateTime, default=datetime.utcnow)


class SellerPreferences(Base):
    """Computed seller preferences"""
    __tablename__ = "SellerPreferences"

    id = Column(String, primary_key=True)
    sellerId = Column(String, unique=True)
    categoryScores = Column(JSON, default=[])  # List of {categoryId, score}
    avgOrderValue = Column(Float, default=0)
    priceRangeMin = Column(Float, default=0)
    priceRangeMax = Column(Float, default=0)
    totalOrders = Column(Integer, default=0)
    totalProducts = Column(Integer, default=0)
    lastOrderAt = Column(DateTime)
    computedAt = Column(DateTime, default=datetime.utcnow)


class Category(Base):
    """Product categories"""
    __tablename__ = "Category"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String, unique=True)
    description = Column(Text)
