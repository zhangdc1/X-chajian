import argparse
import json
from urllib import request


def post_json(url: str, token: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Automation-Token": token,
        },
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a test automation job")
    parser.add_argument("--central-api", default="http://127.0.0.1:8766")
    parser.add_argument("--token", required=True)
    parser.add_argument("--job-type", required=True)
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument("--target-node-id")
    args = parser.parse_args()

    payload = {
        "job_type": args.job_type,
        "payload": json.loads(args.payload_json),
        "target_node_id": args.target_node_id,
    }
    print(json.dumps(post_json(f"{args.central_api.rstrip('/')}/jobs", args.token, payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
