import os
import json
import time
import requests

_UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
_UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

_mem = {}  # fallback for local dev: key -> (exp_ts, json_str)

def _mem_get(key: str):
    item = _mem.get(key)
    if not item:
        return None
    exp, val = item
    if exp is not None and time.time() > exp:
        _mem.pop(key, None)
        return None
    return val

def _mem_set(key: str, val: str, ttl: int | None):
    exp = (time.time() + ttl) if ttl else None
    _mem[key] = (exp, val)

def get_json(key: str):
    if _UPSTASH_URL and _UPSTASH_TOKEN:
        r = requests.get(
            f"{_UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        payload = r.json()
        val = payload.get("result")
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    val = _mem_get(key)
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None

def set_json(key: str, obj, ttl_seconds: int | None = None):
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    if _UPSTASH_URL and _UPSTASH_TOKEN:
        if ttl_seconds:
            url = f"{_UPSTASH_URL}/set/{key}/{s}?EX={int(ttl_seconds)}"
        else:
            url = f"{_UPSTASH_URL}/set/{key}/{s}"
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {_UPSTASH_TOKEN}"},
            timeout=10,
        )
        return r.status_code == 200

    _mem_set(key, s, ttl_seconds)
    return True
