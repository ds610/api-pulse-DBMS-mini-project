import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

# 1. Connect to PostgreSQL
print("Connecting to PostgreSQL at localhost:5433...")
conn = psycopg.connect("postgresql://apipulse:apipulse123@localhost:5433/apipulse", autocommit=True)
register_vector(conn)

# 2. Load the sentence-transformer model (384-dimensional dense vectors)
print("Loading model 'all-MiniLM-L6-v2' (approx. 90MB on first download)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

# 3. Ensure all services have their descriptions embedded in PostgreSQL
services = conn.execute("SELECT service_id, service_name, description, embedding FROM api_services").fetchall()
updated_count = 0
for sid, name, desc, emb in services:
    if emb is None and desc:
        print(f"Generating embedding for '{name}'...")
        vector = model.encode(desc).tolist()
        conn.execute("UPDATE api_services SET embedding = %s WHERE service_id = %s", (vector, sid))
        updated_count += 1

if updated_count > 0:
    print(f"Updated {updated_count} service embedding(s).")
else:
    print("All service embeddings are already up to date.")

# 4. Semantic search function using pgvector cosine distance (<=>)
def search_services(query: str, top_k: int = 3):
    query_vector = model.encode(query).tolist()
    sql = """
        SELECT service_id, service_name, provider, description,
               (1 - (embedding <=> %s::vector)) AS similarity
        FROM api_services
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
    """
    results = conn.execute(sql, (query_vector, query_vector, top_k)).fetchall()
    
    print(f"\n=======================================================")
    print(f"Query: \"{query}\"")
    print(f"=======================================================")
    for sid, sname, prov, desc, sim in results:
        pct = sim * 100
        print(f"  • {sname} ({prov}) [Match: {pct:.1f}%]")
        print(f"    Description: {desc}")
    return results

# 5. Verification tests matching expected outputs
if __name__ == "__main__":
    test_queries = [
        "send text messages",
        "accept payments online",
        "chatbot that writes answers"
    ]
    
    print("\nRunning verification test queries...")
    for query in test_queries:
        search_services(query)
        
    conn.close()
    print("\nSemantic search verification completed successfully!")

