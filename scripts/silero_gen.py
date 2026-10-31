#!/usr/bin/env python3
"""Генерит пять WAV-голосов Silero v4_ru для одного текста.

Используется разово — сгенерить сэмплы, выбрать голос, положить в
`emulator/static/`. Запуск: venv/bin/python silero_gen.py "Текст" out_dir/.
"""
import sys, os
from pathlib import Path
import torch

SPEAKERS = ["aidar", "baya", "kseniya", "xenia", "eugene"]


def main() -> None:
    text = sys.argv[1]
    out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    device = torch.device("cpu")
    print("Loading Silero v4_ru…")
    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker="v4_ru",
        trust_repo=True,
    )
    model.to(device)
    for sp in SPEAKERS:
        wav_path = out / f"silero_{sp}.wav"
        print(f"→ {sp}")
        audio = model.apply_tts(text=text, speaker=sp, sample_rate=48000)
        import torchaudio
        torchaudio.save(str(wav_path), audio.unsqueeze(0), 48000)


if __name__ == "__main__":
    main()
