#!/usr/bin/env python3
"""
OpenAI-compatible PCM streaming wrapper for MOSS-TTS-Nano.

Run this file from the MOSS-TTS-Nano repository root:
    pip install fastapi uvicorn numpy
    uvicorn moss_tts_nano_openai_pcm_server:app --host 0.0.0.0 --port 8000

Open WebUI settings:
    TTS Engine: OpenAI
    API Base URL: http://<host>:8000/v1
    TTS Model: moss-tts-nano
    Voice: Junhao
"""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from typing import Iterator, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from moss_tts_nano_runtime import (
    DEFAULT_AUDIO_TOKENIZER_PATH,
    DEFAULT_CHECKPOINT_PATH,
    NanoTTSService,
)

log = logging.getLogger("moss_tts_pcm_server")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="MOSS-TTS-Nano OpenAI-compatible PCM streaming TTS")

service = NanoTTSService(
    checkpoint_path=os.getenv("MOSS_TTS_CHECKPOINT", str(DEFAULT_CHECKPOINT_PATH)),
    audio_tokenizer_path=os.getenv("MOSS_AUDIO_TOKENIZER", str(DEFAULT_AUDIO_TOKENIZER_PATH)),
    device=os.getenv("MOSS_TTS_DEVICE", "auto"),
    dtype=os.getenv("MOSS_TTS_DTYPE", "auto"),
    attn_implementation=os.getenv("MOSS_TTS_ATTN", "auto"),
)


class SpeechRequest(BaseModel):
    input: str = Field(default="")
    voice: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    stream: bool = Field(default=False)
    response_format: Optional[str] = Field(default="pcm")

    # MOSS generation knobs. They are optional so Open WebUI can ignore them.
    max_new_frames: int = 375
    voice_clone_max_text_tokens: int = 75
    do_sample: bool = True
    text_temperature: float = 1.0
    text_top_p: float = 1.0
    text_top_k: int = 50
    audio_temperature: float = 0.8
    audio_top_p: float = 0.95
    audio_top_k: int = 25
    audio_repetition_penalty: float = 1.2
    seed: Optional[int] = None


def _normalize_audio_array(audio_array: np.ndarray) -> np.ndarray:
    audio_np = np.asarray(audio_array, dtype=np.float32)
    if audio_np.ndim == 1:
        return audio_np[:, None]
    if audio_np.ndim == 2 and audio_np.shape[0] <= 8 and audio_np.shape[0] < audio_np.shape[1]:
        return audio_np.T
    if audio_np.ndim == 2:
        return audio_np
    raise ValueError(f"Unsupported audio array shape: {audio_np.shape}")


def _audio_to_pcm16le_bytes(audio_array: np.ndarray) -> tuple[bytes, int]:
    audio_np = _normalize_audio_array(audio_array)
    audio_np = np.clip(audio_np, -1.0, 1.0)
    audio_int16 = (audio_np * 32767.0).astype("<i2")
    channels = int(audio_int16.shape[1])
    return audio_int16.tobytes(), channels


def _audio_to_wav_bytes(audio_array: np.ndarray, sample_rate: int) -> bytes:
    pcm, channels = _audio_to_pcm16le_bytes(audio_array)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sample_rate))
        wav_file.writeframes(pcm)
    buf.seek(0)
    return buf.read()


def _build_stream_events(req: SpeechRequest) -> Iterator[dict[str, object]]:
    return service.synthesize_stream(
        text=req.input,
        voice=req.voice,
        mode="voice_clone",
        max_new_frames=req.max_new_frames,
        voice_clone_max_text_tokens=req.voice_clone_max_text_tokens,
        do_sample=req.do_sample,
        text_temperature=req.text_temperature,
        text_top_p=req.text_top_p,
        text_top_k=req.text_top_k,
        audio_temperature=req.audio_temperature,
        audio_top_p=req.audio_top_p,
        audio_top_k=req.audio_top_k,
        audio_repetition_penalty=req.audio_repetition_penalty,
        seed=req.seed,
    )


def _next_audio_event(events: Iterator[dict[str, object]]) -> Optional[dict[str, object]]:
    for event in events:
        if event.get("type") == "audio":
            return event
    return None


def _stream_pcm_events(
    events: Iterator[dict[str, object]],
    *,
    first_pcm: bytes,
    first_sample_rate: int,
    first_channels: int,
    started_at: float,
) -> Iterator[bytes]:
    first_audio_at = time.monotonic()
    total_bytes = len(first_pcm)
    log.info(
        "first PCM chunk: latency=%.3fs sample_rate=%s channels=%s bytes=%s",
        first_audio_at - started_at,
        first_sample_rate,
        first_channels,
        len(first_pcm),
    )
    yield first_pcm

    for event in events:
        if event.get("type") != "audio":
            continue
        pcm, _channels = _audio_to_pcm16le_bytes(event["waveform_numpy"])
        if not pcm:
            continue
        total_bytes += len(pcm)
        yield pcm

    log.info(
        "PCM stream done: elapsed=%.3fs sample_rate=%s channels=%s bytes=%s",
        time.monotonic() - started_at,
        first_sample_rate,
        first_channels,
        total_bytes,
    )


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "voices": service.list_voice_names(),
        "default_voice": service.default_voice,
        "device": str(service.device),
        "dtype": str(service.dtype),
    }


@app.get("/v1/models")
def models() -> dict[str, object]:
    return {"object": "list", "data": [{"id": "moss-tts-nano", "object": "model"}]}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    req.input = (req.input or "").strip()
    if not req.input:
        raise HTTPException(status_code=400, detail="input is required")

    response_format = (req.response_format or "pcm").lower()

    if req.stream or response_format in {"pcm", "s16le", "raw"}:
        started_at = time.monotonic()
        events = _build_stream_events(req)
        first_event = _next_audio_event(events)
        if first_event is None:
            raise HTTPException(status_code=500, detail="MOSS-TTS-Nano produced no audio chunks")

        first_pcm, channels = _audio_to_pcm16le_bytes(first_event["waveform_numpy"])
        sample_rate = int(first_event["sample_rate"])
        if not first_pcm:
            raise HTTPException(status_code=500, detail="MOSS-TTS-Nano produced an empty first audio chunk")

        return StreamingResponse(
            _stream_pcm_events(
                events,
                first_pcm=first_pcm,
                first_sample_rate=sample_rate,
                first_channels=channels,
                started_at=started_at,
            ),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Audio-Codec": "pcm_s16le",
                "X-Audio-Sample-Rate": str(sample_rate),
                "X-Audio-Channels": str(channels),
                "X-Audio-Endian": "little",
            },
        )

    # Non-stream fallback: return WAV, which Open WebUI can cache/play normally.
    result = service.synthesize(
        text=req.input,
        voice=req.voice,
        mode="voice_clone",
        max_new_frames=req.max_new_frames,
        voice_clone_max_text_tokens=req.voice_clone_max_text_tokens,
        do_sample=req.do_sample,
        text_temperature=req.text_temperature,
        text_top_p=req.text_top_p,
        text_top_k=req.text_top_k,
        audio_temperature=req.audio_temperature,
        audio_top_p=req.audio_top_p,
        audio_top_k=req.audio_top_k,
        audio_repetition_penalty=req.audio_repetition_penalty,
        seed=req.seed,
    )
    wav_bytes = _audio_to_wav_bytes(result["waveform_numpy"], int(result["sample_rate"]))
    return Response(wav_bytes, media_type="audio/wav")
