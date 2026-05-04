import os
from sqlalchemy import create_engine, text

db_url = "postgresql://neondb_owner:npg_7wdEBCVukQ2o@ep-young-star-ag8ff99z-pooler.c-2.eu-central-1.aws.neon.tech/neondb?sslmode=require"
engine = create_engine(db_url)

queries = [
    "SELECT id, email, \"firstName\", \"lastName\" FROM \"User\" WHERE id = 'cmjsjapmz000004jrwrfe0yxn';",
    "SELECT COUNT(*) FROM \"Product\";",
    "SELECT COUNT(*) FROM \"Product\" WHERE status = 'AVAILABLE';",
    "SELECT COUNT(*) FROM \"Product\" WHERE \"providerId\" = 'cmjsjapmz000004jrwrfe0yxn';",
    "SELECT COUNT(*) FROM \"Order\";",
    "SELECT COUNT(*) FROM \"OrderItem\";",
    "SELECT COUNT(*) FROM \"ProductReaction\";",
    "SELECT COUNT(*) FROM \"SellerRecommendation\";",
    "SELECT COUNT(*) FROM \"RecommendationRefreshJob\";"
]

with engine.connect() as conn:
    for q in queries:
        try:
            result = conn.execute(text(q)).fetchall()
            print(f"Query: {q}")
            print(f"Result: {result}\n")
        except Exception as e:
            print(f"Query failed: {q}\nError: {e}\n")
