import json
import os


class JsonConfig:
    """Generic JSON config file load/save with attribute-style access."""

    def __init__(self, path: str, defaults: dict = None):
        self.path = os.path.expanduser(path)
        self._data = dict(defaults or {})
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self._data.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self._data, f, indent=2)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    def update(self, mapping: dict):
        self._data.update(mapping)
        self.save()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        self.save()

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f"JsonConfig({self.path}, data={self._data})"
