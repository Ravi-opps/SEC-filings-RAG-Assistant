import json
import os
import sys
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

TIMEOUT = 60.0

CHAT_ENDPOINTS = [
    {
        "label": "LUNA",
        "model": "gpt-6-luna",
        "key_var": "OPENAI_LUNA",
        "url_var": "OPENAI_ENDPT",
        "path": "/chat/completions",
        "kind": "chat",
    },
    {
        "label": "SOL",
        "model": "gpt-6-sol",
        "key_var": "OPENAI_SOL",
        "url_var": "SOL_URL",
        "path": "",
        "kind": "responses",
    },
]

EMBED_ENDPOINTS = [
    {
        "label": "EMBEDDINGS",
        "model": "text-embedding-3-large",
        "key_var": "OPENAI_EMBEDDINGS",
        "url_var": "EMBED_URL",
        "path": "/openai/v1/embeddings",
        "kind": "embeddings",
    },
]


def request(endpoint, body):
    url = endpoint["base_url"].rstrip("/") + endpoint["path"]
    headers = {
        "api-key": endpoint["api_key"],
        "Content-Type": "application/json",
    }
    start = time.perf_counter()
    resp = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT)
    elapsed = time.perf_counter() - start
    return resp, elapsed


def extract_text(resp):
    data = resp.json()
    if "data" in data and data["data"] and "embedding" in data["data"][0]:
        vectors = data["data"]
        return f"vectors={len(vectors)} dims={len(vectors[0]['embedding'])}"
    if "choices" in data:
        return repr(data["choices"][0]["message"]["content"])
    if "output_text" in data:
        return repr(data["output_text"])
    if "output" in data:
        parts = []
        for item in data["output"]:
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        return repr(" ".join(parts))
    return json.dumps(data)[:200]


def test_chat(endpoint):
    body = {
        "model": endpoint["model"],
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_completion_tokens": 16,
    }
    resp, elapsed = request(endpoint, body)
    status = "OK" if resp.status_code == 200 else "FAIL"
    detail = extract_text(resp) if resp.status_code == 200 else resp.text[:200]
    print(f"  [{status}] {elapsed * 1000:.0f}ms {detail}")
    return resp.status_code == 200


def test_responses(endpoint):
    body = {"model": endpoint["model"], "input": "Reply with the single word: ok"}
    resp, elapsed = request(endpoint, body)
    status = "OK" if resp.status_code == 200 else "FAIL"
    detail = extract_text(resp) if resp.status_code == 200 else resp.text[:200]
    print(f"  [{status}] {elapsed * 1000:.0f}ms {detail}")
    return resp.status_code == 200


def test_embeddings(endpoint):
    body = {"model": endpoint["model"], "input": "health check"}
    resp, elapsed = request(endpoint, body)
    status = "OK" if resp.status_code == 200 else "FAIL"
    detail = extract_text(resp) if resp.status_code == 200 else resp.text[:200]
    print(f"  [{status}] {elapsed * 1000:.0f}ms {detail}")
    return resp.status_code == 200


def run(endpoint):
    label = endpoint["label"]
    model = endpoint["model"]
    base_url = endpoint["base_url"]
    print(f"\n[{label}] model={model} url={base_url}{endpoint['path']}")

    if not endpoint["api_key"] or not base_url:
        print("  [SKIP] key or url not set")
        return False

    kind = endpoint["kind"]
    try:
        if kind == "chat":
            return test_chat(endpoint)
        if kind == "responses":
            return test_responses(endpoint)
        return test_embeddings(endpoint)
    except Exception as exc:
        print(f"  [FAIL] {type(exc).__name__}: {exc}")
        return False


def main():
    results = []

    for spec in CHAT_ENDPOINTS + EMBED_ENDPOINTS:
        endpoint = {
            **spec,
            "api_key": os.getenv(spec["key_var"], ""),
            "base_url": os.getenv(spec["url_var"], ""),
        }
        results.append(run(endpoint))

    print("\n=== SUMMARY ===")
    print(f"passed={sum(results)}/{len(results)}")

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
