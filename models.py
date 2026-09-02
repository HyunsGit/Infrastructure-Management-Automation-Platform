# models.py — SQLAlchemy models + DB initialisation
from datetime import datetime
from werkzeug.security import generate_password_hash
from env_manager import get_env_var
from extensions import db


class DBUser(db.Model):
    __tablename__         = 'users'
    username              = db.Column(db.String(80),  primary_key=True)
    password              = db.Column(db.String(256), nullable=False)
    totp_secret           = db.Column(db.String(32),  nullable=True)
    must_change_password  = db.Column(db.Boolean,     default=False)
    created_at            = db.Column(db.DateTime,    default=datetime.utcnow)
    last_login            = db.Column(db.DateTime,    nullable=True)
    is_admin              = db.Column(db.Boolean,     default=False)
    role                  = db.Column(db.String(20),  default='viewer')
    status                = db.Column(db.String(20),  default='active')
    email                 = db.Column(db.String(120), nullable=True)
    role_reason           = db.Column(db.Text,        nullable=True)

class IAMHistory(db.Model):
    __tablename__   = 'iam_history'
    id              = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    username        = db.Column(db.String(80),  nullable=False)
    action          = db.Column(db.String(40),  nullable=False)
    role_requested  = db.Column(db.String(20),  nullable=True)
    reason          = db.Column(db.Text,        nullable=True)
    decided_by      = db.Column(db.String(80),  nullable=True)
    created_at      = db.Column(db.DateTime,    default=datetime.utcnow)
    decided_at      = db.Column(db.DateTime,    nullable=True)

class SeenNotification(db.Model):
    __tablename__ = 'seen_notifications'
    username  = db.Column(db.String(80),  primary_key=True)
    notif_key = db.Column(db.String(100), primary_key=True)
    seen_at   = db.Column(db.DateTime,    default=datetime.utcnow)

class SeenKcNotice(db.Model):
    __tablename__ = 'seen_kc_notices'
    username  = db.Column(db.String(80),  primary_key=True)
    notice_id = db.Column(db.Integer,     primary_key=True)
    seen_at   = db.Column(db.DateTime,    default=datetime.utcnow)

class Notification(db.Model):
    __tablename__ = 'notifications'
    id          = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    vm_name     = db.Column(db.String(120), nullable=False)
    project_key = db.Column(db.String(80),  nullable=True)
    days_left   = db.Column(db.Integer,     nullable=False)
    checked_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    resolved    = db.Column(db.Boolean,     default=False)
    resolved_at = db.Column(db.DateTime,    nullable=True)

class AppHistory(db.Model):
    __tablename__ = 'app_history'
    id          = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    event_type  = db.Column(db.String(20),  nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    detail      = db.Column(db.Text,        nullable=True)
    username    = db.Column(db.String(80),  nullable=True)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)

class AppChangelog(db.Model):
    __tablename__ = 'app_changelog'
    id          = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    version     = db.Column(db.String(40),  nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    change_type = db.Column(db.String(20),  nullable=False)
    description = db.Column(db.Text,        nullable=False)
    image_url   = db.Column(db.Text,        nullable=True)
    icon        = db.Column(db.String(20),  nullable=True)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    created_by  = db.Column(db.String(80),  nullable=True)

class DeletedProject(db.Model):
    __tablename__ = 'deleted_projects'
    zone       = db.Column(db.String(80), primary_key=True)
    deleted_at = db.Column(db.DateTime,   default=datetime.utcnow)
    deleted_by = db.Column(db.String(80), nullable=True)

class Project(db.Model):
    __tablename__      = 'projects'
    zone               = db.Column(db.String(80),  primary_key=True)
    access_id          = db.Column(db.String(128), nullable=False)
    secret_key         = db.Column(db.String(256), nullable=False)
    token              = db.Column(db.Text,        nullable=True)
    token_refreshed_at = db.Column(db.DateTime,    nullable=True)
    alias              = db.Column(db.String(120), nullable=True)

class SCodeInfo(db.Model):
    __tablename__ = 'scode_info'
    scode      = db.Column(db.String(80),  primary_key=True)
    engineer   = db.Column(db.String(120), nullable=True)
    developer  = db.Column(db.String(120), nullable=True)
    manager    = db.Column(db.String(120), nullable=True)
    updated_at = db.Column(db.DateTime,    default=datetime.utcnow)
    updated_by = db.Column(db.String(80),  nullable=True)

class SCodeRule(db.Model):
    __tablename__ = 'scode_rules'
    id          = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    zone        = db.Column(db.String(80),  nullable=False, index=True)
    priority    = db.Column(db.Integer,     nullable=False, default=0)
    match_type  = db.Column(db.String(20),  nullable=False, default='contains')
    match_value = db.Column(db.String(200), nullable=False)
    scode       = db.Column(db.String(80),  nullable=False)
    is_default  = db.Column(db.Boolean,     default=False)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)
    created_by  = db.Column(db.String(80),  nullable=True)

class SCodeVmSnapshot(db.Model):
    __tablename__ = 'scode_vm_snapshots'
    id         = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    date       = db.Column(db.String(10),  nullable=False)
    scode      = db.Column(db.String(80),  nullable=False)
    vm_id      = db.Column(db.String(80),  nullable=False)
    vm_name    = db.Column(db.String(200), nullable=False, default='')
    project    = db.Column(db.String(80),  nullable=False, default='')
    ip_address = db.Column(db.String(40),  nullable=False, default='')
    created_at = db.Column(db.DateTime,    default=datetime.utcnow)

class SCodeSnapshot(db.Model):
    __tablename__ = 'scode_snapshots'
    id         = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    date       = db.Column(db.String(10), nullable=False)
    scode      = db.Column(db.String(80), nullable=False)
    count      = db.Column(db.Integer,    nullable=False, default=0)
    created_at = db.Column(db.DateTime,   default=datetime.utcnow)


class UserActivity(db.Model):
    """Tracks recently visited pages per user — powers the home dashboard."""
    __tablename__ = 'user_activity'
    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False, index=True)
    page_key      = db.Column(db.String(80),  nullable=False)
    page_label    = db.Column(db.String(120), nullable=False)
    page_url      = db.Column(db.String(255), nullable=False)
    page_category = db.Column(db.String(80),  nullable=True)   # e.g. 'Compute', 'IAM'
    visited_at    = db.Column(db.DateTime, default=datetime.utcnow)


class UserFavorite(db.Model):
    """User-pinned quick links — powers the 즐겨찾기 tab on the home dashboard."""
    __tablename__ = 'user_favorites'
    id            = db.Column(db.Integer,     primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False, index=True)
    page_key      = db.Column(db.String(80),  nullable=False)
    page_label    = db.Column(db.String(120), nullable=False)
    page_url      = db.Column(db.String(255), nullable=False)
    page_category = db.Column(db.String(80),  nullable=True)
    page_desc     = db.Column(db.String(200), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


def _init_db(app):
    """Create tables and seed admin user from env if DB is empty."""
    db.create_all()

    migrations = [
        """CREATE TABLE IF NOT EXISTS scode_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            zone VARCHAR(80) NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            match_type VARCHAR(20) NOT NULL DEFAULT 'contains',
            match_value VARCHAR(200) NOT NULL,
            scode VARCHAR(80) NOT NULL,
            is_default BOOLEAN DEFAULT 0,
            created_at DATETIME,
            created_by VARCHAR(80)
        )""",
        "ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'viewer'",
        "ALTER TABLE projects ADD COLUMN alias VARCHAR(120)",
        """CREATE TABLE IF NOT EXISTS scode_info (
            scode VARCHAR(80) PRIMARY KEY,
            engineer VARCHAR(120),
            developer VARCHAR(120),
            manager VARCHAR(120),
            updated_at DATETIME,
            updated_by VARCHAR(80)
        )""",
        """CREATE TABLE IF NOT EXISTS scode_vm_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date VARCHAR(10) NOT NULL,
            scode VARCHAR(80) NOT NULL,
            vm_id VARCHAR(80) NOT NULL,
            vm_name VARCHAR(200) NOT NULL DEFAULT '',
            project VARCHAR(80) NOT NULL DEFAULT '',
            ip_address VARCHAR(40) NOT NULL DEFAULT '',
            created_at DATETIME
        )""",
        """CREATE TABLE IF NOT EXISTS scode_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date VARCHAR(10) NOT NULL,
            scode VARCHAR(80) NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME
        )""",
        "ALTER TABLE users ADD COLUMN status VARCHAR(20) DEFAULT 'active'",
        "ALTER TABLE users ADD COLUMN email VARCHAR(120)",
        "ALTER TABLE users ADD COLUMN role_reason TEXT",
        """CREATE TABLE IF NOT EXISTS deleted_projects (
            zone TEXT PRIMARY KEY,
            deleted_at DATETIME,
            deleted_by TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS seen_notifications (
            username TEXT NOT NULL,
            notif_key TEXT NOT NULL,
            seen_at DATETIME,
            PRIMARY KEY (username, notif_key)
        )""",
        """CREATE TABLE IF NOT EXISTS seen_kc_notices (
            username TEXT NOT NULL,
            notice_id INTEGER NOT NULL,
            seen_at DATETIME,
            PRIMARY KEY (username, notice_id)
        )""",
        """CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vm_name TEXT NOT NULL,
            project_key TEXT,
            days_left INTEGER NOT NULL,
            checked_at DATETIME,
            resolved INTEGER DEFAULT 0,
            resolved_at DATETIME
        )""",
        """CREATE TABLE IF NOT EXISTS app_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            detail TEXT,
            username TEXT,
            created_at DATETIME
        )""",
        """CREATE TABLE IF NOT EXISTS app_changelog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            title TEXT NOT NULL,
            change_type TEXT NOT NULL,
            description TEXT NOT NULL,
            image_url TEXT,
            icon TEXT,
            created_at DATETIME,
            created_by TEXT
        )""",
        "ALTER TABLE app_changelog ADD COLUMN image_url TEXT",
        "ALTER TABLE app_changelog ADD COLUMN icon TEXT",
        "ALTER TABLE user_activity ADD COLUMN page_category VARCHAR(80)",
        "ALTER TABLE user_favorites ADD COLUMN page_desc VARCHAR(200)",
    ]
    for sql in migrations:
        try:
            db.session.execute(db.text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()

    for u in DBUser.query.filter_by(is_admin=True).all():
        if u.role != 'admin':    u.role   = 'admin'
        if u.status != 'active': u.status = 'active'
    db.session.commit()

    if DBUser.query.count() == 0:
        admin_user = get_env_var('ADMIN_USERNAME', required=False) or 'admin'
        admin_pass = get_env_var('ADMIN_PASSWORD', required=False)
        if admin_pass:
            db.session.add(DBUser(
                username             = admin_user,
                password             = generate_password_hash(admin_pass),
                must_change_password = False,
                is_admin             = True,
                role                 = 'admin',
                status               = 'active',
            ))
            db.session.commit()
            app.logger.info(f'Seeded initial admin user: {admin_user}')
        else:
            app.logger.warning('DB is empty and ADMIN_PASSWORD not set.')

    from helpers import _seed_projects_from_tokens
    if Project.query.count() == 0:
        _seed_projects_from_tokens()