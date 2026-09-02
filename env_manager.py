#!/usr/bin/python3

import os
from pathlib import Path
from dotenv import load_dotenv

class EnvReloader:
    def __init__(self, dotenv_paths):
        self.dotenv_paths = [Path(p) for p in dotenv_paths]
        self.last_mtimes = {}
        self._load()

    def _load(self):
        for path in self.dotenv_paths:
            load_dotenv(dotenv_path=path, override=True)
            self.last_mtimes[path] = path.stat().st_mtime

    def refresh_if_needed(self):
        for path in self.dotenv_paths:
            try:
                current_mtime = path.stat().st_mtime
                if current_mtime != self.last_mtimes.get(path):
                    self._load()
                    break
            except FileNotFoundError:
                pass

    def getenv(self, key, default=None, required=False):
        self.refresh_if_needed()
        val = os.getenv(key)
        if required and val is None:
            raise EnvironmentError(f"Required environment variable '{key}' not found")
        return val if val is not None else default


env_reloader = EnvReloader(['/etc/mgt-api/.project_id', '/etc/mgt-api/.tokens'])

def get_env_var(key, required=False, default=None):
    return env_reloader.getenv(key, default=default, required=required)
