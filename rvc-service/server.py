"""Silero → RVC → Opus pipeline.

HTTP endpoint `/tts?voice=valakas|goblin&text=...` возвращает Ogg Opus.
Внутри:
  1. Silero v4_ru → 48 kHz WAV.
  2. rvc-python infer с подгруженной .pth (+ .index) → WAV в голосе персонажа.
  3. ffmpeg → libopus Ogg.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import torch
from aiohttp import web
from rvc_python.infer import RVCInference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("rvc")

MODELS_DIR = Path(os.environ.get("MARUSIA_RVC_MODELS", "/models"))
DEFAULT_VOICE = os.environ.get("MARUSIA_RVC_DEFAULT", "valakas")
SILERO_SPEAKER = os.environ.get("MARUSIA_SILERO_SPEAKER", "eugene")

# ---- Silero (TTS-источник) --------------------------------------------------
_silero = None


def _load_silero():
    global _silero
    if _silero is not None:
        return _silero
    log.info("loading silero v4_ru…")
    device = torch.device("cpu")
    model, _ = torch.hub.load(repo_or_dir="snakers4/silero-models",
                              model="silero_tts", language="ru", speaker="v4_ru")
    model.to(device)
    _silero = model
    log.info("silero ready, speakers=%s", getattr(model, "speakers", "?"))
    return _silero


def silero_tts(text: str, sample_rate: int = 48000) -> tuple[int, "torch.Tensor"]:
    m = _load_silero()
    audio = m.apply_tts(text=text, speaker=SILERO_SPEAKER, sample_rate=sample_rate)
    return sample_rate, audio


# ---- RVC инференс -----------------------------------------------------------
_rvc_lock = asyncio.Lock()
_rvc: RVCInference | None = None
_rvc_voice: str | None = None


def _rvc_paths(voice: str) -> tuple[Path, Path | None]:
    pth = MODELS_DIR / f"{voice}.pth"
    idx = MODELS_DIR / f"{voice}.index"
    if not pth.exists():
        raise FileNotFoundError(f"no model {pth}")
    return pth, (idx if idx.exists() else None)


def _ensure_rvc(voice: str) -> RVCInference:
    global _rvc, _rvc_voice
    if _rvc is not None and _rvc_voice == voice:
        return _rvc
    pth, idx = _rvc_paths(voice)
    log.info("loading rvc model %s (index=%s)…", pth.name, idx.name if idx else "no")
    rvc = RVCInference(device="cpu")
    rvc.load_model(str(pth), index_path=str(idx) if idx else "")
    _rvc = rvc
    _rvc_voice = voice
    log.info("rvc ready: %s", voice)
    return rvc


async def rvc_convert(voice: str, src_wav_path: str, dst_wav_path: str) -> None:
    async with _rvc_lock:
        rvc = _ensure_rvc(voice)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: rvc.infer_file(src_wav_path, dst_wav_path))


# ---- ffmpeg: WAV → Ogg Opus -------------------------------------------------
async def wav_to_opus(wav_path: str) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", wav_path, "-c:a", "libopus", "-b:a", "48k", "-f", "ogg", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {err.decode(errors='replace')}")
    return out


# ---- HTTP ------------------------------------------------------------------
async def tts(request: web.Request) -> web.Response:
    voice = request.query.get("voice", DEFAULT_VOICE)
    text = request.query.get("text") or ""
    if request.method == "POST":
        try:
            body = await request.json()
            text = body.get("text", text)
            voice = body.get("voice", voice)
        except Exception:  # noqa: BLE001
            pass
    if not text:
        return web.Response(status=400, text="text required")
    log.info("tts voice=%s len=%d: %r", voice, len(text), text[:60])

    try:
        rate, audio = await asyncio.get_running_loop().run_in_executor(
            None, lambda: silero_tts(text, 48000))
    except Exception as e:  # noqa: BLE001
        return web.Response(status=500, text=f"silero: {e}")

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.wav")
        dst = os.path.join(td, "dst.wav")
        import torchaudio
        torchaudio.save(src, audio.unsqueeze(0), rate)
        try:
            await rvc_convert(voice, src, dst)
        except Exception as e:  # noqa: BLE001
            log.exception("rvc fail — fallback to raw silero")
            dst = src  # отдадим чистый silero чтобы не ломать колонку
        opus = await wav_to_opus(dst)
    log.info("  -> %d B opus", len(opus))
    return web.Response(body=opus, content_type="audio/ogg")


async def healthz(_req):
    voices = sorted(p.stem for p in MODELS_DIR.glob("*.pth"))
    return web.json_response({"ok": True, "voices": voices,
                              "default": DEFAULT_VOICE, "silero_speaker": SILERO_SPEAKER})


def main():
    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_route("*", "/tts", tts)
    app.router.add_get("/healthz", healthz)
    web.run_app(app, host="0.0.0.0", port=8081)


if __name__ == "__main__":
    main()
