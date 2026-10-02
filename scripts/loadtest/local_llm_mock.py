"""Local OpenAI-compatible mock with fixed latency and concurrent request handling."""

from __future__ import annotations

import asyncio
import json
import os
import re

DELAY_SECONDS = float(os.environ.get("MOCK_LLM_DELAY_SECONDS", "0.25"))
CALLS = 0


def object_particle(text: str) -> str:
    for character in reversed(text):
        code = ord(character) - ord("가")
        if 0 <= code <= 11171:
            return "을" if code % 28 else "를"
    return "을"


def situation_value(content: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", content, re.MULTILINE)
    return match.group(1).strip() if match else "(없음)"


def make_reason(content: str) -> str:
    recipe = situation_value(content, "레시피")
    have_value = situation_value(content, "가지고 있는 재료")
    missing_value = situation_value(content, "부족한 재료")
    have = [] if have_value == "(없음)" else [name.strip() for name in have_value.split(",")]
    missing = [] if missing_value == "(없음)" else [name.strip() for name in missing_value.split(",")]

    if have:
        first = f"{have[0]}의 풍미가 {recipe}에 잘 어울립니다."
    else:
        first = f"{recipe}를 준비하기 좋은 재료 구성을 갖췄습니다."

    if not missing:
        second = f"부족한 재료가 없으니 바로 {recipe}{object_particle(recipe)} 만들어 보세요."
    elif len(missing) <= 3:
        second = f"{'·'.join(missing)}만 더 담으면 {recipe}{object_particle(recipe)} 완성할 수 있어요."
    else:
        second = f"부족한 재료 몇 가지를 확인하면 {recipe}{object_particle(recipe)} 준비할 수 있어요."
    return f"{first} {second}"


def response_bytes(status: int, payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = (
        f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    return header + body


async def read_request(reader: asyncio.StreamReader) -> tuple[str, str, bytes] | None:
    try:
        header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
        return None
    header_lines = header_bytes.decode("iso-8859-1").split("\r\n")
    try:
        method, path, _version = header_lines[0].split(" ", maxsplit=2)
    except ValueError:
        return None
    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" in line:
            key, value = line.split(":", maxsplit=1)
            headers[key.strip().lower()] = value.strip()
    body_length = int(headers.get("content-length", "0"))
    body = await reader.readexactly(body_length) if body_length else b""
    return method, path, body


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    global CALLS
    try:
        request = await read_request(reader)
        if request is None:
            return
        method, path, body = request
        if method == "GET" and path == "/__stats":
            response = response_bytes(200, {"mock_model_calls": CALLS, "delay_seconds": DELAY_SECONDS})
        elif method == "POST" and path == "/api/v1/chat/completions":
            payload = json.loads(body)
            messages = payload.get("messages", [])
            user_message = next(message["content"] for message in messages if message.get("role") == "user")
            reason = make_reason(user_message)
            await asyncio.sleep(DELAY_SECONDS)
            CALLS += 1
            response = response_bytes(200, {"choices": [{"message": {"content": reason}}]})
        else:
            response = response_bytes(404, {"error": "not_found"})
        writer.write(response)
        await writer.drain()
    except (
        ConnectionError,
        asyncio.IncompleteReadError,
        json.JSONDecodeError,
        KeyError,
        StopIteration,
        TypeError,
        ValueError,
    ):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def main() -> None:
    server = await asyncio.start_server(handle_client, "0.0.0.0", 8080, backlog=512)
    print(f"local model mock ready; fixed delay={DELAY_SECONDS:.3f}s", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
