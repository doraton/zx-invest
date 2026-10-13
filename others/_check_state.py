import pymysql
c = pymysql.connect(host='127.0.0.1', port=3306, user='root',
                    password='dba126543', database='tsdb', charset='utf8mb4')
cur = c.cursor()
cur.execute("SELECT COUNT(*), COUNT(DISTINCT code), MIN(trade_date), MAX(trade_date) "
            "FROM kline_day WHERE code LIKE 'sh%'")
n, codes, lo, hi = cur.fetchone()
print(f'sh 落库现状: {n} 行 / {codes} 只 / {lo} ~ {hi}')
cur.execute("SELECT COUNT(*) FROM kline_day WHERE code='sh600570'")
print(f'恒生电子(先前单股测试): {cur.fetchone()[0]} 行')
cur.execute("SELECT COUNT(*) FROM kline_day WHERE pre_close IS NOT NULL")
print(f'pre_close 已填充的行: {cur.fetchone()[0]}')
c.close()