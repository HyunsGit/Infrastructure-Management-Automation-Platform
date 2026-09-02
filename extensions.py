# extensions.py — shared Flask extensions (no app object here)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db           = SQLAlchemy()
csrf         = CSRFProtect()
login_manager = LoginManager()
limiter      = Limiter(key_func=get_remote_address, default_limits=[],
                       storage_uri="redis://localhost:6379/0")