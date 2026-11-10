import sys
sys.path.insert(0, 'D:\\bully_simulation_pack_v2_20260713\\fin_ver\\web')
import database as db
db.init_db()
conn = db.get_conn()

for table in ['user_participants', 'shared_participants', 'user_scenarios', 'shared_scenarios']:
    try:
        info = conn.execute(f'PRAGMA table_info({table})').fetchall()
        print(f'{table} columns:')
        for r in info:
            print(f'  {r["name"]} ({r["type"]})')
    except Exception as e:
        print(f'{table}: {e}')
    print()

conn.close()
