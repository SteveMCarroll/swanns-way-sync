"""Transcribe the full Swann's Way m4b with timestamps (GPU, faster-whisper).

Writes tools/transcript.json: {"duration":..., "segments":[{"start","end","text"},...]}
Prints a heartbeat so progress is observable.
"""
import json
import os
import sys
import time

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv",
                                    "Lib", "site-packages", "nvidia"))
for sub in ("cublas/bin", "cudnn/bin"):
    d = os.path.join(BASE, *sub.split("/"))
    if os.path.isdir(d):
        os.add_dll_directory(d)

from faster_whisper import WhisperModel, BatchedInferencePipeline  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO = os.path.join(ROOT, "Swann's Way.m4b")
OUT = os.path.join(os.path.dirname(__file__), "transcript.json")

MODEL = os.environ.get("WHISPER_MODEL", "medium.en")


def main():
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] loading {MODEL} on cuda...", flush=True)
    model = WhisperModel(MODEL, device="cuda", compute_type="float16")
    bp = BatchedInferencePipeline(model=model)
    print(f"[{time.strftime('%H:%M:%S')}] transcribing {AUDIO} ...", flush=True)
    segments, info = bp.transcribe(AUDIO, beam_size=1, batch_size=16,
                                   language="en", word_timestamps=True)
    total = float(info.duration)
    out = []
    last_beat = time.time()
    for s in segments:
        words = []
        for w in (s.words or []):
            words.append({"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)})
        out.append({"start": round(s.start, 2), "end": round(s.end, 2),
                    "text": s.text.strip(), "words": words})
        now = time.time()
        if now - last_beat > 20:
            pct = 100.0 * s.end / total if total else 0
            el = now - t0
            print(f"[{time.strftime('%H:%M:%S')}] {s.end/3600:5.2f}h / "
                  f"{total/3600:5.2f}h ({pct:4.1f}%)  elapsed {el/60:4.1f}m  "
                  f"segs={len(out)}", flush=True)
            last_beat = now
            with open(OUT + ".partial", "w", encoding="utf-8") as f:
                json.dump({"duration": total, "segments": out}, f)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"duration": total, "segments": out}, f, ensure_ascii=False)
    if os.path.exists(OUT + ".partial"):
        os.remove(OUT + ".partial")
    print(f"[{time.strftime('%H:%M:%S')}] DONE: {len(out)} segments, "
          f"{total/3600:.2f}h audio in {(time.time()-t0)/60:.1f} min -> {OUT}",
          flush=True)


if __name__ == "__main__":
    main()
