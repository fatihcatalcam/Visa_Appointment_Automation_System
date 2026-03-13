import psycopg2
import redis

# Redis connection
r = redis.Redis.from_url('redis://localhost:6379/0')

# DB connection
conn = psycopg2.connect("dbname='bot_db' user='bot_user' password='bot_password' host='localhost' port='5432'")
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT address FROM proxies")
rows = cur.fetchall()

deleted_count = 0
for row in rows:
    addr = row[0]
    # If the proxy is unusually long (concatenated by mistake) or contains spaces
    if len(addr) > 200 or ' ' in addr or '\n' in addr:
        cur.execute("DELETE FROM proxies WHERE address = %s", (addr,))
        print(f"Deleted from DB: {addr[:100]}... (length: {len(addr)})")
        
        # Remove from Redis
        r.srem("proxies:active", addr)
        r.zrem("proxies:cooldown", addr)
        deleted_count += 1

print(f"Total malformed proxies deleted: {deleted_count}")

cur.close()
conn.close()
