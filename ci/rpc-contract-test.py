import json
import subprocess
import sys

REQUIRED_METHODS = {
    "bioauth_status",
    "bioauth_enrollV2",
    "bioauth_authenticateV2",
    "bioauth_getFacetecDeviceSdkParams",
    "bioauth_getFacetecSessionToken",
    "system_health",
    "chain_getHeader",
    "chain_getFinalizedHead",
    "chain_subscribeNewHeads",
    "chain_subscribeFinalizedHeads",
}


def rpc_call(container: str, method: str, params=None) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []})
    cmd = [
        "docker", "exec", container, "sh", "-c",
        f"curl -sS --fail -H 'Content-Type: application/json' -d '{payload}' http://127.0.0.1:9944",
    ]
    out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=30)
    return json.loads(out)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: rpc-contract-test.py <container-name>", file=sys.stderr)
        return 2
    container = sys.argv[1]

    resp = rpc_call(container, "rpc_methods")
    if "result" not in resp:
        print(f"FAIL: rpc_methods returned no result: {resp}", file=sys.stderr)
        return 1

    methods = set(resp["result"]["methods"])
    missing = REQUIRED_METHODS - methods
    if missing:
        print(f"FAIL: required RPC methods missing: {sorted(missing)}", file=sys.stderr)
        return 1

    status = rpc_call(container, "bioauth_status")
    if "result" not in status:
        print(f"FAIL: bioauth_status returned no result: {status}", file=sys.stderr)
        return 1

    print(f"rpc-contract-test: OK - {len(REQUIRED_METHODS)} required methods present")
    print(f"rpc-contract-test: bioauth_status = {status['result']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
