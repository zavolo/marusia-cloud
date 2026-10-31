# Marusia / VK Capsula Neo — эмулятор облака

Локальная замена `vc.go.mail.ru`: говорит на протоколе колонки (реверс прошивки
BES2300WP + живой `/trace`-захват), принимает голосовые фразы, отдаёт команды и
управляет колонкой. TTS — настоящий Ogg Opus (колонка декодирует без сбоев).

## Структура

```
server.py            точка входа (argparse -> Config -> run)
emu/
  config.py          настройки
  backend.py         мимикрия бэка (nginx-заголовки, X-Request-Id, конверт ошибок, 404)
  auth.py            реестр устройств + сессии (attach_xtoken, Bearer)
  commands.py        билдеры всех команд облако->колонка + очередь
  tts.py             TTS-движок: текст -> Ogg Opus через ffmpeg (внешняя TTS опц.)
  handlers.py        обработчики маршрутов (phrase / account / device / stream / admin)
  app.py             сборка приложения, middleware, роуты
mock_speaker.py      тест-клиент (attach + phrase + admin push)
```

Требования: Python 3.10+, `aiohttp`, `ffmpeg` (в PATH, с libopus).

## Запуск

```bash
python server.py                                    # http :8080 — для mock/отладки
python server.py --tls cert.pem key.pem --port 443  # https — для железа
# внешняя TTS (текст в stdin, WAV в stdout):
python server.py --tls cert.pem key.pem --port 443 --tts-cmd "piper --model ru.onnx -f -"
```

Проверка автономно (TLS не нужен):
```bash
python mock_speaker.py                       # против https://127.0.0.1:443
python mock_speaker.py http://127.0.0.1:8080 # против http
```

## Протокол (что реализовано)

- **phrase-флоу**: `POST /phrase/create[/0chunk|/text|/event|/flowmode]` → `phrase_id`;
  `POST /phrase/add` (чанки со статусами); `POST /phrase/result` → команды
  `text` + `tts` + `listen`; `GET /stream?stream_id=..` → Ogg Opus.
- **аккаунт/сессия**: `POST /account/attach_xtoken` → `{result:{session_secret,
  session_id, account_id, coordinates}}`; дальше колонка носит `Authorization:
  Bearer <session_secret>` + `X-Vc-Device-Secret`.
- **телеметрия/настройки**: `/rt_logs/send`, `/device/stat`, `/device/settings/v1/*`,
  `/connect` — принимаются заглушками.
- **мимикрия бэка**: `Server: nginx`, `X-Request-Id` на каждом ответе, 404 →
  `text/plain "404: Not Found"`, ошибка → `{qid,error,code,reason}` + reason в
  статус-строке (как настоящий `vc.go.mail.ru`).

## Аутентификация

Реверс: `FUN_2d1ac8c4` (парсер `result.{session_secret,session_id,account_id,
coordinates}`), `FUN_2d1bd09c` (Bearer), `FUN_2d1bed24` (device_id/serial/session_id
+ X-Vc-Device-Secret). Режимы:

- **lenient** (по умолчанию): колонка с персистентным Bearer от старого облака
  принимается как `adopted` — работает сразу.
- **`--strict-auth`**: неизвестный Bearer на phrase → `401`, колонка делает
  re-attach через `attach_xtoken`.

## Управление колонкой (admin-API)

Команды колонке приходят только от сервера (массив `commands`). Наш пульт кладёт
их в очередь; доставка — в ближайшем `/phrase/result` или опросом `/phrase/commands`.

```bash
# озвучить произвольный текст
curl -k -X POST "https://<host>/admin/say?device_id=<id>&text=Привет"
# произвольная команда
curl -k -X POST "https://<host>/admin/command?device_id=<id>" -d '{"type":"volume","value":7}'
curl -k -X POST "https://<host>/admin/command?device_id=<id>" -d '{"type":"sound","url":"https://<host>/stream?stream_id=..."}'
curl -k -X POST "https://<host>/admin/command?device_id=<id>" -d '{"type":"enter_flowmode"}'
# список устройств
curl -k "https://<host>/admin/devices?device_id=admin"
```

Поддержанные `type` (из command_runner прошивки): `tts, text, listen, sound, play,
playlist, volume, pause/resume/next/previous/rewind_control, enter_flowmode,
flowmode_sound, flowmode_bg_music, bluetooth_pair, pretty_mode, led_digits_animation,
enter/exit_light_demo, alarm, reminder, reminder_cancel, timer, delay,
cancel_notification, scenario_step, send_event_data, speed_test`.

## Подключение реальной колонки

Проверено кодом и вживую: колонка держит TLS `MBEDTLS_SSL_VERIFY_REQUIRED` и сверяет
hostname (`mbedtls_ssl_set_hostname`), самоподписанный/свой-CA серт отвергает. Рецепт:

1. Свой домен + **валидный Let's Encrypt-серт** (верификация через DNS-TXT; корни
   ISRG X1/X2 есть в бандле колонки).
2. **UART RAM-патч** строки хоста в `ssl_ctx + 0x498` → твой домен (подписанный
   флеш не трогается, secure boot ни при чём).
3. DNS: твой домен → этот сервер.
4. `python server.py --tls fullchain.pem privkey.pem --port 443`.

После этого колонка проходит TLS (hostname совпал, серт доверенный) → авторизуется →
получает команды.
