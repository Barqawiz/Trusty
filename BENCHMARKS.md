# Trusty benchmarks

End-to-end `/chat` latency on the Raspberry Pi 5, measured with the FastAPI runner pointed at `llama.cpp --ctx-size 4096 --threads 3 --threads-batch 4 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --jinja`. All numbers are wall-clock seconds end-to-end through `/chat`, with `KNOWN_INTENT_LAYER=OFF` so every turn exercises the Gemma planner.

## Gemma 4 E2B IT: Trusty-tuned vs un-tuned

The Trusty-tuned build pairs a Q4_K_S GGUF with a short 1.5 KB planner prompt (the un-tuned build needs an 11 KB prompt to route correctly). The combination is the headline win.

| Build | Planner prompt | avg / turn | speedup | JSON-valid |
|---|---|---:|---:|---:|
| Un-tuned Q4_K_S | long, 11 KB | ~100 s | 1.0× | 50 % |
| **Tuned Q4_K_S** (deploy) | short, 1.5 KB | **17.6 s** | **5.7×** | **100 %** |
| Tuned Q3_K_M (fallback) | short, 1.5 KB | 16.8 s | 6.0× | 75 % (warmup parse fail) |

File sizes: Tuned Q4_K_S = 3.1 GB, Tuned Q3_K_M = 3.0 GB. Both ship with the short `prompts/planner_system.md` (1.5 KB); the orchestrator auto-selects it because the filename contains "trusty".

Verdict: deploy **Tuned Q4_K_S**. Q3_K_M is within 1 s of Q4_K_S on speed but the answers are noticeably terser and the cold-warmup occasionally hits a planner JSON parse error, so it stays as a manual A/B fallback.

### Historical un-tuned numbers (kept for reference)

These older numbers are warm-cache, single-thread averages on un-tuned Gemma with the long 11 KB prompt, before the run-7 / run-8 fine-tunes existed.

| Prompt | Q6_K | Q5_K_M | Q4_K_M | Δ Q4 vs Q6 |
|---|---:|---:|---:|---:|
| `what is the weather in Dublin` | 79.5 s | 68.6 s | 60.0 s | −19.5 s (−24.5%) |
| `stop the vacuum` | 73.9 s | 68.1 s | 60.3 s | −13.6 s (−18.4%) |
| `play some jazz` | 88.3 s | 75.1 s | 59.5 s | −28.8 s (−32.6%) |
| `what is the capital of France` | 72.3 s | 68.4 s | 59.9 s | −12.4 s (−17.2%) |
| `update my location to Berlin` | 72.1 s | 66.8 s | 59.7 s | −12.4 s (−17.2%) |
| **average per turn** | **77.2 s** | **69.4 s** | **59.9 s** | **−17.3 s (−22.4%)** |

File sizes: Q6_K = 3.9 GB, Q5_K_M = 3.1 GB, Q4_K_M = 2.8 GB. Routing accuracy on the 32-prompt suite was 32 / 32 for all three un-tuned quants. The five benchmark prompts (one per route category: weather / vacuum / music / local-answer / memory) all returned the correct plan on Pi for Q4_K_M.

## Reproduction

Run on the host being tested (Pi or Mac), after llama-server + uvicorn are warm:

```bash
bash /tmp/time_chat.sh
```

The script POSTs an initial `"hi"` to warm the planner cache, then times five representative prompts (one per route category) and reports per-prompt and average milliseconds.

## Failed experiments

| Quant | File size | Mac routing (best) | Why it didn't win |
|---|---:|---|---|
| `Q3_K_S` | 2.2 GB | 27 / 32 | Memory + weather drop to `local.answer`. |
| `Q3_K_M` | 2.3 GB | 31 / 32 | `update my location to Dublin` → `weather.live`. |
| `IQ4_XS` | 2.7 GB | 32 / 32 | Slower on Pi: 101.6 s vs Q4_K_M 59.9 s (ARM I-quant kernels). |
| `Q4_0` | 2.8 GB | 32 / 32 (2 iterations) | Routing OK with prompt iter, but lost the Pi A/B vs Q4_K_M (67.3 s vs 63.3 s avg, same iterated prompt). `local.answer` decode was 2× slower (118.8 s vs 63 s): Q4_K_M's ARM NEON kernel beats Q4_0's older one for sustained token generation. |
| `Q4_K_S` | 2.8 GB | 3 / 32 | Routes everything to `local.answer` un-tuned. Fixed by fine-tuning (see the Tuned Q4_K_S column above). |

