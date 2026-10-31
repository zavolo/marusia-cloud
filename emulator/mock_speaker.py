#!/usr/bin/env python3
"""
Mock-колонка: проигрывает реальный phrase-флоу Капсулы в наш эмулятор.

Повторяет то, что делает железо (из живого /trace-захвата):
  POST /phrase/create/0chunk  -> phrase_id
  POST /phrase/add  xN         (KEYWORD_CONFIRMED..., последний END_OF_PHRASE)
  POST /phrase/result          -> команды text/tts/listen
  GET  /stream?stream_id=..    -> TTS-аудио

Запуск (эмулятор уже слушает :443 с самоподписанным сертом):
  python mock_speaker.py                       # https://127.0.0.1:443, TLS без проверки
  python mock_speaker.py http://127.0.0.1:8080 # если эмуль на http
"""
import asyncio
import json
import ssl
import sys
import aiohttp

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://127.0.0.1:443"
DEVICE_ID = ":c:d:capsula_neo:2.P4YnFg6slBe-e7mock"
SERIAL = "08548911ED83D7C6"
DEVICE_SECRET = "mock-device-secret-abc123"  # железо шлёт X-Vc-Device-Secret
XTOKEN = "mock-xtoken-from-vk-app"           # приходит из приложения VK

# как на железе: 34 чанка ~2560 байт, статусы как в реальном логе
CHUNK = b"\x00" * 2560

# заполняется после attach_xtoken
SESSION = {"secret": None, "session_id": None}


def dev_params(extra=None):
    p = {"device_id": DEVICE_ID, "serial": SERIAL, "device_ver": "3.5"}
    if SESSION["session_id"]:
        p["session_id"] = SESSION["session_id"]
    if extra:
        p.update(extra)
    return p


def auth_headers():
    h = {"X-Vc-Device-Secret": DEVICE_SECRET}
    if SESSION["secret"]:
        h["Authorization"] = "Bearer " + SESSION["secret"]
    return h


async def main():
    ctx = None
    if BASE.startswith("https"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # мы сами себе клиент; серт самоподписанный

    conn = aiohttp.TCPConnector(ssl=ctx) if ctx else None
    async with aiohttp.ClientSession(connector=conn) as s:
        print(f"[mock] -> {BASE}  device={DEVICE_ID[-10:]}")

        # 0) привязка аккаунта: attach_xtoken -> получаем session_secret
        async with s.post(f"{BASE}/account/attach_xtoken",
                          params=dev_params({"xtoken": XTOKEN}),
                          headers=auth_headers()) as r:
            aj = await r.json()
        res = aj["result"]
        SESSION["secret"] = res["session_secret"]
        SESSION["session_id"] = res["session_id"]
        print(f"[mock] attach -> {r.status}  session_secret={res['session_secret'][:10]}.. "
              f"session_id={res['session_id'][:8]} account={res['account_id']} "
              f"coords={res.get('coordinates')}")
        print(f"[mock] дальше авторизуюсь: Authorization: Bearer {SESSION['secret'][:10]}..")

        # 1) открыть фразу (первый keyword-чанк в теле, как /0chunk)
        async with s.post(f"{BASE}/phrase/create/0chunk",
                          params=dev_params(), data=CHUNK,
                          headers=auth_headers()) as r:
            j = await r.json()
        pid = j["result"]["phrase_id"]
        hint = j["result"].get("chunk_length_hint")
        print(f"[mock] create -> {r.status}  phrase_id={pid}  chunk_length_hint={hint}")

        # 2) лить аудио чанками: 1 OKAY_GO_ON, потом KEYWORD_CONFIRMED, финал END_OF_PHRASE
        n_chunks = 12
        for i in range(n_chunks):
            if i == 0:
                st = "OKAY_GO_ON"
            elif i == n_chunks - 1:
                st = "END_OF_PHRASE"
            else:
                st = "KEYWORD_CONFIRMED"
            async with s.post(f"{BASE}/phrase/add",
                             params=dev_params({"phrase_id": pid, "status": st}),
                             data=CHUNK, headers=auth_headers()) as r:
                await r.read()
            print(f"[mock] add #{i+1:02d} status={st:16s} -> {r.status}")

        # 3) забрать результат = команды колонке
        async with s.post(f"{BASE}/phrase/result",
                          params=dev_params({"phrase_id": pid}),
                          headers=auth_headers()) as r:
            res = await r.json()
        pr = res["result"]["phrase_result"]
        print(f"[mock] result -> {r.status}  asr='{pr.get('asr_text')}'  skill={pr.get('skill')}")
        for c in pr["commands"]:
            print(f"           command: {c}")

        # 4) выполнить tts-команду: скачать стрим (как vc_play_tts)
        tts = next((c for c in pr["commands"] if c.get("type") == "tts"), None)
        if tts:
            sid = tts["stream_id"]
            async with s.get(f"{BASE}/stream",
                            params=dev_params({"stream_id": sid}),
                            headers=auth_headers()) as r:
                audio = await r.read()
            print(f"[mock] stream stream_id={sid[:8]} -> {r.status}  "
                  f"{len(audio)} bytes  ctype={r.headers.get('Content-Type')}")
            print(f"[mock] >>> колонка бы проиграла ответ: '{tts.get('text')}'")

        print("[mock] OK — полный phrase-флоу отработал через эмулятор")

        # 5) проверка admin-push: ставим команду в очередь и забираем её опросом
        async with s.post(f"{BASE}/admin/command", params={"device_id": DEVICE_ID},
                         data=json.dumps({"type": "volume", "value": 7}),
                         headers=auth_headers()) as r:
            aj = await r.json()
        print(f"[mock] admin push volume -> {r.status}  {aj['result']}")
        async with s.get(f"{BASE}/phrase/commands", params=dev_params(),
                        headers=auth_headers()) as r:
            pj = await r.json()
        print(f"[mock] poll /phrase/commands -> {r.status}  commands={pj['result']['commands']}")


if __name__ == "__main__":
    asyncio.run(main())
