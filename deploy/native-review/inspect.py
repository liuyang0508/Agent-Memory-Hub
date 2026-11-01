"""Read-only release/schema inventory. Never prints tokens, secrets or memory bodies."""
import json, os, re, sqlite3, subprocess
from pathlib import Path
current=Path('/opt/amh-native/current').resolve()
result={'release':str(current),'service':subprocess.check_output(['systemctl','is-active','amh-native'],text=True).strip()}
version=current/'venv/lib/python3.11/site-packages/agent_brain/_version.py'
if version.exists():
    match=re.search(r'__version__\s*=\s*[\'"]([^\'"]+)',version.read_text())
    result['package_version']=match.group(1) if match else 'unknown'
config={}
env=Path('/etc/amh-native/feishu.env')
if env.exists():
    for line in env.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and key in ('FEISHU_APP_ID','FEISHU_TENANT','FEISHU_ALLOWED_TENANT_KEYS','AMH_HUB_PUBLIC_URL'):
            config[key]=value.strip().strip('\'"')
    result['env_mode']=oct(env.stat().st_mode & 0o777)
result['public_configuration']=config
conn=sqlite3.connect('file:/var/lib/amh-native/hub.sqlite?mode=ro',uri=True)
conn.row_factory=sqlite3.Row
conn.execute('BEGIN')
result['integrity']=conn.execute('PRAGMA quick_check').fetchone()[0]
result['assets']=[dict(r) for r in conn.execute("SELECT json_extract(payload,'$.deleted') deleted,COUNT(*) count FROM assets GROUP BY deleted")]
result['metadata_sidecars']=[dict(r) for r in conn.execute("SELECT json_extract(payload,'$.deleted') deleted,COUNT(*) count FROM assets WHERE json_extract(payload,'$.kind')='transcript' AND json_extract(payload,'$.path') LIKE '%.meta.json' GROUP BY deleted")]
result['unknown_active']=conn.execute("SELECT count(*) FROM assets WHERE NOT json_extract(payload,'$.deleted') AND json_extract(payload,'$.producer_unknown')").fetchone()[0]
result['members']=conn.execute('SELECT count(*) FROM members').fetchone()[0]
result['legacy_feishu_accounts']=conn.execute('SELECT count(*) FROM feishu_members').fetchone()[0]
result['tenant_pin']=[dict(r) for r in conn.execute("SELECT key,value FROM settings WHERE key IN ('feishu_tenant_key','legacy-feishu-scope')")]
result['change_cursor']=conn.execute('SELECT max(seq) FROM changes').fetchone()[0]
conn.close()
print(json.dumps(result,ensure_ascii=False,indent=2))
