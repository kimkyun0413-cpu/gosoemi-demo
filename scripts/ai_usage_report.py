"""Operator-only usage report / retention task. Reads credentials from the server environment only.

Usage: python scripts/ai_usage_report.py          # aggregate report (no account ids/content)
       python scripts/ai_usage_report.py --prune  # delete usage metadata older than 30 days
Never run until the AI usage migration and environment have been validated.
"""
import argparse
import json
import os
import sys
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prune', action='store_true')
    args = parser.parse_args()
    if os.environ.get('AI_USAGE_DB_ENABLED') != 'true':
        sys.exit('Usage DB is not enabled; no request sent.')
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('AI_USAGE_SERVICE_KEY', '')
    if not url.startswith('https://') or not key:
        sys.exit('Usage server settings are missing; no request sent.')
    operation = 'prune_ai_usage' if args.prune else 'ai_usage_report'
    try:
        response = httpx.post(url + '/rest/v1/rpc/' + operation, json={}, timeout=30,
                             headers={'apikey': key, 'Authorization': 'Bearer ' + key})
        response.raise_for_status()
        data = response.json()
    except Exception:
        # Error bodies can contain account settings. Do not print response bodies or credentials.
        sys.exit('Usage request failed; check server health and RPC permissions.')
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
