"""Read-only, bounded deployment inventory; never prints credentials or private keys."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def run(argv):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=15)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired):
        return -1, '', ''


report = {'read_only': True, 'uid': os.getuid()}
nginx = shutil.which('nginx')
if not nginx:
    nginx = next((p for p in ['/usr/sbin/nginx', '/www/server/nginx/sbin/nginx',
                             '/usr/local/nginx/sbin/nginx'] if Path(p).is_file()), None)
report['nginx_binary'] = nginx
certificates = set()
renewal_names = set()
sections = []
if nginx:
    code, output, _ = run([nginx, '-T'])
    report['nginx_config_valid'] = code == 0
    pieces = re.split(r'^# configuration file ([^\n]+):\n', output, flags=re.M)
    for index in range(1, len(pieces), 2):
        filename, body = pieces[index:index + 2]
        if 'aihub0508.com' not in body:
            continue
        allowed = []
        for line in body.splitlines():
            s = line.strip()
            if re.match(r'^(server_name|listen|root|ssl_certificate|ssl_certificate_key|include|location|return)\s', s):
                # Do not print arbitrary return URLs that could carry query secrets.
                if s.startswith('return ') and not re.match(r'return\s+30[178]\s+https?://\$host\$request_uri;', s):
                    continue
                allowed.append(s)
            m = re.match(r'\s*ssl_certificate\s+([^;]+);', line)
            if m:
                path = Path(m.group(1).strip().strip('"'))
                certificates.add(str(path))
                if path.parent.parent.name == 'live':
                    renewal_names.add(path.parent.name)
        path = Path(filename)
        sections.append({'file': filename, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()
                         if path.is_file() else None, 'directives': allowed})
report['site_configuration'] = sections
report['certificates'] = []
for certificate in sorted(certificates):
    code, output, _ = run(['openssl', 'x509', '-in', certificate, '-noout', '-dates',
                            '-subject', '-issuer', '-fingerprint', '-sha256'])
    report['certificates'].append({'path': certificate, 'read_ok': code == 0,
                                    'metadata': output.strip()})
report['renewal_configs'] = []
for name in sorted(renewal_names):
    p = Path('/etc/letsencrypt/renewal') / (name + '.conf')
    if p.is_file():
        lines = [line.strip() for line in p.read_text().splitlines()
                 if re.match(r'\s*(authenticator|installer|webroot_path|server|key_type)\s*=', line)]
        report['renewal_configs'].append({'file': str(p), 'settings': lines})
for executable in ['certbot', 'python3.11', 'python3.12', 'systemctl']:
    report[executable] = shutil.which(executable)
report['acme_sh_present'] = (Path.home()/'.acme.sh/acme.sh').is_file()
report['certbot_timer'] = {key: run(['systemctl', key, 'certbot.timer'])[1].strip()
                          for key in ['is-active', 'is-enabled']}
_, timers, _ = run(['systemctl', 'list-timers', '--all', '--no-legend'])
report['certificate_timer_units'] = [line.split()[-2:] for line in timers.splitlines()
                                    if re.search(r'certbot|acme|cert-renew', line, re.I)]
_, cron, _ = run(['crontab', '-l'])
report['root_certificate_cron_entries'] = sum(bool(re.search(r'certbot|acme\.sh', line))
                                            for line in cron.splitlines() if not line.lstrip().startswith('#'))
_, listeners, _ = run(['ss', '-ltnH'])
report['ports_in_use'] = {str(port): bool(re.search(r':%d\s' % port, listeners)) for port in [80,443,8787,8788]}
report['native_service_state'] = run(['systemctl', 'is-active', 'amh-native.service'])[1].strip()
report['paths_exist'] = {p: Path(p).exists() for p in ['/opt/amh-native','/var/lib/amh-native',
                                                       '/opt/agent-memory-hub','/etc/systemd/system/amh-native.service']}
report['disk_free_mb'] = shutil.disk_usage('/var').free // 1048576
report['memory_available_mb'] = None
for line in Path('/proc/meminfo').read_text().splitlines():
    if line.startswith('MemAvailable:'):
        report['memory_available_mb'] = int(line.split()[1]) // 1024

report['renew_timer_details'] = run(['systemctl','show','certbot-renew.timer','--property=ActiveState,UnitFileState,LastTriggerUSec,NextElapseUSecRealtime'])[1].strip()
report['renew_service_command'] = [line.strip() for line in run(['systemctl','cat','certbot-renew.service'])[1].splitlines() if line.strip().startswith('ExecStart=') and 'certbot' in line]
_, journal, _ = run(['journalctl','-u','certbot-renew.service','-n','200','--no-pager','-o','cat'])
report['renewal_errors'] = []
for line in journal.splitlines():
    if re.search(r'Failed to renew|Failed to start|All renewals failed|no renewals|unauthorized|NXDOMAIN|Invalid response|Connection refused|timed out|not yet due', line, re.I):
        line = re.sub(r'/\.well-known/acme-challenge/[^\s\"]+', '/.well-known/acme-challenge/[challenge]', line)
        report['renewal_errors'].append(line[:400])
report['renewal_errors'] = report['renewal_errors'][-15:]
webroot=Path('/var/www/agent-memory-hub/current')
report['webroot_exists'] = webroot.is_dir()
report['webroot_resolved'] = str(webroot.resolve()) if webroot.exists() else None
report['challenge_directory_exists'] = (webroot/'.well-known/acme-challenge').is_dir()

print(json.dumps(report, sort_keys=True))
