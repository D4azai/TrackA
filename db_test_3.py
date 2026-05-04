import logging
logging.basicConfig(level=logging.DEBUG)

from sqlalchemy.orm import Session
from app.db import engine
from app.services.algorithm import RecommendationEngine

def test_algo(seller_id):
    with Session(engine) as db:
        algo = RecommendationEngine(db)
        recs = algo.compute_recommendations(seller_id, limit=5)

if __name__ == "__main__":
    test_algo("cmjsjapmz000004jrwrfe0yxn")
