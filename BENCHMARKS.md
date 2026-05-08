# Trusty benchmarks

End-to-end `/chat` latency on the Raspberry Pi 5, measured with `bash /tmp/time_chat.sh`. All numbers are seconds per turn, warm planner cache, llama.cpp `--ctx-size 4096 --threads 3 --jinja`.

## Gemma 4 E2B IT
### Compare Gemma Versions Speed

| Prompt | Q6_K | Q5_K_M | Q4_K_M | Δ Q4 vs Q6 |
|---|---:|---:|---:|---:|
| `what is the weather in Dublin` | 79.5 s | 68.6 s | 60.0 s | −19.5 s (−24.5%) |
| `stop the vacuum` | 73.9 s | 68.1 s | 60.3 s | −13.6 s (−18.4%) |
| `play some jazz` | 88.3 s | 75.1 s | 59.5 s | −28.8 s (−32.6%) |
| `what is the capital of France` | 72.3 s | 68.4 s | 59.9 s | −12.4 s (−17.2%) |
| `update my location to Berlin` | 72.1 s | 66.8 s | 59.7 s | −12.4 s (−17.2%) |
| **average per turn** | **77.2 s** | **69.4 s** | **59.9 s** | **−17.3 s (−22.4%)** |

File sizes: Q6_K = 3.9 GB, Q5_K_M = 3.1 GB, Q4_K_M = 2.8 GB.

Routing accuracy on the 32-prompt suite (`/tmp/route_test.sh`, run on Mac): **32 / 32** for all three quants. No planner-prompt edits required for any of them. The five benchmark prompts (one per route category — weather / vacuum / music / local-answer / memory) all returned the correct plan on Pi for Q4_K_M.

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
| `Q4_0` | 2.8 GB | 32 / 32 (2 iterations) | Routing OK with prompt iter, but lost the Pi A/B vs Q4_K_M (67.3 s vs 63.3 s avg, same iterated prompt). `local.answer` decode was 2× slower (118.8 s vs 63 s) — Q4_K_M's ARM NEON kernel beats Q4_0's older one for sustained token generation. |
| `Q4_K_S` | 2.8 GB | 3 / 32 | Routes everything to `local.answer`. |

