#!/usr/bin/env python3
"""
GitHub orphan commit brute-forcer with multi-token rotation.
Usage:
    python cfor_rotated.py OWNER REPO tokens.txt [--threads 2] [--batch 50] [--delay 0.5]
"""

import requests
import time
import os
import sys
import argparse
from itertools import product
from queue import Queue
from threading import Thread, Lock
import urllib3

urllib3.disable_warnings()

# ------------------------------
# Token rotation pool
# ------------------------------
class TokenPool:
    def __init__(self, tokens):
        self._tokens = tokens
        self._lock = Lock()
        self._suspended_until = [0.0] * len(tokens)
        self._current = 0

    def get_token(self):
        """Return a token (str) and its index, or (None, wait_time) if all are suspended."""
        with self._lock:
            now = time.time()
            # Try to find a free token
            for _ in range(len(self._tokens)):
                idx = self._current % len(self._tokens)
                self._current = (self._current + 1) % len(self._tokens)
                if self._suspended_until[idx] <= now:
                    return self._tokens[idx], idx
            # All suspended – calculate shortest wait
            min_wait = min(self._suspended_until) - now
            return None, max(0, min_wait)

    def suspend_token(self, idx, duration):
        with self._lock:
            self._suspended_until[idx] = time.time() + duration

# ------------------------------
# GraphQL query builder
# ------------------------------
def make_graphql_query(prefixes, owner, name):
    """Build a single GraphQL query to check multiple commit SHA prefixes."""
    query = f'query {{ repository(owner:"{owner}", name:"{name}") {{ '
    for i, p in enumerate(prefixes):
        query += f'a{i}: object(expression:"{p}") {{ ... on Commit {{ oid }} }} '
    query += '} }'
    return query

# ------------------------------
# GraphQL request with token rotation
# ------------------------------
def check_prefixes(prefixes, owner, name, token_pool, proxy=None):
    """Send a GraphQL query, handle rate limits by rotating tokens."""
    query = make_graphql_query(prefixes, owner, name)
    base_sleep = 1.0  # initial backoff for non-rate-limit errors

    while True:
        token, token_idx = token_pool.get_token()
        if token is None:
            _, wait = token_pool.get_token()
            print(f"All tokens rate-limited, waiting {wait:.0f}s ...")
            time.sleep(wait)
            continue

        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.post('https://api.github.com/graphql',
                                 json={"query": query},
                                 headers=headers,
                                 proxies=proxy,
                                 verify=False,
                                 timeout=30)
        except Exception as e:
            print(f"Network error: {e}, retrying in {base_sleep}s")
            time.sleep(base_sleep)
            base_sleep = min(base_sleep * 2, 60)
            continue

        # Primary rate limit
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get('Retry-After')
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 60
            print(f"Token {token[:8]}... primary limit, suspended {wait}s")
            token_pool.suspend_token(token_idx, wait)
            continue

        # JSON content
        if resp.headers.get('Content-Type', '').startswith('application/json'):
            data = resp.json()

            # Secondary rate limit
            if 'message' in data and 'secondary rate limit' in data['message'].lower():
                retry_after = resp.headers.get('Retry-After')
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 60
                print(f"Token {token[:8]}... secondary limit, suspended {wait}s")
                token_pool.suspend_token(token_idx, wait)
                continue

            # Successful data
            if 'data' in data and data['data'] and data['data']['repository']:
                found = []
                for v in data['data']['repository'].values():
                    if v and 'oid' in v:
                        found.append(v['oid'])
                return found

            # Error handling (e.g. parse errors, "Something went wrong")
            msg = ''
            if 'errors' in data:
                msg = data['errors'][0].get('message', '')
                if 'parse error' in msg.lower():
                    print("Parse error, returning empty.")
                    return []
                print(f"GraphQL error: {msg}, retrying in {base_sleep}s")
            elif 'message' in data:
                print(f"API message: {data['message']}, retrying in {base_sleep}s")
            else:
                print(f"Unexpected JSON: {data}, retrying in {base_sleep}s")
            time.sleep(base_sleep)
            base_sleep = min(base_sleep * 2, 60)
            continue

        # Non-JSON response (e.g. HTML error page)
        print(f"Non-JSON response (status {resp.status_code}), retrying in {base_sleep}s")
        time.sleep(base_sleep)
        base_sleep = min(base_sleep * 2, 60)

# ------------------------------
# Fetch known commits (REST API) – uses the first token only
# ------------------------------
def get_known_commits(owner, name, token, proxy=None):
    """Return a set of all commit SHAs reachable from the repo's default branch."""
    known = set()
    print("Fetching known commits...")
    url = f'https://api.github.com/repos/{owner}/{name}/commits?per_page=100'
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        try:
            resp = requests.get(url, headers=headers, proxies=proxy, verify=False)
            if resp.status_code != 200:
                print(f"Error fetching commits: {resp.status_code}, stopping.")
                break
            commits = resp.json()
            if not commits:
                break
            for c in commits:
                known.add(c['sha'])
            # Pagination
            if 'link' in resp.headers and 'rel="next"' in resp.headers['link']:
                link = resp.headers['link']
                next_url = link.split('>; rel="next"')[0].split('<')[-1]
                url = next_url + "&per_page=100"
            else:
                break
        except Exception as e:
            print(f"Exception fetching commits: {e}, retrying in 5s")
            time.sleep(5)
    print(f"Found {len(known)} known commits.")
    return known

# ------------------------------
# Prefix generation
# ------------------------------
def generate_prefixes(known_commits, batch_size):
    """
    Yields batches of commit SHA prefixes (initially 4-char, extended to 5
    when they collide with a known commit).
    """
    chars = "0123456789abcdef"
    batch = []
    for prefix in product(chars, repeat=4):
        prefix = ''.join(prefix)
        collision = any(k.startswith(prefix) for k in known_commits)
        if not collision:
            batch.append(prefix)
        else:
            # Extend to 5 chars to skip the known commit
            for c in chars:
                new_prefix = prefix + c
                if not any(k.startswith(new_prefix) for k in known_commits):
                    batch.append(new_prefix)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch

# ------------------------------
# Worker threads for GraphQL brute-force
# ------------------------------
def graphql_worker(prefix_queue, found_queue, owner, name, token_pool, proxy, request_delay):
    while True:
        try:
            prefixes = prefix_queue.get_nowait()
        except:
            return
        time.sleep(request_delay)   # gentle spacing between requests
        found = check_prefixes(prefixes, owner, name, token_pool, proxy)
        for sha in found:
            found_queue.put(sha)
            print(f"Found hidden commit: {sha}")
        prefix_queue.task_done()

# ------------------------------
# Download worker
# ------------------------------
def download_worker(found_queue, owner, name, output_dir, proxy):
    while True:
        try:
            sha = found_queue.get_nowait()
        except:
            return
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        for suffix in ['.diff', '.patch']:
            url = f"https://github.com/{owner}/{name}/commit/{sha}{suffix}"
            try:
                resp = requests.get(url, proxies=proxy, verify=False, timeout=30)
                if resp.status_code == 200:
                    with open(f"{output_dir}/{sha}{suffix}", 'wb') as f:
                        f.write(resp.content)
                    print(f"Downloaded {sha}{suffix}")
                    break   # success, no need for the other suffix
                elif resp.status_code == 404:
                    continue
                else:
                    print(f"Status {resp.status_code} for {url}")
            except Exception as e:
                print(f"Download error for {sha}: {e}")
        found_queue.task_done()

# ------------------------------
# Main
# ------------------------------
def main():
    parser = argparse.ArgumentParser(description="Find and download orphan GitHub commits.")
    parser.add_argument("owner", help="Repository owner (user or org)")
    parser.add_argument("repo", help="Repository name")
    parser.add_argument("token_file", help="File containing one GitHub token per line")
    parser.add_argument("--threads", type=int, default=2, help="Number of concurrent requests (default: 2)")
    parser.add_argument("--batch", type=int, default=50, help="Prefixes per GraphQL query (default: 50)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between GraphQL requests in seconds (default: 0.5)")
    parser.add_argument("--proxy", help="HTTPS proxy (e.g. http://user:pass@host:port)")
    args = parser.parse_args()

    # Load tokens
    if not os.path.exists(args.token_file):
        print(f"Token file {args.token_file} not found.")
        sys.exit(1)
    with open(args.token_file) as f:
        tokens = [line.strip() for line in f if line.strip()]
    if not tokens:
        print("No tokens found in file.")
        sys.exit(1)
    print(f"Loaded {len(tokens)} token(s).")

    proxy = {"https": args.proxy} if args.proxy else {}

    # Fetch known commits (use first token; if it rate-limits, script will exit – but unlikely)
    known = get_known_commits(args.owner, args.repo, tokens[0], proxy)
    if not known:
        print("Warning: could not fetch known commits. Proceeding blind (will take longer).")

    # Build queue of prefix batches
    prefix_queue = Queue()
    total_batches = 0
    for batch in generate_prefixes(known, args.batch):
        prefix_queue.put(batch)
        total_batches += 1
    print(f"Generated {total_batches} GraphQL queries to test.")

    found_queue = Queue()
    token_pool = TokenPool(tokens)

    # Start GraphQL workers
    print(f"Starting {args.threads} GraphQL worker(s)...")
    for _ in range(args.threads):
        t = Thread(target=graphql_worker,
                   args=(prefix_queue, found_queue, args.owner, args.repo,
                         token_pool, proxy, args.delay))
        t.daemon = True
        t.start()

    # Wait for prefix queue to be processed
    prefix_queue.join()
    print("GraphQL phase complete.")

    # Output directory
    output_dir = f"output/{args.owner}_{args.repo}"
    os.makedirs(output_dir, exist_ok=True)

    # Start download workers (use same number of threads)
    print(f"Downloading {found_queue.qsize()} commits...")
    for _ in range(args.threads):
        t = Thread(target=download_worker,
                   args=(found_queue, args.owner, args.repo, output_dir, proxy))
        t.daemon = True
        t.start()

    found_queue.join()
    print("All done. Check", output_dir)

if __name__ == "__main__":
    main()
