import os
import sys
import json
import time
import argparse

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_FILE = os.path.join(BRIDGE_DIR, "telemetry_inbox.json")

def main():
    parser = argparse.ArgumentParser(description="Antigravity Wake-Up Terminal Hook")
    parser.add_argument("--timeout", type=float, default=120.0, help="Max wait seconds (default: 120s)")
    parser.add_argument("--initial-job-id", type=str, default=None, help="Previous job_id to ignore")
    parser.add_argument("--any", action="store_true", help="Accept existing inbox immediately if present")
    args = parser.parse_args()

    start_time = time.time()
    start_mtime = 0.0
    start_job_id = args.initial_job_id

    if os.path.exists(INBOX_FILE) and not args.any:
        try:
            start_mtime = os.path.getmtime(INBOX_FILE)
            if not start_job_id:
                with open(INBOX_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
                        start_job_id = data.get("job_id")
        except Exception:
            pass

    # Loop waiting for a new telemetry packet
    while (time.time() - start_time) < args.timeout:
        if os.path.exists(INBOX_FILE):
            try:
                curr_mtime = os.path.getmtime(INBOX_FILE)
                # Check if file is newly written or we are accepting --any
                if args.any or curr_mtime > start_mtime or (start_mtime == 0.0):
                    with open(INBOX_FILE, "r", encoding="utf-8") as f:
                        raw = f.read().strip()
                    if raw:
                        data = json.loads(raw)
                        new_job_id = data.get("job_id")
                        if new_job_id and (args.any or new_job_id != start_job_id):
                            print("=== NEW TELEMETRY INBOX READY ===")
                            print(json.dumps(data, indent=2))
                            sys.stdout.flush()
                            sys.exit(0)
            except Exception:
                # File may be mid-write by FastAPI, retry on next tick
                pass

        time.sleep(0.3)

    print(f"ERROR: Timed out after {args.timeout}s waiting for new telemetry inbox.", file=sys.stderr)
    sys.exit(1)

if __name__ == "__main__":
    main()
