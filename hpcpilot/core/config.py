import json
import os
from typing import Any


class JsonConfig:
    """Generic JSON config file load/save with attribute-style access.

    Secrets are stored separately in credentials.json with chmod 0600.
    """

    def __init__(self, path: str, secrets_path: str | None = None, defaults: dict | None = None):
        self.path = os.path.expanduser(path)
        self.secrets_path: str
        if secrets_path is None:
            if self.path.endswith("config.json"):
                self.secrets_path = self.path.replace("config.json", "credentials.json")
            else:
                self.secrets_path = self.path + "_credentials"
        else:
            self.secrets_path = os.path.expanduser(secrets_path)

        self._data = dict(defaults or {})
        self._secrets: dict[str, Any] = {}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self._data.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        try:
            with open(self.secrets_path) as f:
                self._secrets.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        self._save_file(self.path, self._data, mode=0o700, file_mode=0o644)
        if self._secrets:
            self._save_file(self.secrets_path, self._secrets, mode=0o700, file_mode=0o600)

    def _save_file(self, filepath, data, mode=0o700, file_mode=0o600):
        dirname = os.path.dirname(filepath)
        if dirname and dirname != ".":
            if not os.path.exists(dirname):
                os.makedirs(dirname, mode=mode, exist_ok=True)
            try:
                os.chmod(dirname, mode)
            except OSError:
                pass

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        try:
            fd = os.open(filepath, flags, file_mode)
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
        except OSError:
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
            try:
                os.chmod(filepath, file_mode)
            except OSError:
                pass

    def get(self, key, default=None):
        if key == "api_key":
            return self._secrets.get(key, default)
        return self._data.get(key, default)

    def set(self, key, value):
        if key == "api_key":
            self._secrets[key] = value
        else:
            self._data[key] = value
        self.save()

    def update(self, mapping: dict):
        for k, v in mapping.items():
            if k == "api_key":
                self._secrets[k] = v
            else:
                self._data[k] = v
        self.save()

    def __getitem__(self, key):
        if key == "api_key":
            return self._secrets[key]
        return self._data[key]

    def __setitem__(self, key, value):
        if key == "api_key":
            self._secrets[key] = value
        else:
            self._data[key] = value
        self.save()

    def __contains__(self, key):
        if key == "api_key":
            return key in self._secrets
        return key in self._data

    def __repr__(self):
        return f"JsonConfig({self.path}, secrets={self.secrets_path}, data={self._data})"
