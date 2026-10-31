import json
import os
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

load_dotenv()

TIMEOUT = 20.0
URL_VAR = "AGENT_MEMORY_URL"
KEY_VAR = "AGENT_MEM_KEY"
AGENT_VAR = "AGENT_NAME"
API_VERSIONS = ("v1", "2025-11-15-preview")

BASE_HEADERS = {
    "Accept": "application/json",
    "Foundry-Features": "MemoryStores=V1Preview",
}


def display_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def redact(text, secret):
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:400]


def summarize(data):
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            names = [
                item.get("name")
                for item in data["data"]
                if isinstance(item, dict) and item.get("name")
            ]
            suffix = f" names={','.join(names)}" if names else ""
            return f"memory_stores={len(data['data'])}{suffix}"
        if isinstance(data.get("value"), list):
            return f"values={len(data['value'])}"
        error = data.get("error")
        if isinstance(error, dict):
            return f"error={error.get('code')} message={error.get('message')}"
    return json.dumps(data)[:300]


def probe(client, url, headers, label, key):
    start = time.perf_counter()
    try:
        resp = client.get(url, headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  [FAIL] {label}: {elapsed:.0f}ms {type(exc).__name__}: {exc}")
        return None

    elapsed = (time.perf_counter() - start) * 1000
    try:
        body = resp.json()
        detail = summarize(body)
    except ValueError:
        detail = redact(resp.text, key)

    print(f"  [{resp.status_code}] {label}: {elapsed:.0f}ms {detail}")
    return resp.status_code


def main():
    base_url = os.getenv(URL_VAR, "").strip()
    api_key = os.getenv(KEY_VAR, "").strip()
    agent_name = os.getenv(AGENT_VAR, "").strip()

    print(f"agent={agent_name or '<unset>'}")
    if base_url:
        print(f"endpoint={display_url(base_url)}")

    missing = [name for name, value in ((URL_VAR, base_url), (KEY_VAR, api_key)) if not value]
    if missing:
        print(f"[FAIL] missing env vars: {', '.join(missing)}")
        return 1

    base_url = base_url.rstrip("/")
    auth_candidates = [
        ("api-key", {**BASE_HEADERS, "api-key": api_key}),
        ("bearer-key", {**BASE_HEADERS, "Authorization": f"Bearer {api_key}"}),
    ]

    with httpx.Client(follow_redirects=True) as client:
        for api_version in API_VERSIONS:
            url = f"{base_url}/memory_stores?api-version={api_version}&limit=1"
            print(f"\nGET {display_url(url)}")
            for label, headers in auth_candidates:
                status = probe(client, url, headers, label, api_key)
                if status == 200:
                    print("\n=== SUMMARY ===")
                    print(f"connected=True api_version={api_version} auth={label}")
                    return 0
                if status == 403:
                    print("\n=== SUMMARY ===")
                    print("connected=True but authorization failed (403).")
                    print("The identity needs the Foundry User role on this project.")
                    return 1

    print("\n=== SUMMARY ===")
    print("connected=False")
    print("The memory store API did not accept these credentials.")
    print("Foundry memory stores require Microsoft Entra ID (Bearer token);")
    print("an API key may work for other Foundry endpoints but not this one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
