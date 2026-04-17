"""
SQLAlchemy models matching Prisma schema.
Only includes models needed for the recommendation service.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, JSON, func
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    """User/Seller model."""
    __tablename__ = "User"

    id = Column(String, primary_key=True)
    email = Column(String, unique=True)
    firstName = Column(String)
    lastName = Column(String)
    sellerType = Column(String)  # NORMAL, PRO, VIP
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    orders = relationship("Order", back_populates="seller")


class Product(Base):
    """Product model."""
    __tablename__ = "Product"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    code = Column(String, unique=True)
    categoryId = Column(Integer, ForeignKey("Category.id"))
    sellingPrice = Column(Float)
    ratingStars = Column(Float, default=0)
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())
    status = Column(String, default="AVAILABLE")  # DRAFT, AVAILABLE, SOLD_OUT
    isPublic = Column(Boolean, default=True)
    allowedSellerIds = Column(JSON, default=list)

    # Relationships
    category = relationship("Category", back_populates="products")
    order_items = relationship("OrderItem", back_populates="product")
    reactions = relationship("ProductReaction", back_populates="product")
    comments = relationship("ProductComment", back_populates="product")


class Order(Base):
    """Order model."""
    __tablename__ = "Order"

    id = Column(Integer, primary_key=True)
    sellerId = Column(String, ForeignKey("User.id"))
    status = Column(String)  # PENDING, CONFIRMED, COMPLETED, CANCELLED
    createdAt = Column(DateTime, server_default=func.now())
    updatedAt = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    seller = relationship("User", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    """Individual item in an order."""
    __tablename__ = "OrderItem"

    id = Column(Integer, primary_key=True)
    orderId = Column(Integer, ForeignKey("Order.id"))
    productId = Column(Integer, ForeignKey("Product.id"))
    quantity = Column(Integer)
    createdAt = Column(DateTime, server_default=func.now())

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class ProductReaction(Base):
    """Likes/favorites on products."""
    __tablename__ = "ProductReaction"

    id = Column(Integer, primary_key=True)
    productId = Column(Integer, ForeignKey("Product.id"))
    userId = Column(String, ForeignKey("User.id"))
    createdAt = Column(DateTime, server_default=func.now())

    # Relationships
    product = relationship("Product", back_populates="reactions")


class ProductComment(Base):
    """Comments on products."""
    __tablename__ = "ProductComment"

    id = Column(Integer, primary_key=True)
    productId = Column(Integer, ForeignKey("Product.id"))
    userId = Column(String, ForeignKey("User.id"))
    content = Column(Text)
    createdAt = Column(DateTime, server_default=func.now())

    # Relationships
    product = relationship("Product", back_populates="comments")


class Category(Base):
    """Product categories."""
    __tablename__ = "Category"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    slug = Column(String, unique=True)
    description = Column(Text)

    # Relationships
    products = relationship("Product", back_populates="category")
