"""tdx2db 写入 MySQL `tdx2db` 库，分阶段跑 stock-list / daily / blocks / status。
依赖：MySQL 容器已启、库 tdx2db 已建（root / dba126543 @ 127.0.0.1:3306）。
"""
import os
import subprocess
from pathlib import Path

DEMO = Path(r'D:\repo\03_pers-proj\zx-stock\server\tdx2db_demo')
DEMO.mkdir(parents=True, exist_ok=True)

env_file = DEMO / '.env'
env_file.write_text(
    'TDX_PATH=D:\\prod-forces\\finance\\tdx\n'
    'CSV_OUTPUT_PATH=' + str(DEMO).replace('\\', '\\\\') + '\n'
    'DB_TYPE=mysql\n'
    'DB_HOST=127.0.0.1\n'
    'DB_PORT=3306\n'
    'DB_USER=root\n'
    'DB_PASSWORD=dba126543\n'
    'DB_NAME=tdx2db\n'
    'USE_TQDM=False\n'
    'DB_BATCH_SIZE=500\n',
    encoding='utf-8',
)
print(f"[probe] .env -> {env_file}")

tdx2db_exe = str(Path(r'D:\repo\03_pers-proj\zx-stock\server\.venv\Scripts\tdx2db.exe'))

def run_cli(args, timeout=600):
    cmd = [tdx2db_exe] + args
    print(f"\n[probe] $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(DEMO), env={**os.environ}, capture_output=True, text=True, timeout=timeout)
    print('--- stdout ---')
    print(r.stdout[-3000:])
    print('--- stderr ---')
    print(r.stderr[-2000:])
    print(f"[probe] exit={r.returncode}")
    return r.returncode == 0

print('\n========= Step 1/4: stock-list --db-only =========')
ok1 = run_cli(['stock-list', '--db-only'], timeout=120)

print('\n========= Step 2/4: blocks --db-only =========')
ok2 = run_cli(['blocks', '--db-only'], timeout=300)

print('\n========= Step 3/4: daily --code 三只样本（不增量） =========')
ok3a = run_cli(['daily', '--db-only', '--code', '600570'], timeout=120)
ok3b = run_cli(['daily', '--db-only', '--code', '600519'], timeout=120)
ok3c = run_cli(['daily', '--db-only', '--code', '000001'], timeout=120)

print('\n========= Step 4/4: status =========')
ok4 = run_cli(['status', '--json'], timeout=60)

print(f"\n[probe] stock-list {'OK' if ok1 else 'FAIL'}")
print(f"[probe] blocks     {'OK' if ok2 else 'FAIL'}")
print(f"[probe] daily x3   {'OK' if ok3a and ok3b and ok3c else 'PARTIAL'}")
print(f"[probe] status     {'OK' if ok4 else 'FAIL'}")