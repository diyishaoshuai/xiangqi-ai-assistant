"""Bounded local resource preferences; no global power/driver settings changed."""
import json
import logging
import os
from app_paths import user_data_dir


def resource_settings():
    logical = os.cpu_count() or 4
    values = {}
    try:
        values = json.loads((user_data_dir() / 'performance.json').read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            values = {}
    except (OSError, ValueError):
        pass

    def bounded(name, default, low, high):
        try:
            return max(low, min(high, int(values.get(name, default))))
        except (ValueError, TypeError, OverflowError):
            return default

    settings = dict(threads=bounded('threads', min(12, max(1, logical-2)), 1, max(1, logical-2)),
                    hash_mb=bounded('hash_mb', 256, 64, 2048),
                    vision_device=bounded('vision_device', -1, -1, 7))
    logging.getLogger('xiangqi_ai.performance').info('resource configuration %s', settings)
    return settings
