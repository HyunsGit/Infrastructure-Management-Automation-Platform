# app.py
from flask import Flask, Response, render_template, request, redirect, url_for, session, jsonify, flash, stream_with_context
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired
import os, re, redis, threading, time, requests, subprocess, paramiko
import pandas as pd, concurrent.futures, logging, socket, json, boto3, pyotp, qrcode, io, base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from env_manager import get_env_var
from celery_app import celery_app, create_volumes_task, attach_volumes_task
from celery.result import AsyncResult
from werkzeug.security import generate_password_hash, check_password_hash
from botocore.exceptions import ClientError
from datetime import timedelta, datetime
from time import sleep

# ── Extensions (db, csrf, login_manager, limiter) ─────────────
from extensions import db, csrf, login_manager, limiter

# ── Models ────────────────────────────────────────────────────
from models import (DBUser, IAMHistory, SeenNotification, SeenKcNotice,
                    Notification, AppHistory, AppChangelog, DeletedProject, Project,
                    SCodeInfo, SCodeRule, SCodeVmSnapshot, SCodeSnapshot,
                    _init_db)

# ── Helpers ───────────────────────────────────────────────────
from helpers import (
    _get_all_scode_rules, _resolve_scode, _apply_scodes_for_project,
    _fetch_kc_token, _write_tokens_file, _seed_projects_from_tokens,
    _log_history, get_projects_from_dotenv, _list_all_vms_by_project,
    _find_kakaocloud_vm, _find_vm_across_all_projects,
    _is_ubuntu_24_or_higher, _get_os_type,
    _load_pem_key, _get_ssh_client, _resolve_pem_path, _get_ssh_client_with_retry,
    _get_puppet_cert_list, _check_puppet_cert_status,
    _build_ansible_inventory, PROJECT_CREDS,
    IAM_URL, TOKEN_FILE, TOKEN_REFRESH_INTERVAL, TOKEN_CHECK_INTERVAL,
    PUPPET_MASTER, PEM_DIR, SSH_USERNAME, SSH_PASSWORD,
    KAKAOCLOUD_COMPUTE_URL, EXCLUDED_KEYS, _puppet_cert_cache,
)

# ── Background threads ────────────────────────────────────────
from background import start_background_threads, _take_scode_snapshot

# ── VM 생성 feature ────────────────────────────────────────────
import vm_create

# ── Flask app setup ───────────────────────────────────────────
app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
if not app.logger.hasHandlers():
    app.logger.addHandler(handler)
else:
    app.logger.handlers.clear()
    app.logger.addHandler(handler)

secret_key     = get_env_var('FLASK_SECRET_KEY', required=True)
app.secret_key = secret_key

app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:////etc/mgt-api/api/infrapilot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME']     = timedelta(hours=1)
app.config['SESSION_COOKIE_HTTPONLY']        = True
app.config['SESSION_COOKIE_SAMESITE']        = 'Lax'
app.config['REMEMBER_COOKIE_DURATION']       = 9 * 3600  # 9 hours in seconds
app.config['REMEMBER_COOKIE_HTTPONLY']       = True
app.config['REMEMBER_COOKIE_SAMESITE']       = 'Lax'
app.config['SESSION_PROTECTION']             = 'basic'

# Init extensions with app
db.init_app(app)
csrf.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'
limiter.init_app(app)

# ── Flask-Login ───────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, username):
        self.id = username

    def get_id(self):
        return self.id


@app.context_processor
def inject_helpers():
    def get_db_user(username):
        return DBUser.query.get(username)
    def has_role(*roles):
        if not current_user.is_authenticated:
            return False
        u = DBUser.query.get(current_user.get_id())
        return u and u.role in roles
    return dict(get_db_user=get_db_user, timedelta=timedelta, has_role=has_role)


def require_role(*roles):
    """Decorator to restrict route access by role."""
    from functools import wraps
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            u = DBUser.query.get(current_user.get_id())
            if not u or u.role not in roles:
                return redirect(url_for('insufficient_role'))
            return f(*args, **kwargs)
        return wrapped
    return decorator


@login_manager.user_loader
def load_user(user_id):
    if DBUser.query.get(user_id):
        return User(user_id)
    return None



# Use this function to load projects from tokens file (same as original)
dotenv_token_path = '/etc/mgt-api/.tokens'


def get_projects_from_dotenv():
    projects = []
    try:
        with open(dotenv_token_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    var = line.split('=')[0]
                    projects.append(var)
    except Exception:
        pass
    return projects


PROJECTS = get_projects_from_dotenv()

# List of hosts only
MONITOR_HOSTS = [
    # List of VPC subnet host identifiers to monitor for PING/SSH/DNS connectivity.
    # Example: "vpc-prod-a", "vpc-prod-b"
]

PRIVATE_IP_MAP = {
    # Maps host identifier -> CIDR subnet string.
    # Example: "vpc-prod-a": "10.0.0.0/24",
}

# Shared SSH credentials for all hosts
SSH_USERNAME = "scv"
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", "")

def ping_host(host, timeout=2):
    try:
        result = subprocess.run(
            ["/bin/ping", "-c", "2", "-W", str(timeout), host],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout + 1
        )
        return result.returncode == 0
    except Exception:
        return False

def check_ssh(host, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=3):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(host, username=username, password=password, timeout=timeout)
        ssh.close()
        return True
    except Exception:
        return False

def check_dns_inside_vm(host, domain="www.naver.com", username=SSH_USERNAME, password=SSH_PASSWORD, timeout=3):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=username, password=password, timeout=timeout)

        stdin, stdout, stderr = ssh.exec_command(f"nslookup {domain}")
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode().lower()
        error = stderr.read().decode().lower()
        ssh.close()

        failure_indicators = [
            "server can't find",
            "non-existent domain",
            "name or service not known",
            "timed out",
            "no servers could be reached",
            "no answer",
            "failure",
            "refused",
            "could not",
            "unknown host",
            "servfail",
            "nx domain"
        ]

        failed = any(keyword in output for keyword in failure_indicators) or any(keyword in error for keyword in failure_indicators)
        return not failed
    except Exception:
        return False

def check_host(host):
    ping_status = ssh_status = dns_status = False
    try:
        ping_status = ping_host(host)
    except Exception:
        pass
    try:
        ssh_status = check_ssh(host)
    except Exception:
        pass
    try:
        dns_status = check_dns_inside_vm(host)
    except Exception:
        pass
    return {
        'host': host,
        'ping': ping_status,
        'ssh': ssh_status,
        'dns': dns_status
    }

ANSIBLE_PLAYBOOK_PATH = get_env_var('ANSIBLE_PLAYBOOK_PATH',
                                    default='/etc/ansible/playbooks/site.yml')
PUPPET_APPLY_CMD      = get_env_var('PUPPET_APPLY_CMD',
                                    default='puppet agent --test')
ROUTE53_HOSTED_ZONE_ID = get_env_var('ROUTE53_HOSTED_ZONE_ID', default='')
REDIS_CLIENT = redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)
PROVISIONING_KEY_PREFIX = 'provisioning:'


def _redis_get_state(hostname: str) -> dict:
    raw = REDIS_CLIENT.get(f'{PROVISIONING_KEY_PREFIX}{hostname}')
    if raw:
        try: return json.loads(raw)
        except Exception: pass
    return {'ansible': False, 'puppet': False, 'ansible_log': '', 'puppet_log': ''}


def _redis_set_state(hostname: str, state: dict):
    REDIS_CLIENT.set(f'{PROVISIONING_KEY_PREFIX}{hostname}', json.dumps(state))


KAKAOCLOUD_COMPUTE_URL  = 'https://compute.your-cloud.example.com/api/v1/instances'
 
 
 
def _find_kakaocloud_vm(hostname: str, token: str) -> dict | None:
    """Search KakaoCloud instances for a VM whose name matches the hostname."""
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token,
    }
    marker = None
    try:
        while True:
            params = {'limit': 200}
            if marker:
                params['marker'] = marker
            resp = requests.get(KAKAOCLOUD_COMPUTE_URL, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for inst in data.get('instances', []):
                if hostname in inst.get('name', ''):
                    return inst
            marker = data.get('next_marker') or data.get('next')
            if not marker:
                break
    except Exception as e:
        app.logger.error(f'KakaoCloud lookup error: {e}')
    return None

def _list_all_vms_by_project(project_key: str) -> list[dict]:
    """Return all VMs for a given project token key."""
    token = get_env_var(project_key, default='')
    if not token or token.strip().lower() == 'none':
        return []
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-Auth-Token': token,
    }
    vms = []
    marker = None
    try:
        while True:
            params = {'limit': 200}
            if marker:
                params['marker'] = marker
            resp = requests.get(KAKAOCLOUD_COMPUTE_URL, headers=headers,
                                params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            instances = data.get('instances', [])
            for inst in instances:
                inst['_project_key'] = project_key  # tag with project
            vms.extend(instances)
            marker = data.get('next_marker') or data.get('next')
            if not marker:
                break
    except Exception as e:
        app.logger.debug(f'VM list error for project {project_key}: {e}')
    return vms


PUPPET_MASTER = 'puppet-master'
_puppet_cert_cache = {'data': None, 'ts': 0}
_puppet_cert_cache_ttl = 60  # seconds


def _get_puppet_cert_list() -> dict:
    """
    Fetch all certs from puppet master, cached for 60 seconds.
    Returns dict: { hostname_short: 'signed' | 'requested' }
    """
    import time
    now = time.time()
    if _puppet_cert_cache['data'] is not None and (now - _puppet_cert_cache['ts']) < _puppet_cert_cache_ttl:
        return _puppet_cert_cache['data']

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    result = {}
    try:
        ssh.connect(PUPPET_MASTER, username=SSH_USERNAME, password=SSH_PASSWORD, timeout=8)
        _, stdout, _ = ssh.exec_command('sudo puppetserver ca list --all 2>/dev/null', timeout=30)
        output = stdout.read().decode()
        ssh.close()

        in_signed = False
        in_requested = False
        for line in output.splitlines():
            if 'Signed Certificates:' in line:
                in_signed = True; in_requested = False; continue
            if 'Requested Certificates:' in line:
                in_requested = True; in_signed = False; continue
            # Extract short hostname from FQDN
            line = line.strip()
            if not line: continue
            # Line format: "  hostname.internal.example.com    (SHA256) ..."
            parts = line.split()
            if parts:
                fqdn = parts[0]
                short = fqdn.split('.')[0]
                if in_signed:
                    result[short] = 'signed'
                    result[fqdn]  = 'signed'
                elif in_requested:
                    result[short] = 'requested'
                    result[fqdn]  = 'requested'

        _puppet_cert_cache['data'] = result
        _puppet_cert_cache['ts']   = now
    except Exception as e:
        app.logger.debug(f'Puppet master cert list failed: {e}')

    return result


def _check_puppet_cert_status(hostname: str) -> str:
    """
    Check cert status for a hostname using cached puppet master cert list.
    Returns: 'signed' | 'requested' | 'none' | 'error'
    """
    short = hostname.split('.')[0]
    try:
        cert_list = _get_puppet_cert_list()
        if not cert_list:
            return 'error'
        return cert_list.get(short, cert_list.get(hostname, 'none'))
    except Exception:
        return 'error'


def _check_ansible_plays_detail(hostname: str, key_name: str, image: str,
                                private_ip: str, os_type: str) -> list:
    """
    Run detailed per-play checks for ansible provisioning status.
    Returns list of { name, done, detail } dicts.
    """
    fqdn = f'{hostname}.internal.example.com'
    plays = []

    # ── SSH checks ─────────────────────────────────────────────
    ssh, _ = _get_ssh_client_with_retry(hostname, key_name, image, private_ip, retries=2, interval=1)
    if ssh is None:
        for name in ['install kc_monitor_agent', 'Update Hostname',
                     'Install Puppet agent', 'Deploy Facter NTP facts', 'Set Time Zone']:
            plays.append({'name': name, 'done': False, 'detail': 'SSH 연결 실패'})
        return plays, None

    def ssh_run(cmd):
        try:
            _, out, _ = ssh.exec_command(cmd, timeout=10)
            return out.read().decode().strip()
        except Exception:
            return ''

    # ── Play 2: kc_monitor_agent ───────────────────────────────
    kic = ssh_run('test -f /etc/default/kic_monitor_agent && echo exists || echo missing')
    plays.append({
        'name':   'install kc_monitor_agent',
        'done':   kic == 'exists',
        'detail': '/etc/default/kic_monitor_agent ' + ('확인됨' if kic == 'exists' else '없음 ✗'),
    })

    # ── Play 3: Update Hostname ────────────────────────────────
    current_hostname = ssh_run('hostname')
    hostname_ok = current_hostname == hostname
    plays.append({
        'name':   'Update Hostname',
        'done':   hostname_ok,
        'detail': f'hostname = {current_hostname} {"" if hostname_ok else f"✗ (expected: {hostname})"}',
    })

    if os_type == 'ubuntu24':
        # ── Play 4: Install Puppet agent ──────────────────────
        puppet_conf = ssh_run('test -f /etc/puppet/puppet.conf && echo exists || echo missing')
        plays.append({
            'name':   'Install Puppet agent',
            'done':   puppet_conf == 'exists',
            'detail': '/etc/puppet/puppet.conf ' + ('확인됨' if puppet_conf == 'exists' else '없음 ✗'),
        })

        # ── Play 5: Facter NTP facts ───────────────────────────
        facter = ssh_run('test -f /etc/facter/facts.d/gather_ntp_facts.sh && echo exists || echo missing')
        plays.append({
            'name':   'Deploy Facter NTP facts',
            'done':   facter == 'exists',
            'detail': '/etc/facter/facts.d/gather_ntp_facts.sh ' + ('확인됨' if facter == 'exists' else '없음 ✗'),
        })
    else:
        is_rocky = 'rocky' in image.lower()

        # ── Play 4: Create Default OS users ───────────────────
        scv_user = ssh_run('id scv 2>/dev/null && echo exists || echo missing')
        users_ok = 'exists' in scv_user or 'uid' in scv_user
        plays.append({
            'name':   'Create Default OS users',
            'done':   users_ok,
            'detail': 'scv 계정 확인됨' if users_ok else 'scv 계정 없음 ✗',
        })

        # ── Play 5: Modify Ulimit ──────────────────────────────
        # Ansible sets 4 entries: root soft/hard nofile + * soft/hard nofile = all 655350
        ulimit = ssh_run('grep "655350" /etc/security/limits.conf 2>/dev/null | wc -l || echo 0')
        try: ulimit_count = int(ulimit.strip().split()[0])
        except: ulimit_count = 0
        ulimit_ok = ulimit_count >= 4
        plays.append({
            'name':   'Modify Ulimit',
            'done':   ulimit_ok,
            'detail': f'/etc/security/limits.conf 655350 {ulimit_count}개 항목 확인됨' if ulimit_ok else f'ulimit 655350 항목 부족 ({ulimit_count}개) ✗',
        })

        # ── Play 6: Install filebeats ──────────────────────────
        filebeat = ssh_run('systemctl is-active system-filebeat8 2>/dev/null || echo inactive')
        filebeat_ok = filebeat.strip() == 'active'
        plays.append({
            'name':   'Install filebeats',
            'done':   filebeat_ok,
            'detail': 'system-filebeat8 active' if filebeat_ok else f'system-filebeat8 {filebeat.strip()} ✗',
        })

        # ── Play 7: Install KC-Node-Exporter ──────────────────
        node_exp = ssh_run('test -f /usr/local/bin/node_exporter && echo exists || echo missing')
        plays.append({
            'name':   'Install KC-Node-Exporter',
            'done':   node_exp == 'exists',
            'detail': '/usr/local/bin/node_exporter 확인됨' if node_exp == 'exists' else '없음 ✗',
        })

        # ── Play 8: Install Promtail ───────────────────────────
        promtail = ssh_run('test -f /etc/promtail/promtail-config.yaml && echo exists || echo missing')
        plays.append({
            'name':   'Install Promtail',
            'done':   promtail == 'exists',
            'detail': '/etc/promtail/promtail-config.yaml 확인됨' if promtail == 'exists' else '없음 ✗',
        })

        # ── Play 9: Register DNS Server ────────────────────────
        if is_rocky:
            dns = ssh_run('sudo grep -c "search internal.example.com" /etc/resolv.conf 2>/dev/null || echo 0')
            dns_ok = dns.strip().splitlines()[0].strip() not in ('', '0')
            dns_detail = 'search internal.example.com in /etc/resolv.conf 확인됨' if dns_ok else '/etc/resolv.conf search internal.example.com 없음 ✗'
        else:
            dns_file  = ssh_run('test -f /etc/systemd/resolved.conf && echo exists || echo missing')
            dns_entry = ssh_run('grep -c "internal.example.com" /etc/systemd/resolved.conf 2>/dev/null || echo 0')
            file_ok   = dns_file.strip() == 'exists'
            no_kr2    = dns_entry.strip().splitlines()[0].strip() in ('', '0')
            dns_ok    = file_ok and no_kr2
            if not file_ok:
                dns_detail = '/etc/systemd/resolved.conf 파일 없음 ✗'
            elif not no_kr2:
                dns_detail = '/etc/systemd/resolved.conf에 internal.example.com 존재 — ansible 미적용 ✗'
            else:
                dns_detail = '/etc/systemd/resolved.conf 존재 + internal.example.com 없음 확인됨'
        plays.append({
            'name':   'Register Private DNS Server',
            'done':   dns_ok,
            'detail': dns_detail,
        })

        # ── Play 10: Register NTP Server ──────────────────────
        if is_rocky:
            ntp = ssh_run('grep -c "infra-iet-ntp-01" /etc/chrony.conf 2>/dev/null || echo 0')
            ntp_ok = ntp.strip().splitlines()[0].strip() not in ('', '0')
            ntp_detail = 'infra-iet-ntp-01 NTP 등록 확인됨' if ntp_ok else 'NTP 설정 없음 ✗'
        else:
            ntp = ssh_run('grep -c "infra-iet-ntp-01" /etc/chrony/chrony.conf 2>/dev/null || echo 0')
            ntp_chrony_ok = ntp.strip().splitlines()[0].strip() not in ('', '0')
            # Also accept if systemd-timesyncd is active
            timesyncd = ssh_run('systemctl is-active systemd-timesyncd 2>/dev/null || echo inactive')
            timesyncd_ok = timesyncd.strip() == 'active'
            ntp_ok = ntp_chrony_ok or timesyncd_ok
            if ntp_chrony_ok:
                ntp_detail = 'infra-iet-ntp-01 NTP 등록 확인됨'
            elif timesyncd_ok:
                ntp_detail = 'systemd-timesyncd active 확인됨'
            else:
                ntp_detail = 'NTP 설정 없음 ✗'
        plays.append({
            'name':   'Register NTP Server',
            'done':   ntp_ok,
            'detail': ntp_detail,
        })

        # ── Play 11: Password Authentication SSH ───────────────
        # File requires sudo to read on Rocky
        ssh_auth = ssh_run('sudo grep -cE "^AllowUsers.*scv" /etc/ssh/sshd_config 2>/dev/null || echo 0')
        try: ssh_auth_count = int(ssh_auth.strip().splitlines()[0])
        except: ssh_auth_count = 0
        ssh_auth_ok = ssh_auth_count > 0
        plays.append({
            'name':   'Password Authentication SSH',
            'done':   ssh_auth_ok,
            'detail': 'AllowUsers scv 확인됨' if ssh_auth_ok else 'AllowUsers scv 없음 ✗',
        })

    # ── Play last: Timezone ────────────────────────────────────
    tz = ssh_run('cat /etc/timezone 2>/dev/null || timedatectl | grep "Time zone" | awk \'{print $3}\'')
    tz_ok = 'Asia/Seoul' in tz
    plays.append({
        'name':   'Set Time Zone',
        'done':   tz_ok,
        'detail': f'timezone = {tz or "unknown"} {"" if tz_ok else "✗ (expected: Asia/Seoul)"}',
    })

    # Don't close ssh — return it so caller can reuse for puppet check
    return plays, ssh



    """
    SSH into target VM and check if puppet has completed a full run.
    Checks for last_run_summary.yaml — only created after cert acceptance
    and successful catalog apply.
    Returns: True (done), False (not done), None (SSH error)
    """
    ssh, _ = _get_ssh_client_with_retry(hostname, key_name, image, private_ip)
    if ssh is None:
        return None
    try:
        _, stdout, _ = ssh.exec_command(
            'test -f /var/cache/puppet/public/last_run_summary.yaml && echo exists || echo missing',
            timeout=10)
        result = stdout.read().decode().strip()
        ssh.close()
        return result == 'exists'
    except Exception as e:
        app.logger.debug(f'Puppet check failed for {hostname}: {e}')
        try: ssh.close()
        except Exception: pass
        return None


def _is_ubuntu_24_or_higher(image_name: str) -> bool:
    """Check if KakaoCloud image name indicates Ubuntu 24.04 or higher."""
    import re
    match = re.search(r'Ubuntu\s+(\d+)\.(\d+)', image_name, re.IGNORECASE)
    if not match:
        return False
    major, minor = int(match.group(1)), int(match.group(2))
    return major > 24 or (major == 24 and minor >= 4)


def _get_os_type(image_name: str) -> str:
    """
    Determine OS type for provisioning logic.
    Returns:
      'ubuntu24' — Ubuntu 24.04+ → TOBE-Default-Playbook-V3.yaml + puppet
      'legacy'   — Ubuntu 22/20/18 or Rocky → Default-Playbook-V3.yaml, no puppet
      'skip'     — K8s node, Windows, or unknown → skip all checks
    """
    import re
    if not image_name or image_name == 'K8s node':
        return 'skip'
    if 'windows' in image_name.lower():
        return 'skip'
    if re.search(r'Ubuntu\s+24', image_name, re.IGNORECASE):
        return 'ubuntu24'
    if re.search(r'Ubuntu\s+(18|20|22)', image_name, re.IGNORECASE):
        return 'legacy'
    if 'rocky' in image_name.lower():
        return 'legacy'
    return 'skip'


EXCLUDED_KEYS = {
    'ROUTE53_HOSTED_ZONE_ID', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
    'AWS_REGION', 'ANSIBLE_PLAYBOOK_PATH', 'PUPPET_APPLY_CMD', 'FLASK_SECRET_KEY',
    'ADMIN_USERNAME', 'ADMIN_PASSWORD'
}

# Maps KakaoCloud project_id → (access_id, secret_key)
PROJECT_CREDS = {
    # Maps project_id -> (access_id, secret_key)
    # Loaded from environment variables or a secrets manager in production.
    # Example:
    # '<project_id>': (os.environ.get('KC_ACCESS_ID_1', ''), os.environ.get('KC_SECRET_KEY_1', '')),
}


def _find_vm_across_all_projects(hostname: str) -> dict | None:
    """Search every KakaoCloud project token for a VM matching the hostname."""
    short_name = hostname.split('.')[0]
    for project_key in get_projects_from_dotenv():
        if project_key in EXCLUDED_KEYS:
            continue
        token = get_env_var(project_key, default='')
        if not token or token.strip().lower() == 'none':
            continue
        try:
            vm = _find_kakaocloud_vm(short_name, token)
            if vm:
                return vm
        except Exception as e:
            app.logger.debug(f'Skipping project {project_key}: {e}')
            continue
    return None


@app.route('/connectivity_status')
@login_required
def connectivity_status():
    status_list = []

    # Increased max_workers for higher concurrency 
    max_workers = 30    
    timeout = 60  # Increased total timeout for all hosts

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_host, host): host for host in MONITOR_HOSTS}

        done, not_done = concurrent.futures.wait(futures, timeout=timeout)
        
        for future in done:
            try:
                status_list.append(future.result())
            except Exception:
                host = futures[future]
                status_list.append({
                    'host': host,
                    'ping': False,
                    'ssh': False,
                    'dns': False,
                    'error': 'exception'
                })
        
        # Handle hosts that did not complete in time
        for future in not_done:
            host = futures[future]
            status_list.append({
                'host': host,
                'ping': False,
                'ssh': False,
                'dns': False,
                'error': 'timeout'
            })

    return jsonify(status_list)

@app.route('/check_connectivity')
@login_required
def check_connectivity():
    return render_template('check_connectivity.html', MONITOR_HOSTS=MONITOR_HOSTS, PRIVATE_IP_MAP=PRIVATE_IP_MAP)


# Login Form with Flask-WTF
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(message="아이디를 입력해주세요.")])
    password = PasswordField('Password', validators=[DataRequired(message="비밀번호를 입력해주세요.")])
    submit = SubmitField('Login')



@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    return redirect(url_for('login'))



# Rate limit login: e.g. 5 per minute per IP
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        session.clear()
        logout_user()
        return redirect(url_for('signup'))
    error = None
    if request.method == 'POST':
        username    = request.form.get('username', '').strip()
        email       = request.form.get('email', '').strip()
        password    = request.form.get('password', '').strip()
        confirm     = request.form.get('confirm_password', '').strip()
        role        = request.form.get('role', 'viewer')
        reason      = request.form.get('reason', '').strip()

        if not username or not password:
            error = '사용자명과 비밀번호는 필수입니다.'
        elif len(password) < 8:
            error = '비밀번호는 8자 이상이어야 합니다.'
        elif password != confirm:
            error = '비밀번호가 일치하지 않습니다.'
        elif DBUser.query.get(username):
            error = '이미 존재하는 사용자명입니다.'
        elif role not in ('admin', 'operator', 'viewer'):
            error = '올바른 역할을 선택해주세요.'
        elif role != 'viewer' and not reason:
            error = 'Viewer 외의 역할을 요청하려면 사유를 입력해주세요.'
        else:
            new_user = DBUser(
                username             = username,
                password             = generate_password_hash(password),
                email                = email or None,
                role                 = role,
                role_reason          = reason or None,
                status               = 'pending',
                must_change_password = False,
                is_admin             = False,
            )
            db.session.add(new_user)
            db.session.add(IAMHistory(
                username       = username,
                action         = 'signup_request',
                role_requested = role,
                reason         = reason or None,
                created_at     = datetime.utcnow(),
            ))
            db.session.commit()
            flash('가입 요청이 완료되었습니다. 관리자 승인 후 로그인이 가능합니다.', 'success')
            return redirect(url_for('login'))
    return render_template('signup.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('summary'))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data
        db_user  = DBUser.query.get(username)
        if db_user and check_password_hash(db_user.password, password):
            # Block pending/rejected users
            if db_user.status == 'pending':
                flash('계정이 아직 승인 대기 중입니다. 관리자에게 문의하세요.', 'warning')
                return render_template('login.html', form=form)
            if db_user.status == 'rejected':
                flash('계정 가입이 거절되었습니다. 관리자에게 문의하세요.', 'danger')
                return render_template('login.html', form=form)
            session.permanent = True
            if db_user.must_change_password:
                session['change_pw_user'] = username
                return redirect(url_for('change_password'))
            session['2fa_user'] = username
            if not db_user.totp_secret:
                db_user.totp_secret = pyotp.random_base32()
                db.session.commit()
                return redirect(url_for('setup_2fa'))
            return redirect(url_for('verify_2fa'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'danger')
    return render_template('login.html', form=form)


@app.route('/setup-2fa', methods=['GET', 'POST'])
def setup_2fa():
    username = session.get('2fa_user')
    db_user  = DBUser.query.get(username) if username else None
    if not db_user:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        totp = pyotp.TOTP(db_user.totp_secret)
        if totp.verify(code, valid_window=2):
            db_user.last_login = datetime.utcnow() + timedelta(hours=9)
            db.session.commit()
            session.pop('2fa_user', None)
            login_user(User(username), remember=True, duration=timedelta(hours=9))
            session.permanent = True
            app.logger.info(f'login_user called for {username}, _remember={session.get("_remember")}, _remember_seconds={session.get("_remember_seconds")}')
            _log_history('IAM', f'로그인: {username}', request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip(), username)
            flash('2단계 인증이 설정되었습니다.', 'success')
            return redirect(url_for('summary'))
        flash('인증 코드가 올바르지 않습니다. 다시 시도해주세요.', 'danger')

    totp    = pyotp.TOTP(db_user.totp_secret)
    otp_uri = totp.provisioning_uri(name=username, issuer_name='InfraPilot')
    img     = qrcode.make(otp_uri)
    buf     = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64  = base64.b64encode(buf.getvalue()).decode()
    return render_template('setup_2fa.html', qr_b64=qr_b64,
                           secret=db_user.totp_secret, username=username)


@app.route('/verify-2fa', methods=['GET', 'POST'])
def verify_2fa():
    username = session.get('2fa_user')
    db_user  = DBUser.query.get(username) if username else None
    if not db_user:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        totp = pyotp.TOTP(db_user.totp_secret)
        app.logger.debug(f'2FA verify: user={username} entered={code} expected={totp.now()}')
        if totp.verify(code, valid_window=2):
            db_user.last_login = datetime.utcnow() + timedelta(hours=9)
            db.session.commit()
            session.pop('2fa_user', None)
            login_user(User(username), remember=True, duration=timedelta(hours=9))
            session.permanent = True
            app.logger.info(f'login_user called for {username}, _remember={session.get("_remember")}, _remember_seconds={session.get("_remember_seconds")}')
            _log_history('IAM', f'로그인: {username}', request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip(), username)
            return redirect(request.args.get('next') or url_for('home'))
        flash('인증 코드가 올바르지 않습니다.', 'danger')
    return render_template('verify_2fa.html', username=username)


@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    username = session.get('change_pw_user')
    db_user  = DBUser.query.get(username) if username else None
    if not db_user:
        return redirect(url_for('login'))

    error = None
    if request.method == 'POST':
        new_pw  = request.form.get('new_password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()
        if len(new_pw) < 8:
            error = '비밀번호는 8자 이상이어야 합니다.'
        elif new_pw != confirm:
            error = '비밀번호가 일치하지 않습니다.'
        else:
            db_user.password             = generate_password_hash(new_pw)
            db_user.must_change_password = False
            db.session.commit()
            session.pop('change_pw_user', None)
            session['2fa_user'] = username
            if not db_user.totp_secret:
                db_user.totp_secret = pyotp.random_base32()
                db.session.commit()
                return redirect(url_for('setup_2fa'))
            return redirect(url_for('verify_2fa'))
    return render_template('change_password.html', username=username, error=error)


@app.route('/iam')
@login_required
@require_role('admin')
def iam():
    users    = DBUser.query.filter(DBUser.status == 'active').order_by(DBUser.username).all()
    pending  = DBUser.query.filter(DBUser.status == 'pending').order_by(DBUser.created_at.desc()).all()
    rejected = DBUser.query.filter(DBUser.status == 'rejected').order_by(DBUser.created_at.desc()).all()
    projects = Project.query.order_by(Project.zone).all()
    history  = IAMHistory.query.order_by(IAMHistory.created_at.desc()).limit(100).all()
    return render_template('iam.html', users=users, pending=pending,
                           rejected=rejected, projects=projects, history=history,
                           now=datetime.utcnow())


@app.route('/iam/update_user', methods=['POST'])
@login_required
@require_role('admin')
def iam_update_user():
    action   = request.form.get('action')
    username = request.form.get('username', '').strip()
    admin    = current_user.get_id()
    now      = datetime.utcnow()

    # Handle create action before querying db_user
    if action == 'create':
        temp_pw = request.form.get('temp_password', '').strip()
        role    = request.form.get('role', 'operator')
        if not username or not temp_pw:
            flash('사용자명과 임시 비밀번호를 입력해주세요.', 'danger')
            return redirect(url_for('iam'))
        if len(temp_pw) < 6:
            flash('임시 비밀번호는 6자 이상이어야 합니다.', 'danger')
            return redirect(url_for('iam'))
        if DBUser.query.get(username):
            flash(f'이미 존재하는 사용자명입니다: {username}', 'danger')
            return redirect(url_for('iam'))
        if role not in ('admin', 'operator', 'viewer'):
            role = 'operator'
        db.session.add(DBUser(
            username             = username,
            password             = generate_password_hash(temp_pw),
            must_change_password = True,
            is_admin             = (role == 'admin'),
            role                 = role,
            status               = 'active',
        ))
        db.session.add(IAMHistory(
            username       = username, action = 'approved',
            role_requested = role,
            decided_by     = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'사용자 생성: {username}', f'역할: {role}', admin)
        flash(f'사용자 {username} 생성 완료. (역할: {role})', 'success')
        return redirect(url_for('iam'))

    db_user  = DBUser.query.get(username)
    if not db_user:
        flash(f'사용자를 찾을 수 없습니다: {username}', 'danger')
        return redirect(url_for('iam'))

    if action == 'approve':
        approved_role = request.form.get('approved_role', db_user.role) or db_user.role
        if approved_role not in ('admin', 'operator', 'viewer'):
            approved_role = 'viewer'
        db_user.status   = 'active'
        db_user.role     = approved_role
        db_user.is_admin = (approved_role == 'admin')
        db.session.add(IAMHistory(
            username       = username, action = 'approved',
            role_requested = approved_role,
            decided_by     = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'사용자 승인: {username}', f'역할: {approved_role}, 승인자: {admin}', admin)
        flash(f'{username} 승인 완료. (역할: {approved_role})', 'success')

    elif action == 'reject':
        reason = request.form.get('reason', '').strip()
        db_user.status = 'rejected'
        db.session.add(IAMHistory(
            username   = username, action = 'rejected',
            reason     = reason or None,
            decided_by = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'사용자 거절: {username}', reason, admin)
        flash(f'{username} 거절 완료.', 'success')

    elif action == 'update_role':
        old_role = db_user.role
        role = request.form.get('role', 'viewer')
        if role not in ('admin', 'operator', 'viewer'):
            role = 'viewer'
        db_user.role     = role
        db_user.is_admin = (role == 'admin')
        db.session.add(IAMHistory(
            username       = username, action = 'role_changed',
            role_requested = role,
            reason         = f'{old_role} → {role}',
            decided_by     = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'역할 변경: {username}', f'{old_role} → {role}', admin)
        flash(f'{username} 역할이 {role}로 변경되었습니다.', 'success')

    elif action == 'delete':
        if username == admin:
            flash('자기 자신을 삭제할 수 없습니다.', 'danger')
        else:
            db.session.add(IAMHistory(
                username   = username, action = 'deleted',
                decided_by = admin, created_at = now, decided_at = now,
            ))
            db.session.delete(db_user)
            db.session.commit()
            _log_history('IAM', f'사용자 삭제: {username}', None, admin)
            flash(f'{username} 삭제 완료.', 'success')

    elif action == 'reset_totp':
        db_user.totp_secret = None
        db.session.add(IAMHistory(
            username   = username, action = 'totp_reset',
            decided_by = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'2FA 초기화: {username}', None, admin)
        flash(f'{username} 2FA 초기화 완료.', 'success')

    elif action == 'reset_password':
        new_pw = request.form.get('new_password', '').strip()
        if not new_pw:
            flash('새 임시 비밀번호를 입력해주세요.', 'danger')
            return redirect(url_for('iam') + '#users')
        if len(new_pw) < 6:
            flash('임시 비밀번호는 6자 이상이어야 합니다.', 'danger')
            return redirect(url_for('iam') + '#users')
        db_user.password             = generate_password_hash(new_pw)
        db_user.must_change_password = True
        db.session.add(IAMHistory(
            username   = username, action = 'password_reset',
            decided_by = admin, created_at = now, decided_at = now,
        ))
        db.session.commit()
        _log_history('IAM', f'비밀번호 초기화: {username}', '다음 로그인 시 변경 필요', admin)
        flash(f'{username} 비밀번호 초기화 완료. 다음 로그인 시 변경을 요청합니다.', 'success')

    tab = 'pending' if action in ('approve', 'reject') else 'users'
    return redirect(url_for('iam') + f'#{tab}')


@app.route('/admin/projects', methods=['GET', 'POST'])
@require_role('admin')
@login_required
def manage_projects():
    db_me = DBUser.query.get(current_user.get_id())
    if not db_me or not db_me.is_admin:
        flash('관리자만 접근할 수 있습니다.', 'danger')
        return redirect(url_for('summary'))

    error = success = None

    if request.method == 'POST':
        action     = request.form.get('action')
        zone       = request.form.get('zone', '').strip()
        access_id  = request.form.get('access_id', '').strip()
        secret_key = request.form.get('secret_key', '').strip()

        if action == 'add':
            if not zone or not access_id or not secret_key:
                error = '모든 필드를 입력해주세요.'
            elif Project.query.get(zone):
                error = f'이미 존재하는 프로젝트입니다: {zone}'
            else:
                token = _fetch_kc_token(access_id, secret_key)
                if not token:
                    error = f'토큰 발급 실패 — Access ID/Secret Key를 확인해주세요.'
                else:
                    db.session.add(Project(
                        zone               = zone,
                        access_id          = access_id,
                        secret_key         = secret_key,
                        token              = token,
                        token_refreshed_at = datetime.utcnow(),
                    ))
                    db.session.commit()
                    _write_tokens_file()
                    # Apply S-Codes to all VMs in background
                    import threading
                    threading.Thread(
                        target=_apply_scodes_for_project,
                        args=(zone, token),
                        daemon=True
                    ).start()
                    _log_history('IAM', f'프로젝트 추가: {zone}',
                                 '인프라 코드 자동 적용 시작', current_user.get_id())
                    success = f'프로젝트 {zone} 추가 완료. 인프라 코드 자동 적용 중...'

        elif action == 'delete':
            proj = Project.query.get(zone)
            if proj:
                db.session.delete(proj)
                # Record in blocklist so cron scripts can't re-add it
                if not DeletedProject.query.get(zone):
                    db.session.add(DeletedProject(
                        zone       = zone,
                        deleted_at = datetime.utcnow(),
                        deleted_by = current_user.get_id(),
                    ))
                db.session.commit()
                _write_tokens_file()
                success = f'프로젝트 {zone} 삭제 완료.'
            else:
                error = f'프로젝트를 찾을 수 없습니다: {zone}'

        elif action == 'refresh':
            proj = Project.query.get(zone)
            if proj and proj.access_id and proj.secret_key:
                token = _fetch_kc_token(proj.access_id, proj.secret_key)
                if token:
                    proj.token              = token
                    proj.token_refreshed_at = datetime.utcnow()
                    db.session.commit()
                    _write_tokens_file()
                    success = f'{zone} 토큰 갱신 완료.'
                else:
                    error = f'{zone} 토큰 갱신 실패.'
            else:
                error = f'{zone} — 저장된 자격증명 없음 (토큰 파일에서 가져온 프로젝트).'

        elif action == 'edit':
            proj = Project.query.get(zone)
            if not proj:
                error = f'프로젝트를 찾을 수 없습니다: {zone}'
            else:
                new_zone       = request.form.get('new_zone', '').strip()
                new_alias      = request.form.get('alias', '').strip()
                new_access_id  = request.form.get('access_id', '').strip()
                new_secret_key = request.form.get('secret_key', '').strip()

                # Update alias and credentials in place
                proj.alias = new_alias or None
                if new_access_id:  proj.access_id  = new_access_id
                if new_secret_key: proj.secret_key = new_secret_key

                # Rename zone (PK) if changed
                if new_zone and new_zone != zone:
                    if Project.query.get(new_zone):
                        error = f'이미 존재하는 프로젝트명입니다: {new_zone}'
                    else:
                        new_proj = Project(
                            zone               = new_zone,
                            access_id          = proj.access_id,
                            secret_key         = proj.secret_key,
                            token              = proj.token,
                            token_refreshed_at = proj.token_refreshed_at,
                            alias              = proj.alias,
                        )
                        db.session.add(new_proj)
                        if not DeletedProject.query.get(zone):
                            db.session.add(DeletedProject(
                                zone=zone, deleted_at=datetime.utcnow(),
                                deleted_by=current_user.get_id()
                            ))
                        db.session.delete(proj)
                        db.session.commit()
                        _write_tokens_file()
                        _log_history('IAM', f'프로젝트 이름 변경: {zone} → {new_zone}',
                                     None, current_user.get_id())
                        success = f'프로젝트 이름이 {new_zone}으로 변경되었습니다.'

                if not error and not success:
                    db.session.commit()
                    _write_tokens_file()
                    _log_history('IAM', f'프로젝트 수정: {zone}', None, current_user.get_id())
                    success = f'{new_zone or zone} 프로젝트 수정 완료.'

    projects = Project.query.order_by(Project.zone).all()
    if request.method == 'POST':
        return redirect(url_for('iam') + '#projects')
    return render_template('manage_projects.html', projects=projects,
                           error=error, success=success)



# ── Session inactivity timeout ────────────────────────────────
@app.before_request
def check_session_timeout():
    app.logger.info(f'before_request: path={request.path} authenticated={current_user.is_authenticated} remember_cookie={request.cookies.get("remember_token", "NONE")[:20] if request.cookies.get("remember_token") else "NONE"} session_keys={list(session.keys())}')
    if current_user.is_authenticated:
        last = session.get('_last_activity')
        now  = datetime.utcnow().timestamp()
        if last and (now - last) > 3600:
            logout_user()
            session.clear()
            flash('세션이 만료되었습니다. 다시 로그인해주세요.', 'warning')
            return redirect(url_for('login'))
        session['_last_activity'] = now
        session.modified = True


@app.route('/logout', methods=['POST'])
@login_required
@csrf.exempt
def logout():
    username = current_user.get_id() if current_user.is_authenticated else 'unknown'
    _log_history('IAM', f'로그아웃: {username}', None, username)
    logout_user()
    session.clear()
    response = redirect(url_for('login'))
    response.delete_cookie('remember_token')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    flash('로그아웃 되었습니다.', 'info')
    return response


@app.route('/search_vms', methods=['GET'])
@login_required
def search_vms():
    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    # Remove time part by splitting at 'T' and take only date part
    df['date'] = df['date'].astype(str).str.split('T').str[0]

    # Convert date column to datetime for filtering
    df['date'] = pd.to_datetime(df['date'], errors='coerce')

    # Filter columns to lowercase + strip
    filter_columns = [
        'status', 'project', 'servicecode', 'vm_name', 'flavor', 'availability_zone',
        'vpc', 'subnet', 'os', 'ip_address'
    ]

    # Re-resolve S-Code from DB rules BEFORE filtering so filter uses correct values
    rules = _get_all_scode_rules()
    if rules:
        df['servicecode'] = df['vm_name'].apply(lambda name: _resolve_scode(name, rules) or 'invalid')

    for col in filter_columns:
        df[col] = df[col].astype(str).str.strip().str.lower()

    # Get date filters from request args
    date_start = request.args.get('date_start')
    date_end = request.args.get('date_end')

    if date_start:
        try:
            start_dt = pd.to_datetime(date_start)
            df = df[df['date'] >= start_dt]
        except Exception:
            pass

    if date_end:
        try:
            end_dt = pd.to_datetime(date_end)
            df = df[df['date'] <= end_dt]
        except Exception:
            pass

    # Parse other filters from query
    def get_filters(param_name):
        raw_val = request.args.get(param_name, '')
        return [v.strip().lower() for v in raw_val.split(',') if v.strip()]

    filters = {col: get_filters(col) for col in filter_columns}

    # Apply filters to the dataframe
    for col, vals in filters.items():
        if vals:
            if col in ['vm_name', 'ip_address'] and len(vals) == 1:
                substring = vals[0]
                # Case-insensitive substring match
                df = df[df[col].str.contains(substring, na=False, case=False)]
            else:
                df = df[df[col].isin(vals)]

    # Format date column to 'YYYY-MM-DD' string in output JSON
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')

    # Replace NaN with empty string to produce valid JSON
    df = df.fillna('')

    # Convert to dict and jsonify
    results = df.to_dict(orient='records')
    return jsonify(results)

@app.route('/export_vms_csv', methods=['GET'])
@login_required
def export_vms_csv():
    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    df['date'] = df['date'].astype(str).str.split('T').str[0]
    df['date'] = pd.to_datetime(df['date'], errors='coerce')

    # Make filter columns lowercase and strip whitespace
    filter_columns = [
        'status', 'project', 'servicecode', 'vm_name', 'flavor', 'availability_zone',
        'vpc', 'subnet', 'os'
    ]
    for col in filter_columns:
        df[col] = df[col].astype(str).str.strip().str.lower()

    # Date filtering
    date_start = request.args.get('date_start')
    date_end = request.args.get('date_end')
    if date_start:
        try:
            start_dt = pd.to_datetime(date_start)
            df = df[df['date'] >= start_dt]
        except Exception:
            pass
    if date_end:
        try:
            end_dt = pd.to_datetime(date_end)
            df = df[df['date'] <= end_dt]
        except Exception:
            pass

    # Other query filters
    def get_filters(param_name):
        raw_val = request.args.get(param_name, '')
        return [v.strip().lower() for v in raw_val.split(',') if v.strip()]

    filters = {col: get_filters(col) for col in filter_columns}

    for col, vals in filters.items():
        if vals:
            if col == ['vm_name', 'ip_address'] and len(vals) == 1:
                substring = vals[0]
                df = df[df[col].str.contains(substring, na=False)]
            else:
                df = df[df[col].isin(vals)]

    # Convert date column to string format 'YYYY-MM-DD' for CSV output
    df['date'] = df['date'].dt.strftime('%Y-%m-%d')

    # Compose filename based on project filter query parameter
    project_param = request.args.get('project', '')
    if project_param:
        project_name = project_param.split(',')[0].strip()  # Use first project if multiple
        project_name = project_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
        filename = f"{project_name}_vm.csv"
    else:
        filename = 'vm_export.csv'

    # Prepare CSV content in memory
    csv_data = df.to_csv(index=False)

    # Create response with CSV mime type and dynamic attachment filename
    response = Response(
        csv_data,
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-cache'
        }
    )
    return response


@app.route('/filter_subnets', methods=['GET'])
# @login_required
def filter_subnets():
    vpcs = request.args.get('vpc', '')
    vpc_list = [v.strip().lower() for v in vpcs.split(',') if v.strip()]
    print("Received VPC filter:", vpc_list)

    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    df['vpc'] = df['vpc'].astype(str).str.strip().str.lower()
    df['subnet'] = df['subnet'].astype(str).str.strip()
    filtered = df[df['vpc'].isin(vpc_list)][['vpc', 'subnet']].drop_duplicates()

    print("Filtered subnets:")
    print(filtered)

    unique_subnets = sorted(filtered['subnet'].dropna().unique())
    print("Unique subnets returned:", unique_subnets)
    return jsonify(unique_subnets)

@app.route('/filter_servicecode', methods=['GET'])
# @login_required
def filter_servicecode():
    projects = request.args.get('project', '')
    project_list = [p.strip().lower() for p in projects.split(',') if p.strip()]
    print("Received Project filter:", project_list)

    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    df['project'] = df['project'].astype(str).str.strip().str.lower()
    df['servicecode'] = df['servicecode'].astype(str).str.strip()
    filtered = df[df['project'].isin(project_list)][['project', 'servicecode']].drop_duplicates()

    print("Filtered servicecodes:")
    print(filtered)

    unique_servicecodes = sorted(filtered['servicecode'].dropna().unique())
    print("Unique servicecodes returned:", unique_servicecodes)
    return jsonify(unique_servicecodes)


@app.route('/filter_options/<column>', methods=['GET'])
# @login_required
def filter_options(column):
    valid_columns = [
        'status', 'project', 'servicecode', 'ip_address', 'vm_name', 'flavor', 'availability_zone',
        'vpc', 'subnet', 'os'
    ]
    if column not in valid_columns:
        return jsonify([])

    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    unique_vals = df[column].dropna().unique()
    unique_vals_sorted = sorted(set([str(v) for v in unique_vals]))
    return jsonify(unique_vals_sorted)

@app.route('/api/vms/count', methods=['POST'])
@login_required
def vm_count():
    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    filter_columns = [
        'status', 'project', 'servicecode', 'ip_address', 'vm_name', 'flavor', 'availability_zone',
        'vpc', 'subnet', 'os'
    ]

    for col in filter_columns:
        df[col] = df[col].astype(str).str.strip().str.lower()

    data = request.get_json()

    for col in filter_columns:
        vals = data.get(col, [])
        if vals:
            if col == 'vm_name' and len(vals) == 1:
                substring = vals[0]
                df = df[df[col].str.contains(substring, na=False)]
            else:
                df = df[df[col].isin([v.lower() for v in vals])]

    # Optional: handle per_page parameter if you want to count results after pagination or total count
    # For pure count, ignore pagination

    count = len(df)
    return jsonify({'count': count})


@app.route('/vm_search')
@login_required
def vm_search():
    return render_template('cmdb.html')


@app.route('/api/debug_projects')
@login_required
@require_role('admin')
def debug_projects():
    projects = Project.query.order_by(Project.zone).all()
    result = []
    for p in projects:
        result.append({
            'zone': p.zone,
            'has_access_id': bool(p.access_id),
            'has_secret_key': bool(p.secret_key),
            'has_token': bool(p.token),
            'token_age_hours': round((datetime.utcnow() - p.token_refreshed_at).total_seconds() / 3600, 1) if p.token_refreshed_at else None,
        })
    # Also check .tokens file
    tokens_file_keys = get_projects_from_dotenv()
    return jsonify({
        'db_projects': result,
        'tokens_file_count': len(tokens_file_keys),
        'tokens_file_keys': tokens_file_keys,
        'missing_from_file': [p['zone'] for p in result if p['zone'] not in tokens_file_keys]
    })



@app.route('/api/scode_treemap')
@login_required
def scode_treemap():
    """Return S-Code counts with yesterday comparison for treemap."""
    try:
        # Get current counts
        csv_path = '/home/ubuntu/merged_vm_result.csv'
        df = pd.read_csv(csv_path, sep='\t', header=None, names=[
            'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id',
            'ip_address', 'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
        ])
        rules = _get_all_scode_rules()
        if rules:
            df['servicecode'] = df['vm_name'].apply(
                lambda name: _resolve_scode(name, rules) or 'invalid'
            )
        current = df.groupby('servicecode').size().to_dict()

        # Get the most recent available snapshot (not strictly yesterday —
        # avoids false diffs when yesterday's snapshot is missing)
        kst_today = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d')
        kst_yesterday = ((datetime.utcnow() + timedelta(hours=9)) - timedelta(days=1)).strftime('%Y-%m-%d')

        yesterday_rows = SCodeSnapshot.query.filter_by(date=kst_yesterday).all()
        baseline_date  = kst_yesterday
        if not yesterday_rows:
            # Fall back to most recent available snapshot before today
            latest = db.session.query(SCodeSnapshot.date) \
                .filter(SCodeSnapshot.date < kst_today) \
                .order_by(SCodeSnapshot.date.desc()).first()
            if latest:
                baseline_date  = latest[0]
                yesterday_rows = SCodeSnapshot.query.filter_by(date=baseline_date).all()
            else:
                # No historical snapshot at all — use today's if available,
                # otherwise show no diff (treat everything as no change)
                baseline_date  = kst_today
                yesterday_rows = SCodeSnapshot.query.filter_by(date=kst_today).all()
        yesterday = {r.scode: r.count for r in yesterday_rows}

        # VM-level diff — use same S-Code rules as the live query above so the
        # comparison is apples-to-apples even if rules changed since yesterday's
        # snapshot was taken.
        yesterday_vm_rows = SCodeVmSnapshot.query.filter_by(date=baseline_date).all()
        yesterday_vm_ids_by_scode = {}
        for v in yesterday_vm_rows:
            # Strip whitespace from vm_id to avoid false diff from CSV padding
            vid = (v.vm_id or '').strip()
            if vid:
                yesterday_vm_ids_by_scode.setdefault(v.scode, set()).add(vid)

        # Strip whitespace from live vm_ids for the same reason
        df['vm_id'] = df['vm_id'].astype(str).str.strip()
        live_vm_ids_by_scode = df.groupby('servicecode')['vm_id'].apply(set).to_dict()

        result = []
        for scode, count in current.items():
            prev = yesterday.get(scode, count)  # if new scode, treat as no change
            diff = count - prev
            live_ids = live_vm_ids_by_scode.get(scode, set())
            prev_ids = yesterday_vm_ids_by_scode.get(scode, set())
            added_count   = len(live_ids - prev_ids)
            removed_count = len(prev_ids - live_ids)
            result.append({
                'scode': scode,
                'count': count,
                'prev': prev,
                'diff': diff,
                'added_count':   added_count,
                'removed_count': removed_count,
                'group': scode.split('-')[0] if '-' in scode else scode
            })

        result.sort(key=lambda x: x['count'], reverse=True)
        return jsonify({
            'data': result,
            'snapshot_date': baseline_date,
            'today': kst_today
        })
    except Exception as e:
        return jsonify({'data': [], 'error': str(e)})


@app.route('/api/scode_snapshot/trigger', methods=['POST'])
@csrf.exempt
@login_required
@require_role('admin')
def trigger_scode_snapshot():
    """Manually trigger a snapshot."""
    with app.app_context():
        _take_scode_snapshot()
    return jsonify({'status': 'ok'})


@app.route('/api/scode_vm_diff')
@login_required
def scode_vm_diff():
    """Return VMs added/removed by comparing live CSV against yesterday's snapshot."""
    try:
        scode = request.args.get('scode', '').strip()
        if not scode:
            return jsonify({'error': 'scode required'}), 400

        kst_today     = (datetime.utcnow() + timedelta(hours=9)).strftime('%Y-%m-%d')
        kst_yesterday = ((datetime.utcnow() + timedelta(hours=9)) - timedelta(days=1)).strftime('%Y-%m-%d')

        # Get LIVE VMs from CSV for this S-Code
        csv_path = '/home/ubuntu/merged_vm_result.csv'
        df = pd.read_csv(csv_path, sep='\t', header=None, names=[
            'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id',
            'ip_address', 'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
        ])
        rules = _get_all_scode_rules()
        if rules:
            df['servicecode'] = df['vm_name'].apply(
                lambda name: _resolve_scode(name, rules) or 'invalid'
            )
        df = df.fillna('')
        live_df   = df[df['servicecode'] == scode]
        live_vms  = {row['vm_id']: row for _, row in live_df.iterrows()}

        # Get yesterday's snapshot VMs
        yesterday_vms = SCodeVmSnapshot.query.filter_by(date=kst_yesterday, scode=scode).all()
        if not yesterday_vms:
            latest = db.session.query(SCodeVmSnapshot.date)\
                .filter_by(scode=scode)\
                .filter(SCodeVmSnapshot.date < kst_today)\
                .order_by(SCodeVmSnapshot.date.desc()).first()
            if latest:
                kst_yesterday = latest[0]
                yesterday_vms = SCodeVmSnapshot.query.filter_by(date=kst_yesterday, scode=scode).all()

        yesterday_map = {v.vm_id: v for v in yesterday_vms}

        # Added = in live CSV but not in yesterday's snapshot
        added_ids   = set(live_vms.keys()) - set(yesterday_map.keys())
        # Removed = in yesterday's snapshot but not in live CSV
        removed_ids = set(yesterday_map.keys()) - set(live_vms.keys())

        def live_vm_dict(row):
            return {
                'vm_id':      row['vm_id'],
                'vm_name':    row['vm_name'],
                'project':    row['project'],
                'ip_address': row['ip_address'],
            }

        def snap_vm_dict(v):
            return {
                'vm_id':      v.vm_id,
                'vm_name':    v.vm_name,
                'project':    v.project,
                'ip_address': v.ip_address,
            }

        return jsonify({
            'scode':         scode,
            'snapshot_date': kst_yesterday,
            'today':         kst_today,
            'added':         [live_vm_dict(live_vms[i])     for i in added_ids],
            'removed':       [snap_vm_dict(yesterday_map[i]) for i in removed_ids],
            'added_count':   len(added_ids),
            'removed_count': len(removed_ids),
            'has_snapshot':  bool(yesterday_vms)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/scode_counts')
@login_required
def scode_counts():
    """Return VM counts per S-Code, re-resolved from DB rules."""
    try:
        csv_path = '/home/ubuntu/merged_vm_result.csv'
        df = pd.read_csv(csv_path, sep='\t', header=None, names=[
            'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id',
            'ip_address', 'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
        ])
        # Re-resolve S-Code from DB rules
        rules = _get_all_scode_rules()
        if rules:
            df['servicecode'] = df['vm_name'].apply(
                lambda name: _resolve_scode(name, rules) or 'invalid'
            )
        counts = df.groupby('servicecode').size().reset_index(name='count')
        counts = counts.sort_values('count', ascending=False)
        return jsonify({'counts': counts.rename(columns={'servicecode': 'scode'}).to_dict(orient='records')})
    except Exception as e:
        return jsonify({'counts': [], 'error': str(e)})



@app.route('/api/scode_rules/list')
@login_required
@require_role('admin')
def get_scode_rules():
    rules = SCodeRule.query.filter_by(zone='global').order_by(
        SCodeRule.is_default, SCodeRule.priority).all()
    return jsonify([{
        'id': r.id, 'priority': r.priority,
        'match_type': r.match_type, 'match_value': r.match_value,
        'scode': r.scode, 'is_default': r.is_default
    } for r in rules])


@app.route('/api/scode_info', methods=['GET', 'POST'])
@csrf.exempt
@login_required
@require_role('admin')
def scode_info():
    if request.method == 'GET':
        rows = SCodeInfo.query.all()
        return jsonify({r.scode: {
            'engineer': r.engineer or '',
            'developer': r.developer or '',
            'manager': r.manager or '',
        } for r in rows})
    # POST — upsert
    data = request.get_json() or {}
    scode = data.get('scode', '').strip()
    if not scode:
        return jsonify({'error': 'scode required'}), 400
    row = SCodeInfo.query.get(scode)
    if not row:
        row = SCodeInfo(scode=scode)
        db.session.add(row)
    row.engineer   = data.get('engineer', '').strip() or None
    row.developer  = data.get('developer', '').strip() or None
    row.manager    = data.get('manager', '').strip() or None
    row.updated_at = datetime.utcnow()
    row.updated_by = current_user.get_id()
    db.session.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/scode_rules', methods=['GET', 'POST'])
@csrf.exempt
@login_required
@require_role('admin')
def save_scode_rules():
    if request.method == 'GET':
        rules = SCodeRule.query.filter_by(zone='global').order_by(
            SCodeRule.is_default, SCodeRule.priority).all()
        return jsonify([{
            'id': r.id, 'priority': r.priority,
            'match_type': r.match_type, 'match_value': r.match_value,
            'scode': r.scode, 'is_default': r.is_default
        } for r in rules])

    data  = request.get_json()
    rules = data.get('rules', [])
    SCodeRule.query.filter_by(zone='global').delete()
    for i, r in enumerate(rules):
        db.session.add(SCodeRule(
            zone        = 'global',
            priority    = i,
            match_type  = r.get('match_type', 'contains'),
            match_value = r.get('match_value', '').strip(),
            scode       = r.get('scode', '').strip(),
            is_default  = r.get('is_default', False),
            created_by  = current_user.get_id(),
            created_at  = datetime.utcnow(),
        ))
    db.session.commit()
    _log_history('IAM', '글로벌 인프라 코드 규칙 저장',
                 f'{len(rules)}개 규칙', current_user.get_id())
    return jsonify({'status': 'ok', 'saved': len(rules)})






@app.route('/api/fix_tokens')
@login_required
@require_role('admin')
def fix_tokens_file():
    """Force rewrite .tokens file from DB — fixes missing projects."""
    _write_tokens_file()
    keys     = get_projects_from_dotenv()
    projects = [p.zone for p in Project.query.order_by(Project.zone).all()]
    missing  = [p for p in projects if p not in keys]
    return jsonify({'status': 'done', 'written': len(projects), 'missing_after': missing})



@app.route('/api/data_mtime')
def data_mtime():
    try:
        mtime = os.path.getmtime('/home/ubuntu/merged_vm_result.csv')
        return jsonify({'mtime': mtime})
    except Exception:
        return jsonify({'mtime': None})



@app.route('/api/refresh_data', methods=['POST'])
@csrf.exempt
@login_required
@require_role('admin', 'operator')
def refresh_data():
    """Signal automate_infra_vm_status service to run on next cycle."""
    try:
        # Write flag file — service picks it up and runs immediately
        with open('/tmp/infra_refresh_now.flag', 'w') as f:
            f.write(current_user.get_id())
        _log_history('VM', '데이터 갱신 요청', '다음 서비스 사이클에서 즉시 갱신됩니다',
                     current_user.get_id())
        return jsonify({'status': 'started', 'message': '데이터 갱신을 요청했습니다. 곧 갱신됩니다.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/home')
@login_required
def home():
    """New landing page — shows recent activity, notifications, and CloudTrail log."""
    from models import UserActivity, Notification, AppHistory
    username = current_user.get_id()

    # URL → category fallback — matches Services panel section titles exactly
    URL_CATEGORY = {
        '/vm_create':          'Compute — Instances',
        '/vm_search':          'Compute — Instances',
        '/get_server_info':    'Compute — Volumes',
        '/create_volumes':     'Compute — Volumes',
        '/attach_volumes':     'Compute — Volumes',
        '/check_connectivity': 'Network — VPC',
        '/summary':            'Management Tools — Overview',
        '/provisioning':       'Management Tools — Provisioning',
        '/history':            'IAM',
        '/iam':                'IAM',
    }

    # Recent activity — last 6 unique pages visited
    recent = UserActivity.query \
        .filter_by(username=username) \
        .order_by(UserActivity.visited_at.desc()) \
        .limit(6).all()

    # Apply URL-based category fallback for records missing page_category
    for item in recent:
        if not item.page_category:
            for url, cat in URL_CATEGORY.items():
                if item.page_url.startswith(url):
                    item.page_category = cat
                    break

    # Sort by category alphabetically, then by page_label alphabetically within each category
    recent = sorted(recent, key=lambda x: (x.page_category or '기타', x.page_label or ''))

    # Unread PW expiry notifications
    notifications = Notification.query \
        .filter_by(resolved=False) \
        .order_by(Notification.checked_at.desc()) \
        .limit(10).all()

    # Recent CloudTrail log entries (last 10)
    history = AppHistory.query \
        .order_by(AppHistory.created_at.desc()) \
        .limit(10).all()

    # KC notices — fetched server-side to avoid async JS complexity
    kc_notices = []
    try:
        import subprocess as _sp
        result = _sp.run([
            '/usr/bin/curl', '-s', '--max-time', '8',
            '-H', 'User-Agent: Mozilla/5.0',
            '-H', 'Accept-Language: ko-KR,ko;q=0.9',
            '--compressed', 'https://your-cloud.example.com/more/notices'
        ], capture_output=True, text=True, timeout=10)
        kc_notices = _parse_kc_notices(result.stdout)[:10]
    except Exception:
        pass

    # Last 5 changelog entries
    from models import AppChangelog
    changelog = AppChangelog.query \
        .order_by(AppChangelog.created_at.desc()) \
        .limit(5).all()

    # Favorites
    from models import UserFavorite
    favorites = UserFavorite.query \
        .filter_by(username=username) \
        .order_by(UserFavorite.page_category, UserFavorite.page_label) \
        .all()

    return render_template('home.html',
        recent=recent,
        notifications=notifications,
        history=history,
        kc_notices=kc_notices,
        changelog=changelog,
        favorites=favorites,
        timedelta=timedelta,
    )


@app.route('/api/activity/track', methods=['POST'])
@login_required
def track_activity():
    """Record a page visit for the current user.
    Keeps only the last 6 distinct pages per user (upsert by page_key)."""
    from models import UserActivity
    data      = request.get_json(silent=True) or {}
    page_key      = (data.get('page_key') or '').strip()
    page_label    = (data.get('page_label') or '').strip()
    page_url      = (data.get('page_url') or '').strip()
    page_category = (data.get('page_category') or '').strip()
    if not page_key or not page_url:
        return jsonify({'ok': False}), 400

    username = current_user.get_id()
    # Upsert: update visited_at if already exists, else insert
    existing = UserActivity.query.filter_by(username=username, page_key=page_key).first()
    if existing:
        existing.visited_at    = datetime.utcnow()
        existing.page_label    = page_label
        existing.page_url      = page_url
        existing.page_category = page_category or existing.page_category
    else:
        db.session.add(UserActivity(
            username=username, page_key=page_key,
            page_label=page_label, page_url=page_url,
            page_category=page_category,
        ))
    db.session.commit()
    # Prune to keep only 6 most recent per user
    all_entries = UserActivity.query \
        .filter_by(username=username) \
        .order_by(UserActivity.visited_at.desc()).all()
    for old in all_entries[6:]:
        db.session.delete(old)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/favorites', methods=['GET'])
@login_required
def get_favorites():
    from models import UserFavorite
    username = current_user.get_id()
    favs = UserFavorite.query.filter_by(username=username).order_by(UserFavorite.created_at).all()
    return jsonify([{
        'page_key': f.page_key, 'page_label': f.page_label,
        'page_url': f.page_url, 'page_category': f.page_category,
        'page_desc': f.page_desc or ''
    } for f in favs])


@app.route('/api/favorites', methods=['POST'])
@login_required
def add_favorite():
    from models import UserFavorite
    data         = request.get_json(silent=True) or {}
    page_key     = (data.get('page_key') or '').strip()
    page_label   = (data.get('page_label') or '').strip()
    page_url     = (data.get('page_url') or '').strip()
    page_category = (data.get('page_category') or '').strip()
    page_desc     = (data.get('page_desc') or '').strip()
    if not page_key or not page_url:
        return jsonify({'ok': False}), 400
    username = current_user.get_id()
    if not UserFavorite.query.filter_by(username=username, page_key=page_key).first():
        db.session.add(UserFavorite(
            username=username, page_key=page_key,
            page_label=page_label, page_url=page_url,
            page_category=page_category, page_desc=page_desc,
        ))
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/favorites/<page_key>', methods=['DELETE'])
@login_required
def remove_favorite(page_key):
    from models import UserFavorite
    username = current_user.get_id()
    fav = UserFavorite.query.filter_by(username=username, page_key=page_key).first()
    if fav:
        db.session.delete(fav)
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/system_status')
@login_required
def system_status():
    """Ping KakaoCloud API endpoints and return health status."""
    import requests as _req
    import time as _time
    ENDPOINTS = [
        {'name': 'BCS (Compute)',  'host': 'compute.your-cloud.example.com',     'url': 'https://compute.your-cloud.example.com/api/v1'},
        {'name': 'VPC (Network)',  'host': 'vpc.your-cloud.example.com',     'url': 'https://vpc.your-cloud.example.com/api/v1'},
        {'name': 'Network',        'host': 'network.your-cloud.example.com', 'url': 'https://network.your-cloud.example.com/api/v1'},
        {'name': 'Volume',         'host': 'volume.your-cloud.example.com',  'url': 'https://volume.your-cloud.example.com/api/v1'},
        {'name': 'Image',          'host': 'image.your-cloud.example.com',   'url': 'https://image.your-cloud.example.com/api/v1'},
    ]
    results = []
    for ep in ENDPOINTS:
        t0 = _time.time()
        try:
            r = _req.get(ep['url'], timeout=5, allow_redirects=True)
            ms = round((_time.time() - t0) * 1000)
            # 401/403 means endpoint is reachable (auth required), treat as UP
            status = 'up' if r.status_code < 500 else 'down'
        except _req.exceptions.Timeout:
            ms     = 5000
            status = 'timeout'
        except Exception:
            ms     = round((_time.time() - t0) * 1000)
            status = 'down'
        results.append({'name': ep['name'], 'host': ep['host'], 'status': status, 'ms': ms})
    return jsonify(results)



@app.route('/summary')
@login_required
def summary():
    # VM data (unchanged)
    csv_path = '/home/ubuntu/merged_vm_result.csv'
    df = pd.read_csv(csv_path, sep='\t', header=None, names=[
        'status', 'project', 'tenant_id', 'servicecode', 'vm_name', 'vm_id', 'ip_address',
        'flavor', 'availability_zone', 'vpc', 'subnet', 'os', 'date', 'user_id'
    ])

    # Load Balancer data: tab-separated
    lb_path = '/home/scv/merged_lb_result.csv'
    lb_df = pd.read_csv(lb_path, sep='\t', header=None)

    # Existing health status aggregation
    lb_health_counts = lb_df[0].value_counts().to_dict()
    health_simple_sum = {
        'ACTIVE': lb_health_counts.get('ACTIVE', 0) + lb_health_counts.get('ONLINE', 0),
        'DEGRADED': lb_health_counts.get('DEGRADED', 0),
        'ERROR': lb_health_counts.get('ERROR', 0)
    }

    # New categorization:
    def categorize_lb(row):
        if 'kube_service' in row[4]:
            return 'k8s'
        elif row[14].lower() == 'alb':
            return 'alb'
        elif row[14].lower() == 'nlb':
            return 'nlb'
        else:
            return 'other'

    lb_df['type'] = lb_df.apply(categorize_lb, axis=1)
    lb_type_counts = lb_df['type'].value_counts().to_dict()

    summary_data = {
        'total_vms': len(df),
        'vm_status_counts': df['status'].value_counts().to_dict(),
        'availability_zone_counts': df['availability_zone'].value_counts().to_dict(),
        'project_counts': df['project'].value_counts().to_dict(),
        'top_flavors': df['flavor'].value_counts().head(10).to_dict(),
        'lb_health_simple_sum': health_simple_sum,
        'lb_type_counts': lb_type_counts
    }
    chart_data = {
        'project_labels': list(summary_data['project_counts'].keys()),
        'project_values': list(summary_data['project_counts'].values()),
        'status_labels': list(summary_data['vm_status_counts'].keys()),
        'status_values': list(summary_data['vm_status_counts'].values()),
        'az_labels': list(summary_data['availability_zone_counts'].keys()),
        'az_values': list(summary_data['availability_zone_counts'].values()),
        'flavor_labels': list(summary_data['top_flavors'].keys()),
        'flavor_values': list(summary_data['top_flavors'].values()),
        'lb_status_labels': ['ACTIVE', 'DEGRADED', 'ERROR'],
        'lb_status_values': [
            health_simple_sum['ACTIVE'],
            health_simple_sum['DEGRADED'],
            health_simple_sum['ERROR']
        ],
        'lb_type_labels': list(lb_type_counts.keys()),
        'lb_type_values': list(lb_type_counts.values())
    }
    try:
        csv_mtime = datetime.fromtimestamp(
            os.path.getmtime(csv_path)
        ).strftime('%Y/%m/%d %H:%M')
    except Exception:
        csv_mtime = '-'
    return render_template('index.html', summary=summary_data, chart_data=chart_data,
                           csv_mtime=csv_mtime)
def get_server_info():
    error = None
    project = None
    vm_names = []
    vm_ids = []

    if request.method == 'POST':
        token_key = request.form.get('token', '').strip()
        if not token_key:
            error = "Token 값은 필수입니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)
        project = f"{token_key}-id"

        num_items = request.form.get('num_items')
        if not num_items:
            error = "가져올 개수는 필수입니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)
        try:
            num_items = int(num_items)
        except ValueError:
            error = "가져올 개수는 숫자여야 합니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)

        TOKEN = get_env_var(token_key)
        if not TOKEN:
            error = f"프로젝트 '{token_key}'에 대한 토큰이 없습니다. /etc/mgt-api/.tokens 파일을 확인하세요."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)


        url = 'https://compute.your-cloud.example.com/api/v1/instances'
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Auth-Token': TOKEN
        }
        #headers = {'X-Auth-Token': TOKEN}

        limit = 200  # Maximum per request - adjust if allowed by API
        marker = None
        all_servers = []

        try:
            while True:
                params = {'limit': limit}
                if marker:
                    params['marker'] = marker

                response = requests.get(url, headers=headers, params=params, timeout=10)

                if response.status_code == 429:
                    # Handle rate limit with Retry-After header or exponential backoff
                    retry_after = response.headers.get("Retry-After")
                    wait_time = int(retry_after) if retry_after else 2
                    flash(f"API 요청이 너무 많습니다. {wait_time}초 후 다시 시도합니다...", 'warning')
                    time.sleep(wait_time)
                    continue  # retry the same request

                response.raise_for_status()
                res = response.json()

                if 'error' in res:
                    error = f"API 에러 {res['error']['code']}: {res['error']['message']}"
                    flash(error, 'danger')
                    break

                servers = res.get('instances', [])
                if not servers:
                    break

                all_servers.extend(servers)

                # Get marker for next page - adjust key based on actual API response
                marker = res.get('next_marker') or res.get('next') or None
                if not marker:
                    break

                # Optional delay to prevent hitting rate limits
                time.sleep(0.5)

            # Limit the results to num_items
            vm_names = [server['name'] for server in all_servers[:num_items]]
            vm_ids = [server['id'] for server in all_servers[:num_items]]

        except requests.exceptions.RequestException as e:
            error = f"API 요청 실패: {str(e)}"
            flash(error, 'danger')

    return render_template(
        'browse_volumes.html',
        vm_names=vm_names,
        vm_ids=vm_ids,
        error=error,
        project=project,
        projects=get_projects_from_dotenv()
    )
def create_volumes():
    if request.method == 'POST':
        token_key = request.form.get('token', '').strip()
        if not token_key:
            error = "Token은 필수입니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())
        project_key = f"{token_key}-id"


        vm_list_input = request.form.get('vm_list', '')
        vm_list = [v.strip().strip('"').strip("'") for v in vm_list_input.replace('\n', ',').split(',') if v.strip()]


        volume_type_a = [v.strip() for v in request.form.get('volume_type_a', '').split(',') if v.strip()]
        disk_size_a = []
        try:
            disk_size_a = [int(d.strip()) for d in request.form.get('disk_size_a', '').split(',') if d.strip()]
        except ValueError:
            flash("Zone A의 디스크 크기는 숫자여야 합니다.", "danger")
            return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())


        volume_type_b = [v.strip() for v in request.form.get('volume_type_b', '').split(',') if v.strip()]
        disk_size_b = []
        try:
            disk_size_b = [int(d.strip()) for d in request.form.get('disk_size_b', '').split(',') if d.strip()]
        except ValueError:
            flash("Zone B의 디스크 크기는 숫자여야 합니다.", "danger")
            return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())


        if not vm_list:
            error = "VM 리스트는 필수입니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if not (volume_type_a and disk_size_a) and not (volume_type_b and disk_size_b):
            error = "Zone A와 Zone B의 볼륨 타입/디스크 크기 중 하나 이상 입력해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if (volume_type_a and disk_size_a) and (len(volume_type_a) != len(disk_size_a)):
            error = "Zone A의 볼륨 타입과 디스크 크기 개수가 일치해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if (volume_type_b and disk_size_b) and (len(volume_type_b) != len(disk_size_b)):
            error = "Zone B의 볼륨 타입과 디스크 크기 개수가 일치해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        task_args = {
            'project_key': project_key,
            'token_key': token_key,
            'vm_list': vm_list,
            'volume_type_a': volume_type_a,
            'disk_size_a': disk_size_a,
            'volume_type_b': volume_type_b,
            'disk_size_b': disk_size_b,
        }


        task = create_volumes_task.delay(task_args)
        return redirect(url_for('task_status', task_id=task.id))


    return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())
def attach_volumes():
    error = None


    if request.method == 'POST':
        token_key = request.form.get('token_manual', '').strip() or request.form.get('token', '').strip()


        if not token_key:
            error = "Token은 필수입니다."
            flash(error, 'danger')
            return render_template(
                'attach_volumes.html',
                error=error,
                username=current_user.get_id(),
                projects=get_projects_from_dotenv()
            )


        project_key = f"{token_key}-id"


        vm_list_input = request.form.get('vm_list', '')
        vm_list = [re.sub(r'["\']', '', v.strip()) for v in vm_list_input.replace('\n', ',').split(',') if v.strip()]


        vm_id_input = request.form.get('vm_id', '')


        def clean_vm_id(vm_id):
            return ''.join(c for c in vm_id if c.isalnum() or c == '-')


        vm_id_list = [clean_vm_id(v.strip().strip('"').strip("'")) for v in vm_id_input.replace('\n', ',').split(',') if v.strip()]


        vol_type_input = request.form.get('vol_type', '')
        vol_type = [re.sub(r'["\']', '', vt.strip()) for vt in vol_type_input.replace('\n', ',').split(',') if vt.strip()]


        if not vm_list or not vm_id_list or not vol_type:
            error = "모든 필수 필드를 입력해주세요."
            flash(error, 'danger')
            return render_template(
                'attach_volumes.html',
                error=error,
                username=current_user.get_id(),
                projects=get_projects_from_dotenv()
            )


        task_args = {
            'project_key': project_key,
            'token_key': token_key,
            'vm_list': vm_list,
            'vm_id_list': vm_id_list,
            'vol_type': vol_type
        }


        task = attach_volumes_task.delay(task_args)
        flash('볼륨 연결 작업이 시작되었습니다.', 'info')
        return redirect(url_for('task_status', task_id=task.id))


    return render_template(
        'attach_volumes.html',
        username=current_user.get_id(),
        projects=get_projects_from_dotenv()
    )
def task_status(task_id):
    return render_template('task_status.html', task_id=task_id)
def task_result(task_id):
    task = AsyncResult(task_id, app=celery_app)
    if task.state == 'FAILURE':
        return jsonify({'state': task.state, 'result': str(task.result)})
    return jsonify({'state': task.state, 'result': task.result})
def billing_dashboard():
    return render_template('billing_dashboard.html')
def api_list_s_codes():
    try:
        with open('billing_data.json', 'r') as f:
            data = json.load(f)
        s_codes = set()
        # Extract from your billing JSON structure
        for category in data['total_billing'].values():
            for service in category['services']:
                for billing in service.get('billings', []):
                    s_codes.add(billing['s_code'])
        return jsonify(sorted(list(s_codes)))
    except FileNotFoundError:
        return jsonify({"error": "billing_data.json not found. Run billing_calculator.py first."}), 500
    except Exception as e:
        return jsonify({"error": f"Error reading billing data: {str(e)}"}), 500
def api_billing_for_s_code(s_code):
    try:
        with open('latest_billing_data.json', 'r') as f:
            data = json.load(f)
        
        billing_result = {"s_code": s_code, "billing": {}}
        found = False
        
        # Search all services for this s_code
        for category in data['total_billing'].values():
            for service in category['services']:
                for billing in service.get('billings', []):
                    if billing['s_code'] == s_code:
                        billing_result["billing"][service['service']] = billing
                        found = True
        
        if not found:
            return jsonify({"error": f"No billing data found for s_code '{s_code}'"}), 404
            
        return jsonify(billing_result)
    except FileNotFoundError:
        return jsonify({"error": "billing_data.json not found. Run billing_calculator.py first."}), 500
    except Exception as e:
        return jsonify({"error": f"Error reading billing data: {str(e)}"}), 500



@app.route('/browse_volumes', methods=['GET', 'POST'])
@require_role('admin', 'operator')
@login_required
def get_server_info():
    error = None
    project = None
    vm_names = []
    vm_ids = []

    if request.method == 'POST':
        token_key = request.form.get('token', '').strip()
        if not token_key:
            error = "Token 값은 필수입니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)
        project = f"{token_key}-id"

        num_items = request.form.get('num_items')
        if not num_items:
            error = "가져올 개수는 필수입니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)
        try:
            num_items = int(num_items)
        except ValueError:
            error = "가져올 개수는 숫자여야 합니다."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)

        TOKEN = get_env_var(token_key)
        if not TOKEN:
            error = f"프로젝트 '{token_key}'에 대한 토큰이 없습니다. /etc/mgt-api/.tokens 파일을 확인하세요."
            flash(error, 'danger')
            return render_template('browse_volumes.html', error=error)


        url = 'https://compute.your-cloud.example.com/api/v1/instances'
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Auth-Token': TOKEN
        }
        #headers = {'X-Auth-Token': TOKEN}

        limit = 200  # Maximum per request - adjust if allowed by API
        marker = None
        all_servers = []

        try:
            while True:
                params = {'limit': limit}
                if marker:
                    params['marker'] = marker

                response = requests.get(url, headers=headers, params=params, timeout=10)

                if response.status_code == 429:
                    # Handle rate limit with Retry-After header or exponential backoff
                    retry_after = response.headers.get("Retry-After")
                    wait_time = int(retry_after) if retry_after else 2
                    flash(f"API 요청이 너무 많습니다. {wait_time}초 후 다시 시도합니다...", 'warning')
                    time.sleep(wait_time)
                    continue  # retry the same request

                response.raise_for_status()
                res = response.json()

                if 'error' in res:
                    error = f"API 에러 {res['error']['code']}: {res['error']['message']}"
                    flash(error, 'danger')
                    break

                servers = res.get('instances', [])
                if not servers:
                    break

                all_servers.extend(servers)

                # Get marker for next page - adjust key based on actual API response
                marker = res.get('next_marker') or res.get('next') or None
                if not marker:
                    break

                # Optional delay to prevent hitting rate limits
                time.sleep(0.5)

            # Limit the results to num_items
            vm_names = [server['name'] for server in all_servers[:num_items]]
            vm_ids = [server['id'] for server in all_servers[:num_items]]

        except requests.exceptions.RequestException as e:
            error = f"API 요청 실패: {str(e)}"
            flash(error, 'danger')

    return render_template(
        'browse_volumes.html',
        vm_names=vm_names,
        vm_ids=vm_ids,
        error=error,
        project=project,
        projects=get_projects_from_dotenv()
    )

@app.route('/create_volumes', methods=['GET', 'POST'])
@require_role('admin', 'operator')
@login_required
def create_volumes():
    if request.method == 'POST':
        token_key = request.form.get('token', '').strip()
        if not token_key:
            error = "Token은 필수입니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())
        project_key = f"{token_key}-id"


        vm_list_input = request.form.get('vm_list', '')
        vm_list = [v.strip().strip('"').strip("'") for v in vm_list_input.replace('\n', ',').split(',') if v.strip()]


        volume_type_a = [v.strip() for v in request.form.get('volume_type_a', '').split(',') if v.strip()]
        disk_size_a = []
        try:
            disk_size_a = [int(d.strip()) for d in request.form.get('disk_size_a', '').split(',') if d.strip()]
        except ValueError:
            flash("Zone A의 디스크 크기는 숫자여야 합니다.", "danger")
            return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())


        volume_type_b = [v.strip() for v in request.form.get('volume_type_b', '').split(',') if v.strip()]
        disk_size_b = []
        try:
            disk_size_b = [int(d.strip()) for d in request.form.get('disk_size_b', '').split(',') if d.strip()]
        except ValueError:
            flash("Zone B의 디스크 크기는 숫자여야 합니다.", "danger")
            return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())


        if not vm_list:
            error = "VM 리스트는 필수입니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if not (volume_type_a and disk_size_a) and not (volume_type_b and disk_size_b):
            error = "Zone A와 Zone B의 볼륨 타입/디스크 크기 중 하나 이상 입력해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if (volume_type_a and disk_size_a) and (len(volume_type_a) != len(disk_size_a)):
            error = "Zone A의 볼륨 타입과 디스크 크기 개수가 일치해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        if (volume_type_b and disk_size_b) and (len(volume_type_b) != len(disk_size_b)):
            error = "Zone B의 볼륨 타입과 디스크 크기 개수가 일치해야 합니다."
            flash(error, 'danger')
            return render_template('create_volumes.html', error=error, projects=get_projects_from_dotenv(), username=current_user.get_id())


        task_args = {
            'project_key': project_key,
            'token_key': token_key,
            'vm_list': vm_list,
            'volume_type_a': volume_type_a,
            'disk_size_a': disk_size_a,
            'volume_type_b': volume_type_b,
            'disk_size_b': disk_size_b,
        }


        task = create_volumes_task.delay(task_args)
        return redirect(url_for('task_status', task_id=task.id))


    return render_template('create_volumes.html', projects=get_projects_from_dotenv(), username=current_user.get_id())




@app.route('/attach_volumes', methods=['GET', 'POST'])
@require_role('admin', 'operator')
@login_required
def attach_volumes():
    error = None


    if request.method == 'POST':
        token_key = request.form.get('token_manual', '').strip() or request.form.get('token', '').strip()


        if not token_key:
            error = "Token은 필수입니다."
            flash(error, 'danger')
            return render_template(
                'attach_volumes.html',
                error=error,
                username=current_user.get_id(),
                projects=get_projects_from_dotenv()
            )


        project_key = f"{token_key}-id"


        vm_list_input = request.form.get('vm_list', '')
        vm_list = [re.sub(r'["\']', '', v.strip()) for v in vm_list_input.replace('\n', ',').split(',') if v.strip()]


        vm_id_input = request.form.get('vm_id', '')


        def clean_vm_id(vm_id):
            return ''.join(c for c in vm_id if c.isalnum() or c == '-')


        vm_id_list = [clean_vm_id(v.strip().strip('"').strip("'")) for v in vm_id_input.replace('\n', ',').split(',') if v.strip()]


        vol_type_input = request.form.get('vol_type', '')
        vol_type = [re.sub(r'["\']', '', vt.strip()) for vt in vol_type_input.replace('\n', ',').split(',') if vt.strip()]


        if not vm_list or not vm_id_list or not vol_type:
            error = "모든 필수 필드를 입력해주세요."
            flash(error, 'danger')
            return render_template(
                'attach_volumes.html',
                error=error,
                username=current_user.get_id(),
                projects=get_projects_from_dotenv()
            )


        task_args = {
            'project_key': project_key,
            'token_key': token_key,
            'vm_list': vm_list,
            'vm_id_list': vm_id_list,
            'vol_type': vol_type
        }


        task = attach_volumes_task.delay(task_args)
        flash('볼륨 연결 작업이 시작되었습니다.', 'info')
        return redirect(url_for('task_status', task_id=task.id))


    return render_template(
        'attach_volumes.html',
        username=current_user.get_id(),
        projects=get_projects_from_dotenv()
    )


@app.route('/task_status/<task_id>')
@login_required
def task_status(task_id):
    return render_template('task_status.html', task_id=task_id)



@app.route('/task_result/<task_id>')
@login_required
def task_result(task_id):
    task = AsyncResult(task_id, app=celery_app)
    if task.state == 'FAILURE':
        return jsonify({'state': task.state, 'result': str(task.result)})
    return jsonify({'state': task.state, 'result': task.result})




def _parse_kc_notices(html, only_new=False):
    """Parse KC notices from HTML. If only_new=True, only return New-badged notices."""
    import re
    if not html or len(html) < 100:
        return []

    notices = []
    seen    = set()
    pattern = re.compile(
        r'<a[^>]+href="(/more/notices/(\d+))"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE
    )

    for m in pattern.finditer(html):
        nid   = int(m.group(2))
        title = re.sub(r'<[^>]+>', '', m.group(3)).strip()
        title = title.replace('&#x27;', "'").replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

        if not title or len(title) < 5 or nid in seen:
            continue
        seen.add(nid)

        # New badge appears BEFORE the link — check 500 chars before
        pre    = html[max(0, m.start()-500):m.start()]
        is_new = 'kc-badge' in pre and 'New' in pre

        if only_new and not is_new:
            continue

        # Look ahead for date/type/status
        ctx    = html[m.start():min(len(html), m.end()+400)]
        date_m = re.search(r'(\d{4}\.\d{2}\.\d{2})', ctx)
        type_m = re.search(r'>(안내|점검|장애|보안)<', ctx)
        done_m = '완료' in ctx

        notices.append({
            'id':     nid,
            'title':  title,
            'url':    f'https://your-cloud.example.com/more/notices/{nid}',
            'date':   date_m.group(1) if date_m else '',
            'type':   type_m.group(1) if type_m else '안내',
            'status': '완료' if done_m else '',
            'is_new': is_new,
        })
        if len(notices) >= 20:
            break

    notices.sort(key=lambda x: x['id'], reverse=True)
    return notices


@app.route('/provisioning')
@login_required
@require_role('admin', 'operator')
def provisioning():
    """Main provisioning dashboard."""
    projects = [k for k in get_projects_from_dotenv() if k not in EXCLUDED_KEYS]
    return render_template('provisioning.html', projects=projects)


@app.route('/history')
@login_required
def history():
    return redirect(url_for('iam') + '#history')


@app.route('/api/history/log', methods=['POST'])
@csrf.exempt
@login_required
def log_history_entry():
    """Allow frontend to log audit events."""
    data       = request.get_json() or {}
    event_type = data.get('event_type', 'VM').strip().upper()
    title      = data.get('title', '').strip()
    detail     = data.get('detail', '').strip() or None
    if not title:
        return jsonify({'error': 'title required'}), 400
    _log_history(event_type, title, detail, current_user.get_id())
    return jsonify({'status': 'ok'})


@app.route('/api/history')
@login_required
def api_history():
    event_type = request.args.get('type', 'all')
    limit      = min(int(request.args.get('limit', 100)), 100)
    offset     = max(int(request.args.get('offset', 0)), 0)
    q = AppHistory.query
    if event_type != 'all':
        q = q.filter_by(event_type=event_type.upper())
    total  = q.count()
    events = q.order_by(AppHistory.created_at.desc()).offset(offset).limit(limit).all()
    return jsonify({
        'total':  total,
        'offset': offset,
        'limit':  limit,
        'events': [{
            'id':         e.id,
            'event_type': e.event_type,
            'title':      e.title,
            'detail':     e.detail,
            'username':   e.username,
            'created_at': (e.created_at + timedelta(hours=9)).strftime('%Y/%m/%d %H:%M') if e.created_at else '',
        } for e in events],
    })


# ── Changelog API ─────────────────────────────────────────────
@app.route('/api/changelog')
@login_required
def api_changelog():
    entries = AppChangelog.query.order_by(AppChangelog.created_at.desc()).all()
    result = {}
    version_latest = {}  # track latest created_at per version for sorting
    for e in entries:
        if e.version not in result:
            result[e.version] = {'version': e.version, 'date': (e.created_at + timedelta(hours=9)).strftime('%Y.%m.%d') if e.created_at else '', 'items': []}
            version_latest[e.version] = e.created_at
        else:
            if e.created_at and e.created_at > (version_latest[e.version] or e.created_at):
                version_latest[e.version] = e.created_at
        result[e.version]['items'].append({
            'id':          e.id,
            'title':       e.title,
            'change_type': e.change_type,
            'description': e.description,
            'image_url':   e.image_url or '',
            'icon':        e.icon or '',
        })
    # sort by latest entry date (primary), then version string (secondary), both descending
    sorted_versions = sorted(result.values(), key=lambda v: (version_latest.get(v['version']) or '', v['version'].lstrip('vV')), reverse=True)
    return jsonify(sorted_versions)


@app.route('/api/changelog/add', methods=['POST'])
@login_required
@require_role('admin')
def api_changelog_add():
    data        = request.get_json()
    version     = (data.get('version') or '').strip()
    title       = (data.get('title') or '').strip()
    change_type = (data.get('change_type') or '').strip().upper()
    description = (data.get('description') or '').strip()
    image_url   = (data.get('image_url') or '').strip()
    icon        = (data.get('icon') or '').strip()
    if not all([version, title, change_type, description]):
        return jsonify({'error': '모든 필드를 입력해주세요.'}), 400
    if change_type not in ('NEW', 'IMPROVED', 'FIXED'):
        return jsonify({'error': '유효하지 않은 변경 유형입니다.'}), 400
    entry = AppChangelog(
        version=version, title=title, change_type=change_type,
        description=description, image_url=image_url or None,
        icon=icon or None, created_by=current_user.get_id()
    )
    db.session.add(entry)
    db.session.commit()
    return jsonify({'ok': True, 'id': entry.id})


@app.route('/api/changelog/edit/<int:entry_id>', methods=['PUT'])
@login_required
@require_role('admin')
def api_changelog_edit(entry_id):
    entry = AppChangelog.query.get_or_404(entry_id)
    data        = request.get_json()
    version     = (data.get('version') or '').strip()
    title       = (data.get('title') or '').strip()
    change_type = (data.get('change_type') or '').strip().upper()
    description = (data.get('description') or '').strip()
    image_url   = (data.get('image_url') or '').strip()
    icon        = (data.get('icon') or '').strip()
    if not all([version, title, change_type, description]):
        return jsonify({'error': '모든 필드를 입력해주세요.'}), 400
    if change_type not in ('NEW', 'IMPROVED', 'FIXED'):
        return jsonify({'error': '유효하지 않은 변경 유형입니다.'}), 400
    entry.version     = version
    entry.title       = title
    entry.change_type = change_type
    entry.description = description
    entry.image_url   = image_url or None
    entry.icon         = icon or None
    db.session.commit()
    return jsonify({'ok': True, 'id': entry.id})


@app.route('/api/changelog/delete/<int:entry_id>', methods=['DELETE'])
@login_required
@require_role('admin')
def api_changelog_delete(entry_id):
    entry = AppChangelog.query.get_or_404(entry_id)
    db.session.delete(entry)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/changelog/images')
@login_required
def api_changelog_images():
    import os
    img_dir = os.path.join(app.root_path, 'static', 'update_log_images')
    os.makedirs(img_dir, exist_ok=True)
    exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
    files = []
    for f in sorted(os.listdir(img_dir)):
        if os.path.splitext(f)[1].lower() in exts:
            files.append({'name': f, 'url': f'/static/update_log_images/{f}'})
    return jsonify(files)


@app.route('/api/cmdb/refresh', methods=['POST'])
@login_required
@require_role('admin', 'operator')
def cmdb_refresh():
    """Trigger a fresh VM data pull into the CSV for CMDB."""
    try:
        script = '/etc/mgt-api/api/iet-management/Default-infra-status.py'
        if not os.path.exists(script):
            return jsonify({'error': 'CSV 생성 스크립트를 찾을 수 없습니다.'}), 404
        proc = subprocess.Popen(
            ['python3', script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        app.logger.info(f'CMDB refresh triggered by {current_user.get_id()} (pid={proc.pid})')
        return jsonify({'message': 'CSV 갱신이 시작되었습니다. 약 1-2분 후 새로고침하세요.', 'pid': proc.pid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cmdb/list_vms', methods=['GET'])
@login_required
def cmdb_list_vms():
    """Live VM list for CMDB."""
    all_projects = [k for k in get_projects_from_dotenv() if k not in EXCLUDED_KEYS]
    vms = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_list_all_vms_by_project, k): k for k in all_projects}
        for future in as_completed(futures):
            try:
                vms.extend(future.result())
            except Exception as e:
                app.logger.debug(f'CMDB VM list error: {e}')
    scode_rules = _get_all_scode_rules()
    result = []
    for vm in vms:
        image_raw  = vm.get('image')
        image_name = image_raw.get('name', '') if isinstance(image_raw, dict) else 'K8s node'
        addresses  = vm.get('addresses', [])
        private_ip = ''
        if isinstance(addresses, list):
            private_ip = next((a.get('private_ip', '') for a in addresses if a.get('private_ip')), '')
        zone  = vm.get('_project_key', '')
        scode = _resolve_scode(vm.get('name', ''), scode_rules) if scode_rules else 'invalid'
        result.append({
            'vm_name':     vm.get('name', ''),
            'vm_id':       vm.get('id', ''),
            'status':      vm.get('status', ''),
            'flavor':      vm.get('flavor', {}).get('name', '') if isinstance(vm.get('flavor'), dict) else '',
            'az':          vm.get('availability_zone', ''),
            'image':       image_name,
            'project_key': zone,
            'description': scode,
            'private_ip':  private_ip,
            'project_id':  vm.get('project_id', ''),
            'created_at':  vm.get('created_at', ''),
            'user_id':     vm.get('user_id', ''),
        })
    result.sort(key=lambda x: x['vm_name'])
    return jsonify({'vms': result, 'total': len(result)})


@app.route('/insufficient_role')
@login_required
def insufficient_role():
    return render_template('insufficient_role.html'), 403


@app.route('/api/notifications/run_expiry_check', methods=['POST'])
@login_required
@require_role('admin')
def run_expiry_check():
    """Manually trigger the password expiry check (admin only)."""
    threading.Thread(target=_run_pw_expiry_check, daemon=True).start()
    return jsonify({'message': '만료일 체크가 시작되었습니다. 잠시 후 알림을 확인하세요.'})


@app.route('/api/notifications')
@login_required
def get_notifications():
    """Return grouped notifications with read/unread state."""
    db_me    = DBUser.query.get(current_user.get_id())
    username = current_user.get_id()
    if not db_me or db_me.role not in ('admin', 'operator'):
        return jsonify({'notifications': [], 'count': 0, 'unread': 0})

    seen_keys = {s.notif_key for s in SeenNotification.query.filter_by(username=username).all()}
    items = []
    unread = 0

    # PW expiry — grouped
    pw_notifs = Notification.query.filter_by(resolved=False)\
                    .order_by(Notification.days_left.asc()).all()
    if pw_notifs:
        vm_list  = ', '.join(
            f"{n.vm_name} ({n.days_left}일)" if n.days_left >= 0 else f"{n.vm_name} (만료됨)"
            for n in pw_notifs
        )
        is_read = 'pw-group' in seen_keys
        if not is_read: unread += 1
        items.append({
            'id':         'pw-group',
            'type':       'pw_expiry_group',
            'title':      f'PW 만료 임박 — {len(pw_notifs)}개 VM',
            'detail':     vm_list,
            'count':      len(pw_notifs),
            'checked_at': (pw_notifs[0].checked_at + timedelta(hours=9)).strftime('%Y/%m/%d %H:%M') if pw_notifs[0].checked_at else '',
            'is_read':    is_read,
        })

    # Pending IAM (admin only)
    if db_me.role == 'admin':
        for u in DBUser.query.filter_by(status='pending').all():
            key     = f'iam-{u.username}'
            is_read = key in seen_keys
            if not is_read: unread += 1
            items.append({
                'id':         key,
                'type':       'iam_pending',
                'title':      f'가입 승인 대기: {u.username}',
                'detail':     f'요청 역할: {u.role} / {u.role_reason or "사유 없음"}',
                'checked_at': (u.created_at + timedelta(hours=9)).strftime('%Y/%m/%d %H:%M') if u.created_at else '',
                'is_read':    is_read,
            })

    return jsonify({'count': len(items), 'unread': unread, 'notifications': items})


@app.route('/api/notifications/pw_list')
@login_required
def get_pw_list():
    """Return individual PW expiry notifications for home page."""
    pw_notifs = Notification.query.filter_by(resolved=False)\
                    .order_by(Notification.days_left.asc()).all()
    return jsonify([{
        'vm_name':   n.vm_name,
        'days_left': n.days_left,
    } for n in pw_notifs])


@app.route('/api/notifications/pw_update', methods=['POST'])
@login_required
def pw_update():
    """Update a single VM's PW expiry from a live SSH check result."""
    data        = request.get_json() or {}
    vm_name     = data.get('vm_name', '').strip()
    days_left   = data.get('days_left')
    project_key = data.get('project_key', '')
    if not vm_name or days_left is None:
        return jsonify({'error': 'vm_name and days_left required'}), 400
    existing = Notification.query.filter_by(vm_name=vm_name, resolved=False).first()
    if days_left > 30:
        # Password renewed — resolve existing notification
        if existing:
            existing.resolved    = True
            existing.resolved_at = datetime.utcnow()
            db.session.commit()
    else:
        if existing:
            existing.days_left  = days_left
            existing.checked_at = datetime.utcnow()
        else:
            db.session.add(Notification(
                vm_name=vm_name, project_key=project_key,
                days_left=days_left, checked_at=datetime.utcnow(),
            ))
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/notifications/mark_read', methods=['POST'])
@login_required
@csrf.exempt
def mark_notifications_read():
    """Mark specific or all notifications as read for current user."""
    username = current_user.get_id()
    data     = request.get_json(silent=True) or {}
    keys     = data.get('keys', [])  # empty = mark all
    now      = datetime.utcnow()

    if not keys:
        # Mark all — fetch current notification keys
        db_me    = DBUser.query.get(username)
        pw_notifs = Notification.query.filter_by(resolved=False).all()
        if pw_notifs:
            keys.append('pw-group')
        if db_me and db_me.role == 'admin':
            for u in DBUser.query.filter_by(status='pending').all():
                keys.append(f'iam-{u.username}')

    for key in keys:
        if not SeenNotification.query.get((username, key)):
            db.session.add(SeenNotification(username=username, notif_key=key, seen_at=now))
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/notifications/kc_seen', methods=['POST'])
@login_required
@csrf.exempt
def kc_mark_seen():
    """Mark KC notice IDs as seen — stores max ID so anything older is auto-dismissed."""
    data       = request.get_json(silent=True) or {}
    notice_ids = data.get('notice_ids', [])
    username   = current_user.get_id()
    now        = datetime.utcnow()
    if not notice_ids:
        return jsonify({'ok': True})
    max_id = max(notice_ids)
    # Upsert: store max seen ID (replaces any lower previous record)
    existing = SeenKcNotice.query.filter_by(username=username, notice_id=max_id).first()
    if not existing:
        # Delete any lower seen records and insert max
        SeenKcNotice.query.filter(
            SeenKcNotice.username == username,
            SeenKcNotice.notice_id <= max_id
        ).delete()
        db.session.add(SeenKcNotice(username=username, notice_id=max_id, seen_at=now))
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/notifications/kc_unseen_count')
@login_required
def kc_unseen_count():
    """Return count of KC notices newer than the last seen notice ID for this user."""
    import subprocess
    try:
        username = current_user.get_id()
        result   = subprocess.run([
            '/usr/bin/curl', '-s', '--max-time', '10',
            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            '-H', 'Accept-Language: ko-KR,ko;q=0.9',
            '--compressed',
            'https://your-cloud.example.com/more/notices'
        ], capture_output=True, text=True, timeout=15)
        notices = _parse_kc_notices(result.stdout)
        all_ids = [n['id'] for n in notices]
        if not all_ids:
            return jsonify({'count': 0, 'unseen_ids': []})
        # Get the highest notice ID this user has seen
        seen    = SeenKcNotice.query.filter_by(username=username).order_by(SeenKcNotice.notice_id.desc()).first()
        max_seen = seen.notice_id if seen else 0
        # Count notices with ID higher than max seen
        unseen  = [nid for nid in all_ids if nid > max_seen]
        return jsonify({'count': len(unseen), 'unseen_ids': unseen})
    except Exception as e:
        app.logger.debug(f'kc_unseen_count error: {e}')
        return jsonify({'count': 0, 'unseen_ids': []})


@app.route('/api/notifications/kc_notices')
@login_required
@csrf.exempt
def kc_notices():
    """Fetch KakaoCloud notices using curl and parse raw HTML."""
    import subprocess
    try:
        result = subprocess.run([
            '/usr/bin/curl', '-s', '--max-time', '10',
            '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            '-H', 'Accept-Language: ko-KR,ko;q=0.9',
            '--compressed',
            'https://your-cloud.example.com/more/notices'
        ], capture_output=True, text=True, timeout=15)
        notices = _parse_kc_notices(result.stdout)
        return jsonify({'notices': notices})
    except Exception as e:
        app.logger.error(f'KC notices error: {e}')
        return jsonify({'notices': [], 'error': str(e)})

@app.route('/api/provisioning/list_vms', methods=['POST'])
@require_role('admin', 'operator')
@login_required
def list_vms():
    """
    Return all VMs for the given project(s).
    Body: { "project_key": "infra" }  or  { "project_key": "all" }
    Optional: { "force_refresh": true } to bypass cache.
    """
    data = request.get_json(silent=True) or {}
    project_key   = data.get('project_key', 'all').strip()
    force_refresh = data.get('force_refresh', False)
    all_projects  = [k for k in get_projects_from_dotenv() if k not in EXCLUDED_KEYS]

    if project_key == 'all':
        targets = all_projects
    else:
        if project_key not in all_projects:
            return jsonify({'error': f'프로젝트 키를 찾을 수 없습니다: {project_key}'}), 400
        targets = [project_key]

    # ── Redis cache check ──────────────────────────────────────
    cache_key = f'provvms:{project_key}'
    if not force_refresh:
        try:
            cached = REDIS_CLIENT.get(cache_key)
            if cached:
                return jsonify({'vms': json.loads(cached), 'cached': True})
        except Exception as e:
            app.logger.debug(f'Redis cache read error: {e}')

    # ── Fetch from KakaoCloud ──────────────────────────────────
    vms = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_list_all_vms_by_project, k): k for k in targets}
        for future in as_completed(futures):
            try:
                vms.extend(future.result())
            except Exception as e:
                app.logger.debug(f'VM list future error: {e}')

    scode_rules = _get_all_scode_rules()
    result = []
    for vm in vms:
        image_raw  = vm.get('image')
        if isinstance(image_raw, dict):
            image_name = image_raw.get('name', '') or 'K8s node'
        else:
            image_name = 'K8s node'
        # Extract private IP from addresses
        addresses = vm.get('addresses', [])
        private_ip = ''
        if isinstance(addresses, list):
            private_ip = next((a.get('private_ip', '') for a in addresses if a.get('private_ip')), '')
        elif isinstance(addresses, dict):
            for nets in addresses.values():
                for addr in nets:
                    if addr.get('OS-EXT-IPS:type') == 'fixed':
                        private_ip = addr.get('ip_address', '')
                        break

        zone  = vm.get('_project_key', '')
        scode = _resolve_scode(vm.get('name', ''), scode_rules) if scode_rules else 'invalid'
        result.append({
            'vm_name':     vm.get('name', ''),
            'vm_id':       vm.get('id', ''),
            'status':      vm.get('status', ''),
            'flavor':      vm.get('flavor', {}).get('name', '') if isinstance(vm.get('flavor'), dict) else '',
            'az':          vm.get('availability_zone', ''),
            'image':       image_name,
            'os_ok':       _is_ubuntu_24_or_higher(image_name),
            'os_type':     _get_os_type(image_name),
            'project_key': zone,
            'description': scode,
            'key_name':    vm.get('key_name', ''),
            'private_ip':  private_ip,
            'project_id':  vm.get('project_id', ''),
            'created_at':  vm.get('created_at', ''),
        })

    result.sort(key=lambda x: x['vm_name'])
    # ── Store in Redis cache (TTL 60s) ─────────────────────────
    try:
        REDIS_CLIENT.setex(cache_key, 60, json.dumps(result))
    except Exception as e:
        app.logger.debug(f'Redis cache write error: {e}')
    return jsonify({'vms': result, 'cached': False})


PEM_DIR = '/etc/ansible/pem'


def _load_pem_key(pem_path: str):
    """Load a private key from a pem file — supports RSA, Ed25519, ECDSA."""
    for key_class in [
        paramiko.RSAKey,
        paramiko.Ed25519Key,
        paramiko.ECDSAKey,
    ]:
        try:
            return key_class.from_private_key_file(pem_path)
        except paramiko.ssh_exception.SSHException:
            continue
        except Exception:
            continue
    raise ValueError(f'Could not load pem key: {pem_path}')


# _get_ssh_client is imported from helpers.py


def _check_provisioning_via_ssh(hostname: str, key_name: str = '', image: str = '',
                                private_ip: str = '', os_type: str = 'ubuntu24') -> dict:
    """
    Fast SSH check for ansible/puppet status badge.
    Uses single SSH connection with minimal commands.
    Plays detail is handled separately by _check_ansible_plays_detail.
    """
    puppet_cert = _check_puppet_cert_status(hostname) if os_type == 'ubuntu24' else 'na'

    ssh, _ = _get_ssh_client_with_retry(hostname, key_name, image, private_ip)
    if ssh is None:
        return {
            'ansible_done':  False,
            'ansible_error': True,
            'puppet_done':   False,
            'puppet_error':  True,
            'puppet_cert':   puppet_cert,
        }

    try:
        # Single SSH — run ansible + puppet checks together
        if os_type == 'ubuntu24':
            ansible_cmd = 'test -f /etc/puppet/puppet.conf && echo exists || echo missing'
            puppet_cmd  = 'test -f /var/cache/puppet/public/last_run_summary.yaml && echo exists || echo missing'
        else:
            ansible_cmd = 'test -f /etc/default/kic_monitor_agent && echo exists || echo missing'
            puppet_cmd  = None

        _, stdout_a, _ = ssh.exec_command(ansible_cmd, timeout=10)
        ansible_result = stdout_a.read().decode().strip()

        puppet_result = None
        if puppet_cmd:
            _, stdout_p, _ = ssh.exec_command(puppet_cmd, timeout=10)
            puppet_result = stdout_p.read().decode().strip()

        ssh.close()

        return {
            'ansible_done':  ansible_result == 'exists',
            'ansible_error': False,
            'puppet_done':   puppet_result == 'exists' if puppet_result is not None else False,
            'puppet_error':  False,
            'puppet_cert':   puppet_cert,
        }
    except Exception as e:
        app.logger.debug(f'Provisioning check failed for {hostname}: {e}')
        try: ssh.close()
        except Exception: pass
        return {
            'ansible_done':  False,
            'ansible_error': True,
            'puppet_done':   False,
            'puppet_error':  True,
            'puppet_cert':   puppet_cert,
        }


@app.route('/api/provisioning/check_password_expiry', methods=['POST'])
@login_required
@require_role('admin', 'operator')
def check_password_expiry():
    """Check scv OS user password expiry on target VM, normalized to KST."""
    import pytz
    from datetime import datetime as dt

    data       = request.get_json(silent=True) or {}
    vm_name    = data.get('vm_name', '').strip()
    image      = data.get('image', '')
    key_name   = data.get('key_name', '')
    private_ip = data.get('private_ip', '')
    os_type_hint = data.get('os_type', '').strip()

    if not vm_name:
        return jsonify({'error': 'vm_name is required'}), 400

    # Use os_type from frontend if provided, fall back to deriving from image
    os_type = os_type_hint if os_type_hint in ('ubuntu24', 'legacy') else _get_os_type(image)
    if os_type == 'skip':
        return jsonify({'status': 'na', 'label': '-', 'detail': '대상 아님'})

    ssh, _ = _get_ssh_client_with_retry(vm_name, key_name, image, private_ip, retries=2, interval=1)
    if ssh is None:
        return jsonify({'status': 'error', 'label': '확인 불가', 'detail': 'SSH 연결 실패'})

    try:
        # Step 1: Get VM timezone
        _, out, _ = ssh.exec_command(
            "cat /etc/timezone 2>/dev/null || timedatectl 2>/dev/null | grep 'Time zone' | awk '{print $3}'",
            timeout=6)
        tz_lines  = out.read().decode().strip().splitlines()
        vm_tz_str = tz_lines[0].strip() if tz_lines else 'UTC'
        try:
            vm_tz = pytz.timezone(vm_tz_str)
        except Exception:
            vm_tz = pytz.utc
        kst = pytz.timezone('Asia/Seoul')

        # Read password expiry from /etc/shadow
        # Try multiple methods in order of likelihood to work
        shadow_line = ''
        for cmd in [
            "sudo -n awk -F: '/^scv:/{print $3,$5}' /etc/shadow 2>/dev/null",
            "sudo -n grep '^scv:' /etc/shadow 2>/dev/null | cut -d: -f3,5 --output-delimiter=' '",
            "sudo -n cat /etc/shadow 2>/dev/null | grep '^scv:' | cut -d: -f3,5 --output-delimiter=' '",
            "sudo -n getent shadow scv 2>/dev/null | cut -d: -f3,5 --output-delimiter=' '",
        ]:
            _, out, _ = ssh.exec_command(cmd, timeout=6)
            result = out.read().decode().strip()
            if result and 'sorry' not in result.lower() and 'password' not in result.lower():
                shadow_line = result
                app.logger.debug(f'shadow read via [{cmd[:40]}]: {repr(shadow_line)}')
                break

        # If still empty, try again with ubuntu pem key explicitly
        if not shadow_line:
            app.logger.debug(f'shadow empty via scv for {vm_name}, retrying with ubuntu pem')
            pem_path = _resolve_pem_path(key_name)
            if pem_path:
                try:
                    pkey = _load_pem_key(pem_path)
                    is_rocky = 'rocky' in image.lower()
                    fallback_user = 'rocky' if is_rocky else 'ubuntu'
                    target = private_ip if private_ip else vm_name
                    import paramiko as _pm2
                    ssh_pem = _pm2.SSHClient()
                    ssh_pem.set_missing_host_key_policy(_pm2.AutoAddPolicy())
                    ssh_pem.connect(target, username=fallback_user, pkey=pkey, timeout=6)
                    for cmd in [
                        "sudo -n grep '^scv:' /etc/shadow 2>/dev/null | cut -d: -f3,5 --output-delimiter=' '",
                        "sudo -n awk -F: '/^scv:/{print $3,$5}' /etc/shadow 2>/dev/null",
                        "sudo -n getent shadow scv 2>/dev/null | cut -d: -f3,5 --output-delimiter=' '",
                    ]:
                        _, out, _ = ssh_pem.exec_command(cmd, timeout=6)
                        result = out.read().decode().strip()
                        if result and 'sorry' not in result.lower() and 'password' not in result.lower():
                            shadow_line = result
                            app.logger.debug(f'shadow read via pem [{cmd[:40]}]: {repr(shadow_line)}')
                            break
                    ssh_pem.close()
                except Exception as pem_err:
                    app.logger.debug(f'pem shadow retry failed for {vm_name}: {pem_err}')

        shadow_out = shadow_line
        app.logger.debug(f'shadow_out for {vm_name}: {repr(shadow_out)}')

        chage_output = ''
        if shadow_out:
            parts = shadow_out.split()
            app.logger.debug(f'shadow parts for {vm_name}: {parts}')
            if len(parts) >= 2:
                try:
                    last_change_days = int(parts[0])
                    max_days_str     = parts[1].strip()
                    if not max_days_str or max_days_str in ('', '!!', '!'):
                        chage_output = "Password expires: never"
                    else:
                        max_days = int(max_days_str)
                        if max_days < 0 or max_days >= 99999:
                            chage_output = "Password expires: never"
                        elif max_days == 0:
                            from datetime import date
                            epoch  = date(1970, 1, 1)
                            expiry = epoch + __import__('datetime').timedelta(days=last_change_days)
                            chage_output = f"Password expires: {expiry.strftime('%b %d, %Y')}"
                        else:
                            from datetime import date
                            epoch      = date(1970, 1, 1)
                            expiry     = epoch + __import__('datetime').timedelta(days=last_change_days + max_days)
                            chage_output = f"Password expires: {expiry.strftime('%b %d, %Y')}"
                except Exception as parse_err:
                    app.logger.debug(f'shadow parse error for {vm_name}: {parse_err}, parts={parts}')
                    chage_output = ''
            elif len(parts) == 1:
                chage_output = "Password expires: never"

        # Fallback to chage if shadow parsing failed or returned empty
        if not chage_output:
            app.logger.debug(f'shadow empty for {vm_name}, trying chage fallback')
            _, out2, _ = ssh.exec_command('sudo -n chage -l scv 2>/dev/null', timeout=10)
            chage_output = out2.read().decode().strip()
            if not chage_output:
                # Last resort — try without -n (allows TTY prompt but may hang)
                _, out3, _ = ssh.exec_command(
                    "sudo chage -l scv 2>/dev/null || "
                    "awk -F: '/^scv:/{print \"last:\"$3\" max:\"$5}' /etc/shadow 2>/dev/null",
                    timeout=10)
                raw = out3.read().decode().strip()
                app.logger.debug(f'chage fallback raw for {vm_name}: {repr(raw)}')
                if raw.startswith('last:'):
                    # Parse our custom format
                    import re
                    m = re.search(r'last:(\d+)\s+max:(\S+)', raw)
                    if m:
                        try:
                            lc = int(m.group(1))
                            md = int(m.group(2))
                            from datetime import date
                            epoch = date(1970, 1, 1)
                            if md <= 0 or md >= 99999:
                                chage_output = "Password expires: never"
                            else:
                                expiry = epoch + __import__('datetime').timedelta(days=lc + md)
                                chage_output = f"Password expires: {expiry.strftime('%b %d, %Y')}"
                        except Exception:
                            pass
                else:
                    chage_output = raw

        ssh.close()

        if not chage_output:
                return jsonify({'status': 'unknown', 'label': 'chage 출력 없음', 'detail': 'sudo chage -l scv returned empty'})

        # Step 3: Parse expiry date
        expiry_str = None
        for line in chage_output.splitlines():
            if 'Password expires' in line:
                val = line.split(':', 1)[1].strip()
                expiry_str = val
                break

        if not expiry_str or expiry_str.lower() == 'never':
            return jsonify({
                'status': 'ok', 'label': '만료 없음',
                'detail': '비밀번호 만료일 없음', 'expiry_kst': None
            })

        # Step 4: Parse date and convert to KST
        try:
            expiry_naive = dt.strptime(expiry_str, '%b %d, %Y')
        except ValueError:
            return jsonify({'status': 'unknown', 'label': '파싱 오류', 'detail': expiry_str})

        expiry_local = vm_tz.localize(expiry_naive)
        expiry_kst   = expiry_local.astimezone(kst)
        now_kst      = dt.now(kst)
        days_left    = (expiry_kst.date() - now_kst.date()).days

        expiry_label = expiry_kst.strftime('%Y/%m/%d') + ' KST'

        if days_left < 0:
            status = 'expired'
            label  = f'만료됨'
        elif days_left <= 7:
            status = 'critical'
            label  = f'{days_left}d'
        elif days_left <= 30:
            status = 'warning'
            label  = f'{days_left}d'
        else:
            status = 'ok'
            label  = f'{days_left}d'

        return jsonify({
            'status':     status,
            'label':      label,
            'days_left':  days_left,
            'expiry_kst': expiry_label,
            'vm_tz':      vm_tz_str,
        })

    except Exception as e:
        try: ssh.close()
        except Exception: pass
        app.logger.debug(f'Password expiry check failed for {vm_name}: {e}')
        return jsonify({'status': 'error', 'label': '확인 불가', 'detail': str(e)})


@app.route('/api/provisioning/check_plays', methods=['POST'])
@require_role('admin', 'operator')
@login_required
def check_plays():
    """Return detailed per-play ansible status for the detail pane."""
    data       = request.get_json(silent=True) or {}
    vm_name    = data.get('vm_name', '').strip()
    image      = data.get('image', '')
    key_name   = data.get('key_name', '').strip()
    private_ip = data.get('private_ip', '').strip()
    if not vm_name:
        return jsonify({'error': 'vm_name은 필수입니다.'}), 400

    os_type = _get_os_type(image)
    if os_type == 'skip':
        return jsonify({'plays': [], 'na': True})

    plays, ssh_conn = _check_ansible_plays_detail(vm_name, key_name, image, private_ip, os_type)
    if ssh_conn:
        try: ssh_conn.close()
        except: pass
    return jsonify({'plays': plays, 'os_type': os_type})


@app.route('/api/provisioning/check_status', methods=['POST'])
@require_role('admin', 'operator')
@login_required
def check_provisioning_status():
    """
    Real-time provisioning check for a single VM:
    - Ansible done = VM name exists in Route53
    - Puppet done  = ansible done + Ubuntu 24.04+ + puppet service active
    - puppet_error = True when SSH check failed (state unknown)
    """
    data = request.get_json(silent=True) or {}
    vm_name   = data.get('vm_name', '').strip()
    vm_status = data.get('vm_status', '').strip().lower()
    image     = data.get('image', '')
    if not vm_name:
        return jsonify({'error': 'vm_name은 필수입니다.'}), 400

    # Determine OS type first
    os_type = _get_os_type(image)

    # Skip — K8s node, Windows, or unknown OS
    if os_type == 'skip':
        return jsonify({
            'vm_name': vm_name, 'na': True,
            'ansible_done': False, 'ansible_error': False,
            'puppet_done':  False, 'puppet_error':  False,
            'puppet_cert':  'na',  'os_ok': False,
        })

    # Skip non-active VMs
    if vm_status != 'active':
        return jsonify({
            'vm_name': vm_name, 'skipped': True,
            'ansible_done': False, 'ansible_error': False,
            'puppet_done':  False, 'puppet_error':  False,
            'puppet_cert':  'na',  'os_ok': data.get('os_ok', False),
        })

    key_name   = data.get('key_name', '').strip()
    private_ip = data.get('private_ip', '').strip()
    os_ok      = data.get('os_ok', False)

    if os_type == 'ubuntu24':
        # Full plays check — all must pass
        plays, ssh_reuse = _check_ansible_plays_detail(
            vm_name, key_name=key_name, image=image,
            private_ip=private_ip, os_type=os_type
        )
        ansible_done  = len(plays) > 0 and all(p['done'] for p in plays)
        # Treat as SSH error if all SSH-dependent plays failed with SSH 연결 실패
        ssh_failed = ssh_reuse is None and len(plays) > 0
        ansible_error = len(plays) == 0 or ssh_failed
    else:
        # Legacy — partial check: kic_monitor_agent + node_exporter + AllowUsers scv
        ssh, _ = _get_ssh_client_with_retry(vm_name, key_name, image, private_ip)
        if ssh is None:
            ansible_done  = False
            ansible_error = True
        else:
            try:
                _, o1, _ = ssh.exec_command(
                    'test -f /etc/default/kic_monitor_agent && echo exists || echo missing', timeout=10)
                _, o2, _ = ssh.exec_command(
                    'test -f /usr/local/bin/node_exporter && echo exists || echo missing', timeout=10)
                _, o3, _ = ssh.exec_command(
                    'sudo grep -cE "^AllowUsers.*scv" /etc/ssh/sshd_config 2>/dev/null || echo 0', timeout=10)
                r1 = o1.read().decode().strip()
                r2 = o2.read().decode().strip()
                r3 = int(o3.read().decode().strip().splitlines()[0])
                ssh.close()
                ansible_done  = r1 == 'exists' and r2 == 'exists' and r3 > 0
                ansible_error = False
            except Exception:
                try: ssh.close()
                except Exception: pass
                ansible_done  = False
                ansible_error = True

    # Puppet + Account check (ubuntu24 only)
    puppet_cert  = _check_puppet_cert_status(vm_name) if os_type == 'ubuntu24' else 'na'
    puppet_done  = False
    puppet_error = False
    acc_done     = False

    if os_type == 'ubuntu24':
        if ansible_error or ssh_reuse is None:
            puppet_error = True
        else:
            try:
                _, stdout_p, _ = ssh_reuse.exec_command(
                    'test -f /var/cache/puppet/public/last_run_summary.yaml && echo exists || echo missing',
                    timeout=10)
                puppet_done = stdout_p.read().decode().strip() == 'exists'
                # Account check — verify scv user exists
                _, stdout_a, _ = ssh_reuse.exec_command(
                    'getent passwd scv > /dev/null 2>&1 && echo exists || echo missing',
                    timeout=10)
                acc_done = stdout_a.read().decode().strip() == 'exists'
            except Exception:
                puppet_error = True
            finally:
                try: ssh_reuse.close()
                except: pass

    return jsonify({
        'vm_name':       vm_name,
        'os_type':       os_type,
        'os_ok':         os_ok,
        'ansible_done':  ansible_done,
        'ansible_error': ansible_error,
        'puppet_done':   puppet_done,
        'puppet_error':  puppet_error,
        'puppet_cert':   puppet_cert,
        'acc_done':      acc_done,
    })



def _build_ansible_inventory(hostname: str, private_ip: str, image: str, key_name: str, project_id: str = '') -> tuple:
    """
    Create a temporary ansible inventory file.
    Returns (inv_path, access_id, secret_key, playbook_path).
    Caller must delete inv_path after use.
    """
    import tempfile
    inventory_target = private_ip if private_ip else hostname
    is_rocky         = 'rocky' in image.lower()
    os_group         = 'rocky' if is_rocky else 'ubuntu'
    ansible_user     = 'rocky' if is_rocky else 'ubuntu'
    pem_path         = _resolve_pem_path(key_name)

    # Select playbook based on OS type
    os_type = _get_os_type(image)
    if os_type == 'ubuntu24':
        playbook_path = '/etc/ansible/playbook/TOBE-Default-Playbook-V3.yaml'
    else:
        playbook_path = '/etc/ansible/playbook/Default-Playbook-V3.yaml'

    # Look up access_id and secret_key for this project
    access_id, secret_key = PROJECT_CREDS.get(project_id, ('', ''))

    inventory_content = f"""[{os_group}]
{hostname} ansible_host={inventory_target}

[{os_group}:vars]
ansible_ssh_private_key_file={pem_path}
ansible_connection=ssh
ansible_user={ansible_user}
"""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.ini', delete=False, prefix='ansible_inv_'
    ) as f:
        f.write(inventory_content)
        inv_path = f.name

    app.logger.debug(f'Inventory for {hostname} (os_type={os_type}):\n{inventory_content}')
    app.logger.debug(f'Playbook: {playbook_path}')
    return inv_path, access_id, secret_key, playbook_path


@app.route('/api/provisioning/run_ansible', methods=['POST'])
@require_role('admin', 'operator')
@login_required
def run_ansible():
    """Run ansible-playbook against the given VM using a temp inventory."""
    data       = request.get_json(silent=True) or {}
    hostname   = data.get('hostname', '').strip()
    private_ip = data.get('private_ip', '').strip()
    image      = data.get('image', '').strip()
    key_name   = data.get('key_name', '').strip()
    if not hostname:
        return jsonify({'error': '호스트명은 필수입니다.'}), 400

    inv_path = None
    try:
        project_id = data.get('project_id', '').strip()
        inv_path, access_id, secret_key, playbook_path = _build_ansible_inventory(
            hostname, private_ip, image, key_name, project_id)
        cmd = [
            '/usr/bin/sudo', 'ansible-playbook', playbook_path,
            '-i', inv_path,
        ]
        if access_id and secret_key:
            cmd += ['-e', f'access_id={access_id} secret_key={secret_key}']
        app.logger.info(f'Running ansible: {" ".join(cmd)}')
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        log     = result.stdout + result.stderr
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        log = 'Ansible playbook timed out (900s).'; success = False
    except FileNotFoundError:
        log = 'ansible-playbook 명령어를 찾을 수 없습니다.'; success = False
    finally:
        if inv_path:
            try: os.unlink(inv_path)
            except Exception: pass

    return jsonify({'success': success, 'log': log})


@app.route('/api/provisioning/list_playbooks', methods=['GET'])
@require_role('admin', 'operator')
@login_required
def list_playbooks():
    """Recursively list all .yaml/.yml playbook files under /etc/ansible/playbook/"""
    playbook_dir = '/etc/ansible/playbook'
    try:
        files = []
        for root, dirs, filenames in os.walk(playbook_dir):
            dirs.sort()
            for f in sorted(filenames):
                if f.endswith('.yaml') or f.endswith('.yml'):
                    rel = os.path.relpath(os.path.join(root, f), playbook_dir)
                    files.append(rel)
        return jsonify({'playbooks': files})
    except Exception as e:
        return jsonify({'error': str(e), 'playbooks': []}), 500


@app.route('/api/provisioning/stream_ansible', methods=['GET'])
@require_role('admin', 'operator')
@login_required
def stream_ansible():
    """Stream ansible-playbook output line by line via SSE."""
    hostname          = request.args.get('hostname', '').strip()
    private_ip        = request.args.get('private_ip', '').strip()
    image             = request.args.get('image', '').strip()
    key_name          = request.args.get('key_name', '').strip()
    playbook_override = request.args.get('playbook', '').strip()

    if not hostname:
        return jsonify({'error': '호스트명은 필수입니다.'}), 400

    try:
        project_id = request.args.get('project_id', '').strip()
        inv_path, access_id, secret_key, playbook_path = _build_ansible_inventory(
            hostname, private_ip, image, key_name, project_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Use override playbook if provided and valid
    if playbook_override:
        override_path = os.path.normpath(
            os.path.join('/etc/ansible/playbook', playbook_override))
        if override_path.startswith('/etc/ansible/playbook') and os.path.isfile(override_path):
            playbook_path = override_path

    cmd = [
        '/usr/bin/sudo', 'ansible-playbook', playbook_path,
        '-i', inv_path,
    ]
    if access_id and secret_key:
        cmd += ['-e', f'access_id={access_id} secret_key={secret_key}']
    app.logger.info(f'Streaming ansible: {" ".join(cmd)}')

    def generate():
        try:
            # Wait for SSH to be stable before running playbook
            yield f'data: [사전 확인] SSH 안정성 확인 중...\n\n'
            ssh_stable = False
            for attempt in range(3):
                pre_ssh, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                if pre_ssh:
                    try:
                        _, stdout_test, _ = pre_ssh.exec_command('echo ok', timeout=5)
                        result = stdout_test.read().decode().strip()
                        if result == 'ok':
                            ssh_stable = True
                            pre_ssh.close()
                            break
                    except Exception:
                        pass
                    try: pre_ssh.close()
                    except: pass
                yield f'data:   SSH 불안정 — {attempt+1}/3 재시도 중... (2초 후)\n\n'
                time.sleep(2)

            if not ssh_stable:
                # Last resort — try pem key directly even if _get_ssh_client failed
                pem_path = _resolve_pem_path(key_name)
                if pem_path:
                    try:
                        pkey = _load_pem_key(pem_path)
                        is_rocky = 'rocky' in image.lower()
                        fallback_user = 'rocky' if is_rocky else 'ubuntu'
                        target = private_ip if private_ip else hostname
                        import paramiko as _pm
                        ssh_test = _pm.SSHClient()
                        ssh_test.set_missing_host_key_policy(_pm.AutoAddPolicy())
                        ssh_test.connect(target, username=fallback_user, pkey=pkey, timeout=6)
                        ssh_test.close()
                        ssh_stable = True
                        yield f'data:   ubuntu 계정으로 SSH 연결 확인됨\n\n'
                    except Exception:
                        pass

            if not ssh_stable:
                yield f'data: [사전 확인] 오류: SSH 연결 안정화 실패 (3회 시도)\n\n'
                yield f'data: __EXIT__1\n\n'
                return

            yield f'data:   SSH 안정적\n\n'

            # Wait for apt lock to be released on target VM before running playbook
            yield f'data: [사전 확인] apt 잠금 상태 확인 중...\n\n'
            try:
                pre_ssh, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                if pre_ssh:
                    wait_cmd = (
                        'timeout 120 bash -c \''
                        'while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock '
                        '/var/cache/apt/archives/lock >/dev/null 2>&1; do '
                        'echo "apt 잠금 해제 대기 중..."; sleep 3; done; '
                        'echo "apt 잠금 해제됨"\''
                    )
                    _, stdout_pre, _ = pre_ssh.exec_command(wait_cmd, timeout=125)
                    for line in iter(stdout_pre.readline, ''):
                        line = line.rstrip()
                        if line:
                            yield f'data:   {line}\n\n'
                    pre_ssh.close()
            except Exception as e:
                yield f'data: [사전 확인] apt 잠금 확인 실패 (무시): {e}\n\n'
            yield f'data: [사전 확인] 완료 — ansible-playbook 시작\n\n'
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, 'ANSIBLE_FORCE_COLOR': '0', 'PYTHONUNBUFFERED': '1'}
            )
            import select, threading

            # Heartbeat thread — sends comment every 15s to keep connection alive
            stop_heartbeat = threading.Event()
            heartbeat_q    = []

            def heartbeat():
                while not stop_heartbeat.is_set():
                    stop_heartbeat.wait(15)
                    if not stop_heartbeat.is_set():
                        heartbeat_q.append(': keepalive\n\n')

            hb_thread = threading.Thread(target=heartbeat, daemon=True)
            hb_thread.start()

            while True:
                # Flush any heartbeats first
                while heartbeat_q:
                    yield heartbeat_q.pop(0)

                # Check if there's output available (non-blocking)
                ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                if ready:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    yield f'data: {line.rstrip()}\n\n'
                elif proc.poll() is not None:
                    # Process finished, drain remaining output
                    remaining = proc.stdout.read()
                    for line in remaining.splitlines():
                        yield f'data: {line}\n\n'
                    break

            stop_heartbeat.set()
            proc.stdout.close()
            proc.wait()
            rc = proc.returncode
            with app.app_context():
                _log_history(
                    'ANSIBLE',
                    f'Ansible: {os.path.basename(playbook_path)} ({hostname})',
                    None,
                    current_user.get_id() if current_user.is_authenticated else None
                )
            yield f'data: __EXIT__{rc}\n\n'
        except Exception as e:
            yield f'data: __ERROR__{str(e)}\n\n'
        finally:
            try: os.unlink(inv_path)
            except Exception: pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )




@app.route('/api/provisioning/bulk_register', methods=['POST'])
@login_required
@require_role('admin', 'operator')
def bulk_register():
    """Store bulk VM list in Redis and return a short token."""
    import uuid
    data    = request.get_json(silent=True) or {}
    token   = str(uuid.uuid4())
    payload = json.dumps({'vms': data.get('vms', []), 'playbook': data.get('playbook', '')})
    REDIS_CLIENT.setex(f'bulk:{token}', 300, payload)
    return jsonify({'token': token})


@app.route('/api/provisioning/stream_bulk_ansible', methods=['GET'])
@login_required
@require_role('admin', 'operator')
def stream_bulk_ansible():
    """Stream ansible-playbook for multiple VMs, split by os_type into separate runs."""
    import json as _json, tempfile

    # Support token (avoids URL length limit for 40+ VMs)
    token = request.args.get('token', '').strip()
    if token:
        raw = REDIS_CLIENT.get(f'bulk:{token}')
        if not raw:
            return jsonify({'error': '토큰이 만료되었거나 유효하지 않습니다.'}), 400
        payload           = _json.loads(raw)
        vms               = payload.get('vms', [])
        playbook_override = payload.get('playbook', '')
        REDIS_CLIENT.delete(f'bulk:{token}')
    else:
        vms_json          = request.args.get('vms', '[]')
        playbook_override = request.args.get('playbook', '').strip()
        try:
            vms = _json.loads(vms_json)
        except Exception:
            return jsonify({'error': 'Invalid vms parameter'}), 400

    if not vms:
        return jsonify({'error': 'VM 목록이 비어있습니다.'}), 400

    def vm_inv_line(v):
        ip = v.get('private_ip') or v.get('hostname')
        return f"{v['hostname']} ansible_host={ip}"

    def build_group_inventory(group_vms, playbook_path=''):
        """Build inventory splitting ubuntu and rocky into separate groups."""
        ubuntu_vms = [v for v in group_vms if 'rocky' not in v.get('image', '').lower()]
        rocky_vms  = [v for v in group_vms if 'rocky' in v.get('image', '').lower()]
        pb          = os.path.basename(playbook_path)
        is_pw_play  = 'password' in pb.lower()
        lines = []
        for os_group, user, vms_subset in [('ubuntu', 'scv' if is_pw_play else 'ubuntu', ubuntu_vms),
                                            ('rocky',  'scv' if is_pw_play else 'rocky',  rocky_vms)]:
            if not vms_subset:
                continue
            first    = vms_subset[0]
            pem_path = _resolve_pem_path(first.get('key_name', ''))
            lines.append(f'[{os_group}]')
            lines += [vm_inv_line(v) for v in vms_subset]
            lines += ['', f'[{os_group}:vars]', 'ansible_connection=ssh', f'ansible_user={user}']
            if is_pw_play:
                lines.append('ansible_ssh_pass={{ vault_ssh_pass }}')
                lines.append('ansible_become=yes')
                lines.append('ansible_become_pass={{ vault_become_pass }}')
            elif pem_path:
                lines.append(f'ansible_ssh_private_key_file={pem_path}')
            lines.append('')
        return '\n'.join(lines)

    # Build groups
    if playbook_override:
        override_path = os.path.normpath(
            os.path.join('/etc/ansible/playbook', playbook_override))
        if override_path.startswith('/etc/ansible/playbook') and os.path.isfile(override_path):
            groups = [(os.path.basename(override_path), override_path, vms)]
        else:
            groups = []
    else:
        tobe_vms   = [v for v in vms if _get_os_type(v.get('image', '')) == 'ubuntu24']
        legacy_vms = [v for v in vms if _get_os_type(v.get('image', '')) == 'legacy']
        groups = []
        if tobe_vms:
            groups.append(('TOBE-Default-Playbook-V3.yaml',
                           '/etc/ansible/playbook/TOBE-Default-Playbook-V3.yaml', tobe_vms))
        if legacy_vms:
            groups.append(('Default-Playbook-V3.yaml',
                           '/etc/ansible/playbook/Default-Playbook-V3.yaml', legacy_vms))

    def generate():
        overall_rc = 0
        for playbook_name, playbook_path, group_vms in groups:
            inv_path = None
            try:
                inv_content = build_group_inventory(group_vms, playbook_path)
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.ini', delete=False, prefix='ansible_bulk_inv_'
                ) as f:
                    f.write(inv_content)
                    inv_path = f.name

                project_id = group_vms[0].get('project_id', '')
                access_id, secret_key = PROJECT_CREDS.get(project_id, ('', ''))

                yield f'data: \n\n'
                yield f'data: ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
                yield f'data: [{playbook_name}] {len(group_vms)}개 VM\n\n'
                yield f'data: ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
                for v in group_vms:
                    yield f'data: * {v["hostname"]}\n\n'
                yield f'data: \n\n'

                cmd = ['/usr/bin/sudo', 'ansible-playbook', playbook_path, '-i', inv_path]
                if access_id and secret_key:
                    cmd += ['-e', f'access_id={access_id} secret_key={secret_key}']

                app.logger.info(f'Bulk ansible {playbook_name} ({len(group_vms)} VMs): {" ".join(cmd)}')

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    env={**os.environ, 'ANSIBLE_FORCE_COLOR': '0', 'PYTHONUNBUFFERED': '1'}
                )
                import select, threading
                stop_hb = threading.Event()
                hb_q    = []
                def _hb():
                    while not stop_hb.is_set():
                        stop_hb.wait(15)
                        if not stop_hb.is_set(): hb_q.append(': keepalive\n\n')
                threading.Thread(target=_hb, daemon=True).start()
                while True:
                    while hb_q: yield hb_q.pop(0)
                    ready, _, _ = select.select([proc.stdout], [], [], 1.0)
                    if ready:
                        line = proc.stdout.readline()
                        if not line: break
                        yield f'data: {line.rstrip()}\n\n'
                    elif proc.poll() is not None:
                        for line in proc.stdout.read().splitlines():
                            yield f'data: {line}\n\n'
                        break
                stop_hb.set()
                proc.stdout.close()
                proc.wait()
                rc = proc.returncode
                if rc != 0:
                    overall_rc = rc
                yield f'data: \n\n'
                yield f'data: [{playbook_name}] {"완료 ✓" if rc == 0 else f"실패 (exit: {rc})"}\n\n'
                vm_bullet_list = '\n'.join(f'* {v["hostname"]}' for v in group_vms)
                with app.app_context():
                    _log_history(
                        'ANSIBLE',
                        f'Bulk Ansible: {playbook_name} ({len(group_vms)}개 VM)',
                        f'대상:\n{vm_bullet_list}',
                        current_user.get_id() if current_user.is_authenticated else None
                    )

            except Exception as e:
                yield f'data: 오류: {e}\n\n'
                overall_rc = 1
            finally:
                if inv_path:
                    try: os.unlink(inv_path)
                    except Exception: pass

        yield f'data: __EXIT__{overall_rc}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


@app.route('/api/provisioning/stream_bulk_puppet', methods=['GET'])
@login_required
@require_role('admin', 'operator')
def stream_bulk_puppet():
    """Stream puppet provisioning for multiple VMs sequentially."""
    import json as _json

    token = request.args.get('token', '').strip()
    if token:
        raw = REDIS_CLIENT.get(f'bulk:{token}')
        if not raw:
            return jsonify({'error': '토큰이 만료되었거나 유효하지 않습니다.'}), 400
        payload = _json.loads(raw)
        vms     = payload.get('vms', [])
        REDIS_CLIENT.delete(f'bulk:{token}')
    else:
        vms_json = request.args.get('vms', '[]')
        try:
            vms = _json.loads(vms_json)
        except Exception:
            return jsonify({'error': 'Invalid vms parameter'}), 400

    if not vms:
        return jsonify({'error': 'VM 목록이 비어있습니다.'}), 400

    def generate():
        total = len(vms)
        success_count = 0

        for idx, vm in enumerate(vms):
            hostname   = vm.get('hostname', '')
            key_name   = vm.get('key_name', '')
            image      = vm.get('image', '')
            private_ip = vm.get('private_ip', '')
            fqdn       = f'{hostname}.internal.example.com'

            yield f'data: \n\n'
            yield f'data: ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
            yield f'data: [VM {idx+1}/{total}] {hostname}\n\n'
            yield f'data: ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'

            try:
                # Step 1: Check cert
                yield f'data: [1/4] 인증서 상태 확인 중...\n\n'
                cert_status = _check_puppet_cert_status(hostname)
                yield f'data:       인증서 상태: {cert_status}\n\n'

                # Step 2: Request cert if none
                if cert_status == 'none':
                    yield f'data: [2/4] puppet agent -t 실행 (인증서 요청)...\n\n'
                    vm_ssh = None
                    for ssh_attempt in range(5):
                        vm_ssh, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                        if vm_ssh:
                            break
                        yield f'data:       SSH 연결 실패 — {ssh_attempt+1}/5 재시도 중... (5초 후)\n\n'
                        time.sleep(5)
                    if vm_ssh is None:
                        yield f'data:       오류: SSH 연결 실패 (5회 시도) — 건너뜀\n\n'
                        yield f'data: __VM_FAIL__{hostname}\n\n'
                        continue
                    _, stdout, _ = vm_ssh.exec_command('sudo puppet agent -t 2>&1', timeout=120)
                    for line in iter(stdout.readline, ''):
                        yield f'data:       {line.rstrip()}\n\n'
                    vm_ssh.close()
                    # Clear cert cache so next check fetches fresh data from puppet master
                    _puppet_cert_cache['data'] = None
                    _puppet_cert_cache['ts']   = 0
                    # Wait for puppet master to register the request (retry up to 5 times)
                    yield f'data:       puppet master에 인증서 등록 대기 중...\n\n'
                    for attempt in range(5):
                        time.sleep(5)
                        cert_status = _check_puppet_cert_status(hostname)
                        if cert_status != 'none':
                            break
                        yield f'data:       대기 중... ({(attempt+1)*5}초)\n\n'
                    yield f'data:       갱신된 인증서 상태: {cert_status}\n\n'
                else:
                    yield f'data: [2/4] 건너뜀 — 인증서 이미 존재 ({cert_status})\n\n'

                # Step 3: Sign cert
                if cert_status == 'requested':
                    yield f'data: [3/4] puppet master에서 인증서 서명 중...\n\n'
                    master_ssh = paramiko.SSHClient()
                    master_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    master_ssh.connect(PUPPET_MASTER, username=SSH_USERNAME,
                                       password=SSH_PASSWORD, timeout=8)
                    _, stdout, _ = master_ssh.exec_command(
                        f'sudo puppetserver ca sign --certname {fqdn} 2>&1', timeout=60)
                    sign_out = stdout.read().decode().strip()
                    master_ssh.close()
                    yield f'data:       {sign_out}\n\n'
                    if 'signed' in sign_out.lower() or 'successfully' in sign_out.lower():
                        cert_status = 'signed'
                    else:
                        yield f'data:       오류: 인증서 서명 실패 — 건너뜀\n\n'
                        yield f'data: __VM_FAIL__{hostname}\n\n'
                        continue
                elif cert_status == 'signed':
                    yield f'data: [3/4] 건너뜀 — 인증서 이미 서명됨\n\n'
                else:
                    yield f'data: [3/4] 오류: 인증서 상태 이상 ({cert_status}) — 건너뜀\n\n'
                    yield f'data: __VM_FAIL__{hostname}\n\n'
                    continue

                # Step 4: Apply manifest
                yield f'data: [4/4] puppet agent -t 실행 (manifest 적용)...\n\n'
                vm_ssh2 = None
                for ssh_attempt in range(5):
                    vm_ssh2, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                    if vm_ssh2:
                        break
                    yield f'data:       SSH 연결 실패 — {ssh_attempt+1}/5 재시도 중... (5초 후)\n\n'
                    time.sleep(5)
                if vm_ssh2 is None:
                    yield f'data:       오류: SSH 연결 실패 (5회 시도)\n\n'
                    yield f'data: __VM_FAIL__{hostname}\n\n'
                    continue
                _, stdout2, _ = vm_ssh2.exec_command('sudo puppet agent -t 2>&1', timeout=300)
                for line in iter(stdout2.readline, ''):
                    yield f'data:       {line.rstrip()}\n\n'
                exit_code = stdout2.channel.recv_exit_status()
                vm_ssh2.close()

                if exit_code in (0, 2):
                    yield f'data:       ✓ manifest 적용 완료\n\n'
                    yield f'data: __VM_DONE__{hostname}\n\n'
                    success_count += 1
                    with app.app_context():
                        _log_history('PUPPET', f'Puppet 완료: {hostname}', None,
                                     current_user.get_id() if current_user.is_authenticated else None)
                else:
                    yield f'data:       puppet 실패 (exit: {exit_code})\n\n'
                    yield f'data: __VM_FAIL__{hostname}\n\n'
                    with app.app_context():
                        _log_history('PUPPET', f'Puppet 실패: {hostname}', None,
                                     current_user.get_id() if current_user.is_authenticated else None)

            except Exception as e:
                yield f'data:       오류: {e}\n\n'
                yield f'data: __VM_FAIL__{hostname}\n\n'

        yield f'data: \n\n'
        yield f'data: ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n'
        yield f'data: 완료: {success_count}/{total} VM\n\n'
        yield f'data: __EXIT__{0 if success_count == total else 1}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


@app.route('/api/provisioning/stream_puppet', methods=['GET'])
@require_role('admin', 'operator')
@login_required
def stream_puppet():
    """
    Full automated puppet provisioning flow via SSE:
    1. Check cert status on puppet master
    2. If no cert → run puppet agent -t on target VM (triggers cert request)
    3. Sign cert on puppet master
    4. Run puppet agent -t again on target VM (applies manifest)
    """
    hostname   = request.args.get('hostname', '').strip()
    key_name   = request.args.get('key_name', '').strip()
    image      = request.args.get('image', '').strip()
    private_ip = request.args.get('private_ip', '').strip()
    if not hostname:
        return jsonify({'error': '호스트명은 필수입니다.'}), 400

    fqdn = f'{hostname}.internal.example.com'

    def generate():
        def log(msg):
            return f'data: {msg}\n\n'

        try:
            # ── Step 1: Check cert status ──────────────────────
            yield log(f'[1/4] puppet master에서 인증서 상태 확인 중... ({fqdn})')
            cert_status = _check_puppet_cert_status(hostname)
            yield log(f'      인증서 상태: {cert_status}')

            # ── Step 2: If no cert → run puppet agent -t to request cert ──
            if cert_status == 'none':
                yield log(f'[2/4] 인증서 없음 — {hostname}에서 puppet agent -t 실행 (인증서 요청)...')
                # SSH retry — VM may still be booting
                vm_ssh = None
                for ssh_attempt in range(5):
                    vm_ssh, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                    if vm_ssh:
                        break
                    yield log(f'      SSH 연결 실패 — {ssh_attempt+1}/5 재시도 중... (5초 후)')
                    time.sleep(5)
                if vm_ssh is None:
                    yield log(f'      오류: SSH 연결 실패 (5회 시도)')
                    yield f'data: __EXIT__1\n\n'
                    return
                _, stdout, _ = vm_ssh.exec_command('sudo puppet agent -t 2>&1', timeout=120)
                import queue as _queue2, threading as _threading2
                out_q2  = _queue2.Queue()
                def _ssh_reader2():
                    for line in iter(stdout.readline, ''):
                        out_q2.put(line)
                    out_q2.put(None)
                _threading2.Thread(target=_ssh_reader2, daemon=True).start()
                while True:
                    try:
                        line = out_q2.get(timeout=15)
                        if line is None: break
                        yield log(f'      {line.rstrip()}')
                    except _queue2.Empty:
                        yield ': keepalive\n\n'
                vm_ssh.close()
                yield log(f'      인증서 요청 완료')
                # Clear cert cache so next check fetches fresh data from puppet master
                _puppet_cert_cache['data'] = None
                _puppet_cert_cache['ts']   = 0
                # Wait for puppet master to register the request (retry up to 5 times)
                yield log(f'      puppet master에 인증서 등록 대기 중...')
                for attempt in range(5):
                    time.sleep(5)
                    cert_status = _check_puppet_cert_status(hostname)
                    if cert_status != 'none':
                        break
                    yield log(f'      대기 중... ({(attempt+1)*5}초)')
                yield log(f'      갱신된 인증서 상태: {cert_status}')
            else:
                yield log(f'[2/4] 건너뜀 — 인증서 이미 존재 ({cert_status})')

            # ── Step 3: Sign cert on puppet master ─────────────
            if cert_status == 'requested':
                yield log(f'[3/4] puppet master에서 인증서 서명 중... ({fqdn})')
                master_ssh = paramiko.SSHClient()
                master_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                master_ssh.connect(PUPPET_MASTER, username=SSH_USERNAME,
                                   password=SSH_PASSWORD, timeout=8)
                _, stdout, stderr = master_ssh.exec_command(
                    f'sudo puppetserver ca sign --certname {fqdn} 2>&1', timeout=60)
                sign_out = stdout.read().decode().strip()
                master_ssh.close()
                yield log(f'      {sign_out}')
                if 'signed' in sign_out.lower() or 'successfully' in sign_out.lower():
                    yield log(f'      인증서 서명 완료')
                    cert_status = 'signed'
                else:
                    yield log(f'      오류: 인증서 서명 실패')
                    yield f'data: __EXIT__1\n\n'
                    return
            elif cert_status == 'signed':
                yield log(f'[3/4] 건너뜀 — 인증서 이미 서명됨')
            else:
                yield log(f'[3/4] 오류: 인증서 상태가 requested가 아님 ({cert_status})')
                yield f'data: __EXIT__1\n\n'
                return

            # ── Step 4: Run puppet agent -t to apply manifest ──
            yield log(f'[4/4] {hostname}에서 puppet agent -t 실행 (manifest 적용)...')
            vm_ssh2 = None
            for ssh_attempt in range(5):
                vm_ssh2, _ = _get_ssh_client(hostname, key_name, image, private_ip)
                if vm_ssh2:
                    break
                yield log(f'      SSH 연결 실패 — {ssh_attempt+1}/5 재시도 중... (5초 후)')
                time.sleep(5)
            if vm_ssh2 is None:
                yield log(f'      오류: SSH 연결 실패 (5회 시도)')
                yield f'data: __EXIT__1\n\n'
                return
            _, stdout2, _ = vm_ssh2.exec_command('sudo puppet agent -t 2>&1', timeout=600)
            # Read SSH output with heartbeat to keep SSE connection alive
            import queue as _queue, threading as _threading
            out_q   = _queue.Queue()
            def _ssh_reader():
                for line in iter(stdout2.readline, ''):
                    out_q.put(line)
                out_q.put(None)  # sentinel
            _threading.Thread(target=_ssh_reader, daemon=True).start()
            while True:
                try:
                    line = out_q.get(timeout=15)
                    if line is None:
                        break
                    yield log(f'      {line.rstrip()}')
                except _queue.Empty:
                    yield ': keepalive\n\n'  # SSE comment heartbeat
            exit_code = stdout2.channel.recv_exit_status()
            vm_ssh2.close()

            if exit_code in (0, 2):  # 0=no changes, 2=changes applied
                yield log(f'      puppet manifest 적용 완료')
                with app.app_context():
                    _log_history('PUPPET', f'Puppet 완료: {hostname}', None,
                                 current_user.get_id() if current_user.is_authenticated else None)
                yield f'data: __EXIT__0\n\n'
            else:
                yield log(f'      puppet agent 실패 (exit code: {exit_code})')
                with app.app_context():
                    _log_history('PUPPET', f'Puppet 실패: {hostname}', None,
                                 current_user.get_id() if current_user.is_authenticated else None)
                yield f'data: __EXIT__1\n\n'

        except Exception as e:
            yield log(f'오류: {e}')
            yield f'data: __EXIT__1\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


@app.route('/api/provisioning/run_puppet', methods=['POST'])
@login_required
def run_puppet():
    """Legacy run_puppet — kept for compatibility."""
    return jsonify({'error': 'stream_puppet 엔드포인트를 사용하세요.'}), 400







# ════════════════════════════════════════════════════════════════
# VM 생성 (VM Create) feature
# ════════════════════════════════════════════════════════════════

@app.route('/vm_create')
@login_required
@require_role('admin', 'operator')
def vm_create_page():
    """VM 생성 page — multi-VM creation wizard with live API-driven dropdowns."""
    from models import Project
    project_rows = Project.query.order_by(Project.zone).all()
    project_list = [
        {'key': p.zone, 'alias': p.alias or ''}
        for p in project_rows
        if p.zone not in EXCLUDED_KEYS
    ]
    return render_template('vm_create.html', projects=project_list)


@app.route('/api/vm_create/resources')
@login_required
@require_role('admin', 'operator')
def api_vm_create_resources():
    """Fetch subnets/security-groups/keypairs/flavors/images for one project,
    used to populate the dropdowns in a VM block once a project is selected."""
    project_key = request.args.get('project', '').strip()
    if not project_key:
        return jsonify({'error': 'project가 필요합니다.'}), 400
    if project_key in EXCLUDED_KEYS:
        return jsonify({'error': '유효하지 않은 프로젝트입니다.'}), 400
    token = get_env_var(project_key, default='')
    if not token or token.strip().lower() == 'none':
        return jsonify({'error': f"프로젝트 '{project_key}'에 대한 토큰이 없습니다."}), 400
    try:
        data = vm_create.list_all_resources(token)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/vm_create/register', methods=['POST'])
@login_required
@require_role('admin', 'operator')
def api_vm_create_register():
    """Store the VM block configs in Redis and return a short token —
    avoids URL length limits when creating many VMs at once with
    full per-VM config (mirrors /api/provisioning/bulk_register)."""
    import uuid
    data = request.get_json(silent=True) or {}
    vm_configs = data.get('vms', [])
    if not isinstance(vm_configs, list) or not vm_configs:
        return jsonify({'error': 'vms는 비어있지 않은 배열이어야 합니다.'}), 400
    token = str(uuid.uuid4())
    REDIS_CLIENT.setex(f'vmcreate:{token}', 300, json.dumps(vm_configs))
    return jsonify({'token': token})


@app.route('/api/vm_create/stream', methods=['GET'])
@login_required
@require_role('admin', 'operator')
def api_vm_create_stream():
    """SSE stream — creates N VMs (each with its own full config) with
    bounded concurrency (3 at a time), streaming live per-step progress.

    Reads the VM block configs from Redis via a short-lived token created
    by /api/vm_create/register (avoids URL length limits)."""
    import json as _json
    from concurrent.futures import ThreadPoolExecutor

    token = request.args.get('token', '').strip()
    if not token:
        return jsonify({'error': 'token이 필요합니다.'}), 400
    raw = REDIS_CLIENT.get(f'vmcreate:{token}')
    if not raw:
        return jsonify({'error': '토큰이 만료되었거나 유효하지 않습니다.'}), 400
    REDIS_CLIENT.delete(f'vmcreate:{token}')
    try:
        vm_configs = _json.loads(raw)
        if not isinstance(vm_configs, list) or not vm_configs:
            raise ValueError('vms must be a non-empty list')
    except Exception as e:
        return jsonify({'error': f'잘못된 vms 데이터: {e}'}), 400

    def create_one_vm(idx, total, cfg, log_queue):
        """Runs in a worker thread — creates volumes then the instance for
        one VM block, pushing progress lines onto the shared queue."""
        name = cfg.get('name', f'vm-{idx}')
        prefix = f'[VM {idx}/{total}: {name}]'

        def tick(msg):
            log_queue.put(f'{prefix} {msg}')

        try:
            project_key = cfg['project']
            if project_key in EXCLUDED_KEYS:
                tick(f'오류: 유효하지 않은 프로젝트 \'{project_key}\'')
                log_queue.put(('__VM_FAIL__', name))
                return
            token = get_env_var(project_key, default='')
            if not token or token.strip().lower() == 'none':
                tick(f'오류: 프로젝트 \'{project_key}\' 토큰 없음')
                log_queue.put(('__VM_FAIL__', name))
                return

            az = cfg['availability_zone']

            # Step 1: create extra volumes (always delete_on_termination=False)
            extra_volume_ids = []
            extras = cfg.get('extra_volumes', [])
            for i, ev in enumerate(extras, 1):
                tick(f'볼륨 생성 중 ({i}/{len(extras)}): {ev["name"]} ({ev["size"]}GB)...')
                vol_id = vm_create.create_volume(token, ev['name'], ev['size'], az, on_tick=tick)
                vm_create.wait_for_volume(token, vol_id, on_tick=tick)
                extra_volume_ids.append(vol_id)

            # Step 2: create the instance
            tick('인스턴스 생성 중...')
            result = vm_create.create_instance(
                token              = token,
                name               = name,
                image_id           = cfg['image_id'],
                flavor_id          = cfg['flavor_id'],
                subnet_id          = cfg['subnet_id'],
                availability_zone  = az,
                security_group_names = cfg.get('security_groups', []),
                keypair_name       = cfg['keypair'],
                root_volume_size   = cfg.get('root_volume_size', 50),
                extra_volume_ids   = extra_volume_ids,
            )
            tick('✓ 완료')
            log_queue.put(('__VM_DONE__', name))
            # Invalidate provisioning VM cache so new VM appears immediately
            try:
                REDIS_CLIENT.delete(f'provvms:{project_key}')
                REDIS_CLIENT.delete('provvms:all')
            except Exception:
                pass
            try:
                _log_history('VM_CREATE', f'VM 생성: {name}',
                              f'{name} 생성 완료 (프로젝트: {project_key})',
                              current_user.get_id())
            except Exception:
                pass
        except Exception as e:
            tick(f'오류: {e}')
            log_queue.put(('__VM_FAIL__', name))

    def generate():
        import queue
        log_queue = queue.Queue()
        total = len(vm_configs)
        yield f'data: 총 {total}개 VM 생성을 시작합니다 (순차 처리)\n\n'

        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = [
                executor.submit(create_one_vm, i + 1, total, cfg, log_queue)
                for i, cfg in enumerate(vm_configs)
            ]
            done_count = 0
            while done_count < total:
                # Drain whatever's in the queue so progress streams live
                # even while other VMs are still being created.
                try:
                    item = log_queue.get(timeout=0.5)
                except Exception:
                    # Check if all futures finished even with an empty queue
                    if all(f.done() for f in futures) and log_queue.empty():
                        break
                    continue
                if isinstance(item, tuple):
                    tag, name = item
                    yield f'data: {tag}{name}\n\n'
                    done_count += 1
                else:
                    yield f'data: {item}\n\n'

        yield 'data: __ALL_DONE__\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection':        'keep-alive',
        }
    )


# ── Startup ───────────────────────────────────────────────────
start_background_threads(app)

with app.app_context():
    _init_db(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
