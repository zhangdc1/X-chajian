import argparse
import asyncio
import json


async def fetch(session, url: str, proxy: str | None, headers: dict | None = None) -> None:
    print(f"\nGET {url}")
    async with session.get(url, proxy=proxy, headers=headers or {}) as resp:
        text = await resp.text()
        print(f"Status: {resp.status}")
        print(text[:500])


async def ws_test(session, url: str, proxy: str | None) -> None:
    print(f"\nWS {url}")
    async with session.ws_connect(url, proxy=proxy, timeout=20) as ws:
        msg = await ws.receive(timeout=20)
        print(f"WS message type: {msg.type}")
        data = msg.data
        if isinstance(data, str):
            print(data[:500])
        else:
            print(data)


async def main_async(args) -> None:
    import aiohttp

    proxy = args.proxy.strip() or None
    print(f"Proxy: {proxy or '(none)'}")
    timeout = aiohttp.ClientTimeout(total=30)
    headers = {}
    if args.token:
        headers["Authorization"] = f"Bot {args.token}"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if args.all:
            await fetch(session, "https://discord.com", proxy)
            await fetch(session, "https://discord.com/api/v10/gateway", proxy)
            if args.token:
                await fetch(session, "https://discord.com/api/v10/users/@me", proxy, headers=headers)
            await ws_test(session, "wss://gateway.discord.gg/?v=10&encoding=json", proxy)
        else:
            if args.url.startswith("wss://"):
                await ws_test(session, args.url, proxy)
            else:
                await fetch(session, args.url, proxy, headers=headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Discord connectivity using aiohttp")
    parser.add_argument("--url", default="https://discord.com/api/v10/users/@me")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
