#!/usr/bin/env python3
"""Dump what a llama.cpp server actually exposes for context detection.

Usage: python3 dump_ctx.py [base_url]
Default: http://localhost:8080

Shows the raw /v1/models and /props responses so we can see exactly
what fields are available for context window detection.
"""
import json
import sys
import urllib.request

def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"_error": str(e)}

def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")

    print(f"=== Base: {base} ===\n")

    # /v1/models
    print("--- GET /v1/models ---")
    models_data = fetch(f"{base}/v1/models")
    if "_error" in models_data:
        print(f"ERROR: {models_data['_error']}")
    else:
        models = models_data.get("data", [])
        print(f"Found {len(models)} model(s)")
        for m in models:
            print(f"\n  id: {m.get('id')}")
            # Dump all top-level keys
            for k, v in m.items():
                if k == "id":
                    continue
                if isinstance(v, dict):
                    print(f"  {k}:")
                    for kk, vv in v.items():
                        print(f"    {kk}: {vv}")
                else:
                    print(f"  {k}: {v}")

    print("\n--- GET /props ---")
    props = fetch(f"{base}/props")
    if "_error" in props:
        print(f"ERROR: {props['_error']}")
    else:
        # Dump all keys, highlighting n_ctx related ones
        for k, v in props.items():
            marker = " <== CTX?" if "ctx" in k.lower() or "n_ctx" in k.lower() else ""
            if isinstance(v, dict):
                print(f"{k}:{marker}")
                for kk, vv in v.items():
                    m2 = " <== CTX?" if "ctx" in kk.lower() else ""
                    print(f"  {kk}: {vv}{m2}")
            else:
                print(f"{k}: {v}{marker}")

if __name__ == "__main__":
    main()
