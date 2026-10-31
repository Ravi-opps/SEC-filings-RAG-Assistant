import argparse
import json
import os
import sys
import time
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

API_VERSION = "v1"
DEFAULT_PROMPT = (
    "My name is Ravi. I am testing the SEC RAG assistant. "
    "Remember that I prefer concise, source-backed answers. "
    "Reply with one short confirmation."
)
DEFAULT_SCOPE = "secrag-demo"


def require_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def redact(text, secret):
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return text[:600]


def request_error(response, secret):
    try:
        body = response.json()
    except ValueError:
        body = redact(response.text, secret)
    return f"HTTP {response.status_code}: {redact(json.dumps(body), secret)}"


def call_llm(prompt):
    base_url = require_env("OPENAI_ENDPT").rstrip("/")
    api_key = require_env("OPENAI_LUNA")
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json={
            "model": "gpt-6-luna",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Keep replies concise.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": 200,
        },
        timeout=90.0,
    )
    if response.status_code != 200:
        raise RuntimeError(request_error(response, api_key))
    return response.json()["choices"][0]["message"]["content"].strip()


def memory_headers():
    headers = {
        "Foundry-Features": "MemoryStores=V1Preview",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    token = os.getenv("AZURE_ACCESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["api-key"] = require_env("AGENT_MEM_KEY")
    return headers


def auth_secret(headers):
    token = headers.get("Authorization", "")
    if token.startswith("Bearer "):
        return token.removeprefix("Bearer ").strip()
    return headers.get("api-key", "")


def resolve_store(client, base_url, headers, requested_store):
    if requested_store:
        return requested_store

    response = client.get(
        f"{base_url}/memory_stores",
        params={"api-version": API_VERSION, "limit": 100},
        headers=headers,
    )
    if response.status_code != 200:
        raise RuntimeError(request_error(response, auth_secret(headers)))

    names = [
        item.get("name")
        for item in response.json().get("data", [])
        if isinstance(item, dict) and item.get("name")
    ]
    if len(names) == 1:
        return names[0]
    if not names:
        raise RuntimeError("No memory stores found in this project.")
    raise RuntimeError(
        "Multiple memory stores found; pass --store. Available: " + ", ".join(names)
    )


def update_memories(client, base_url, headers, store, scope, messages):
    store_path = quote(store, safe="")
    response = client.post(
        f"{base_url}/memory_stores/{store_path}:update_memories",
        params={"api-version": API_VERSION},
        headers=headers,
        json={"scope": scope, "items": messages, "update_delay": 0},
    )
    if response.status_code != 202:
        raise RuntimeError(request_error(response, auth_secret(headers)))

    body = response.json()
    update_id = body.get("update_id")
    if not update_id:
        raise RuntimeError("Memory update did not return an update_id.")

    operation_url = response.headers.get("Operation-Location")
    if not operation_url:
        operation_url = (
            f"{base_url}/memory_stores/{store_path}/updates/"
            f"{quote(update_id, safe='')}?api-version={API_VERSION}"
        )
    return update_id, operation_url


def wait_for_update(client, operation_url, headers, timeout):
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        response = client.get(operation_url, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(request_error(response, auth_secret(headers)))

        body = response.json()
        last_status = body.get("status")
        print(f"  memory update status: {last_status}")

        if last_status == "completed":
            return body
        if last_status == "failed":
            raise RuntimeError(
                "Memory update failed: "
                + redact(json.dumps(body.get("error") or body), auth_secret(headers))
            )
        if last_status == "superseded":
            raise RuntimeError("Memory update was superseded by another update.")
        time.sleep(3)

    raise TimeoutError(f"Memory update did not finish within {timeout}s ({last_status}).")


def search_memories(client, base_url, headers, store, scope, query, limit=5):
    store_path = quote(store, safe="")
    response = client.post(
        f"{base_url}/memory_stores/{store_path}:search_memories",
        params={"api-version": API_VERSION},
        headers=headers,
        json={
            "scope": scope,
            "items": [{"type": "message", "role": "user", "content": query}],
            "options": {"max_memories": limit},
        },
    )
    if response.status_code != 200:
        raise RuntimeError(request_error(response, auth_secret(headers)))
    return response.json()


def memory_text(memory):
    item = memory.get("memory_item", memory)
    content = item.get("content") or item.get("summary") or item.get("text")
    if isinstance(content, str):
        return content
    return json.dumps(item)[:500]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--scope", default=os.getenv("AGENT_MEMORY_SCOPE", DEFAULT_SCOPE))
    parser.add_argument("--store", default=os.getenv("AGENT_MEMORY_STORE", ""))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    base_url = require_env("AGENT_MEMORY_URL").rstrip("/")
    headers = memory_headers()

    print("[1/4] Calling LLM")
    reply = call_llm(args.prompt)
    print(f"  user: {args.prompt}")
    print(f"  assistant: {reply}")

    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        store = resolve_store(client, base_url, headers, args.store)
        print(f"\n[2/4] Recording conversation in memory store: {store}")
        print(f"  scope: {args.scope}")

        items = [
            {"type": "message", "role": "user", "content": args.prompt},
            {"type": "message", "role": "assistant", "content": reply},
        ]
        update_id, operation_url = update_memories(
            client, base_url, headers, store, args.scope, items
        )
        print(f"  update_id: {update_id}")

        print("\n[3/4] Waiting for memory extraction")
        result = wait_for_update(client, operation_url, headers, args.timeout)
        operations = result.get("memory_operations") or []
        print(f"  memory_operations: {len(operations)}")
        for operation in operations:
            print(f"  - {memory_text(operation)}")

        print("\n[4/4] Verifying with a memory search")
        search = search_memories(
            client,
            base_url,
            headers,
            store,
            args.scope,
            "What is the user's name and what answer style do they prefer?",
        )
        memories = search.get("memories") or []
        print(f"  memories_found: {len(memories)}")
        for memory in memories:
            print(f"  - {memory_text(memory)}")

    print("\n=== SUMMARY ===")
    print(f"llm_call=ok memory_store={store} scope={args.scope} update=completed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[FAIL] {type(exc).__name__}: {exc}")
        if "Authentication to the Azure OpenAI resource failed" in str(exc):
            print(
                "Hint: use an Entra ID token with Foundry User and "
                "Cognitive Services OpenAI User roles."
            )
        sys.exit(1)
