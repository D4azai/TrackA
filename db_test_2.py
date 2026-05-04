import os
os.environ.pop("DRAIN_INTERVAL_MINUTES", None)
os.environ.pop("ENQUEUE_INTERVAL_MINUTES", None)

import sys
from sqlalchemy.orm import Session
from app.db import engine
from app.services.algorithm import RecommendationEngine

def test_algo(seller_id):
    with Session(engine) as db:
        algo = RecommendationEngine(db)
        recs = algo.compute_recommendations(seller_id, limit=100)
        print(f"Got {len(recs)} recommendations")
        if recs:
            print("First:", recs[0])

if __name__ == "__main__":
    test_algo("cmjsjapmz000004jrwrfe0yxn")
