import argparse
import json
from urllib import parse, request


def api_json(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"X-Automation-Token": token}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(url, data=data, headers=headers, method=method)
    opener = request.build_opener(request.ProxyHandler({}))
    with opener.open(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Account and Grok-plan helper")
    parser.add_argument("--central-api", default="http://127.0.0.1:8766")
    parser.add_argument("--token", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list-accounts")
    list_cmd.add_argument("--group-id")
    list_cmd.add_argument("--node-id")
    list_cmd.add_argument("--limit", type=int, default=100)

    grok_cmd = sub.add_parser("create-grok-plan-jobs")
    grok_cmd.add_argument("--group-id", required=True)
    grok_cmd.add_argument("--period", choices=["daily", "weekly", "monthly", "custom"], default="weekly")
    grok_cmd.add_argument("--target-node-id")
    grok_cmd.add_argument("--limit", type=int, default=500)

    plans_cmd = sub.add_parser("list-plans")
    plans_cmd.add_argument("--account-id")
    plans_cmd.add_argument("--group-id")
    plans_cmd.add_argument("--status")
    plans_cmd.add_argument("--limit", type=int, default=20)

    approve_cmd = sub.add_parser("approve-plan")
    approve_cmd.add_argument("--plan-id", type=int, required=True)
    approve_cmd.add_argument("--max-days", type=int, default=7)
    approve_cmd.add_argument("--dispatch-now", action="store_true")

    schedule_cmd = sub.add_parser("list-schedule")
    schedule_cmd.add_argument("--group-id")
    schedule_cmd.add_argument("--account-id")
    schedule_cmd.add_argument("--status")
    schedule_cmd.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    base = args.central_api.rstrip("/")
    if args.command == "list-accounts":
        query = {
            "limit": str(args.limit),
        }
        if args.group_id:
            query["group_id"] = args.group_id
        if args.node_id:
            query["node_id"] = args.node_id
        url = f"{base}/accounts?{parse.urlencode(query)}"
        result = api_json("GET", url, args.token)
    elif args.command == "create-grok-plan-jobs":
        result = api_json(
            "POST",
            f"{base}/plans/grok/batch",
            args.token,
            {
                "group_id": args.group_id,
                "period": args.period,
                "target_node_id": args.target_node_id,
                "limit": args.limit,
            },
        )
    elif args.command == "list-plans":
        query = {"limit": str(args.limit)}
        if args.account_id:
            query["account_id"] = args.account_id
        if args.group_id:
            query["group_id"] = args.group_id
        if args.status:
            query["status"] = args.status
        result = api_json("GET", f"{base}/plans?{parse.urlencode(query)}", args.token)
    elif args.command == "approve-plan":
        result = api_json(
            "POST",
            f"{base}/plans/{args.plan_id}/approve",
            args.token,
            {"max_days": args.max_days, "dispatch_now": args.dispatch_now},
        )
    else:
        query = {"limit": str(args.limit)}
        if args.group_id:
            query["group_id"] = args.group_id
        if args.account_id:
            query["account_id"] = args.account_id
        if args.status:
            query["status"] = args.status
        result = api_json("GET", f"{base}/schedule?{parse.urlencode(query)}", args.token)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
