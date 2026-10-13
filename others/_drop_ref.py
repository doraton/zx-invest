import pymysql
for db in ("mysqldb", "tq-stock", "tsdb"):
    c = pymysql.connect(host="127.0.0.1", port=3306, user="root",
                        password="dba126543", database=db, autocommit=True)
    cur = c.cursor()
    cur.execute("DROP TABLE IF EXISTS ref_industry")
    cur.execute("DROP TABLE IF EXISTS ref_province")
    cur.execute("SHOW TABLES")
    print(f"{db:<10} 剩余表: {', '.join(r[0] for r in cur.fetchall())}")
    c.close()
