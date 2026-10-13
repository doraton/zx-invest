import pymysql
c = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                    password="dba126543", database="tsdb")
cur = c.cursor()
cur.execute("SHOW TABLES")
for (t,) in cur.fetchall():
    print("  ", t)
cur.execute("SHOW TABLES LIKE 'ref_%'")
print("  ref_* 残留:", cur.fetchall() or "无")
c.close()
