import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.db import engine
from app.services.data_service import DataService

def audit_seller(seller_id):
    with Session(engine) as db:
        data_svc = DataService(db)
        
        print("=== History ===")
        # Raw count of orders
        orders_raw = db.execute(text(f"SELECT COUNT(*), COUNT(DISTINCT id) FROM \"Order\" WHERE \"sellerId\" = '{seller_id}'")).fetchall()
        print(f"Raw Order rows for seller {seller_id}: {orders_raw}")
        
        # Are there items?
        items_raw = db.execute(text(f"""
            SELECT COUNT(*) FROM "OrderItem" 
            JOIN "Order" ON "Order".id = "OrderItem"."orderId"
            WHERE "Order"."sellerId" = '{seller_id}'
        """)).fetchall()
        print(f"Raw OrderItem rows for seller: {items_raw}")

        history = data_svc.get_seller_order_history(seller_id, limit=100)
        print(f"Python returned history keys: {list(history.keys())}")

        print("\n=== Engagement ===")
        # Raw count of reactions
        reactions_raw = db.execute(text("SELECT COUNT(*) FROM \"ProductReaction\"")).fetchall()
        print(f"Total ProductReactions in DB: {reactions_raw}")
        
        comments_raw = db.execute(text("SELECT COUNT(*) FROM \"ProductComment\"")).fetchall()
        print(f"Total ProductComments in DB: {comments_raw}")

        # Let's test for candidate ids: pick a few popular ones
        pop = data_svc.get_popular_products(limit=5)
        cands = [p['product_id'] for p in pop]
        if cands:
            eng = data_svc.get_engagement_scores_batch(cands)
            print(f"Python returned engagement for candidates: {eng}")

if __name__ == "__main__":
    audit_seller("cmjsjapmz000004jrwrfe0yxn")
