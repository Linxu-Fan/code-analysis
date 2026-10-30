# Setting up the AI Assistant

CodeScope's **AI Assistant** answers questions about your code with a language model running **on your own
PC**. Nothing leaves your machine: CodeScope talks to a local AI server at `127.0.0.1`. The model reads
your code through CodeScope's tools: entity search, source, call / class / modification trees, text search
and metrics. Its answers link to `file:line` locations you can click.

You need:

* a model in **GGUF** format, for example `D:\AI\Models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`;
* **llama.cpp** (its `llama-server` program) to run it. CodeScope can download it for you.

Any other server with an OpenAI-compatible API works too: LM Studio, Ollama, vLLM, and so on.

---

## 1. Quick setup in CodeScope (Windows)

1. Open **AI → Set Up Local Model…**. The same dialog opens from **Set Up…** in the AI Assistant panel.
2. **Model file:** choose your `.gguf` file.
3. Leave **llama.cpp build** on *Automatic*: NVIDIA GPUs get the CUDA build, AMD and Intel GPUs the Vulkan
   build, and PCs without a GPU the CPU build.
4. Click **Install & Write Start Script**. CodeScope then:
   * reads the model's header: size, layers, mixture-of-experts;
   * detects your GPU, its memory, the CUDA driver version and the RAM;
   * downloads the latest llama.cpp release from GitHub (0.2–0.6 GB) into
     `%APPDATA%\CodeScope\llama.cpp`;
   * decides how much of the model goes into GPU memory (see *Performance* below);
   * writes `%APPDATA%\CodeScope\start-ai-server.bat` and points the assistant at it.
5. Click **Start Server**. A console window opens and loads the model, which takes a minute or two. Keep
   that window open while you use the assistant.
6. Open the **AI Assistant** panel (Ctrl+Shift+A) and ask away. The dot in the panel turns green when the
   server answers.

Next time, just run `start-ai-server.bat` (or press **Start Server** again) before using the assistant.

### The same from the command line

```bat
codescope-cli ai-setup "D:\AI\Models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf" --start
```

```bat
codescope-cli ask myproject.csdb "Where is damping in EqualityConstraintSettingsT modified?"
```

The first command downloads llama.cpp, writes the start script and starts the server. The second asks a
question from the console; `--reasoning` also prints the model's thinking. Options of `ai-setup`:

| Option | Meaning |
|---|---|
| `--backend cuda` / `vulkan` / `cpu` | force a llama.cpp build |
| `--server PATH` | use an existing `llama-server.exe` instead of downloading |
| `--ctx 65536` | context window (default 32768 tokens) |
| `--port 8081` | server port (default 8080) |

---

## 2. Manual setup with llama.cpp

1. Download llama.cpp from <https://github.com/ggml-org/llama.cpp/releases> (latest release, *Assets*):
   * **NVIDIA:** `llama-<build>-bin-win-cuda-<version>-x64.zip` **and** the matching
     `cudart-llama-bin-win-cuda-<version>-x64.zip`. Pick a CUDA version no newer than the one
     `nvidia-smi` reports.
   * **AMD / Intel GPU:** `llama-<build>-bin-win-vulkan-x64.zip`
   * **No GPU:** `llama-<build>-bin-win-cpu-x64.zip`

   Unzip everything into one folder, e.g. `D:\AI\llama.cpp`.
2. Start the server from that folder:

   ```bat
   llama-server.exe -m "D:\AI\Models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf" --jinja -c 32768 -ngl 99 --n-cpu-moe 25 --host 127.0.0.1 --port 8080
   ```

   | Flag | Why |
   |---|---|
   | `--jinja` | **required**: enables tool calling, which the assistant uses to read your code |
   | `-c 32768` | context window; the assistant needs room for the code it reads (16k minimum, 32k recommended) |
   | `-ngl 99` | put all layers on the GPU (use `-ngl 0` without a GPU) |
   | `--n-cpu-moe 25` | mixture-of-experts models: keep the expert weights of 25 layers in RAM (see below) |
   | `--host 127.0.0.1` | only your own PC can connect |

3. Open <http://127.0.0.1:8080> in a browser. llama-server's own chat page means it works.
4. In CodeScope, open **AI → AI Settings…**, set the server address to `http://127.0.0.1:8080/v1` and
   click **Test Connection**.

---

## 3. LM Studio or Ollama instead

**LM Studio:** load the model, open the *Developer* tab and start the server. In CodeScope set the
address to `http://127.0.0.1:1234/v1` and pick the model in **AI Settings**.

**Ollama:** create a `Modelfile` next to the model:

```text
FROM D:\AI\Models\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf
PARAMETER num_ctx 32768
```

Then run `ollama create qwen36 -f Modelfile`. In CodeScope set the address to
`http://127.0.0.1:11434/v1` and the model to `qwen36`. Set `num_ctx`: Ollama's default context is small
and cuts off the code the assistant reads. Very new model architectures can reach llama.cpp before Ollama
and LM Studio support them.

---

## 4. Performance: GPU memory and mixture-of-experts models

`Qwen3.6-35B-A3B` is a *mixture-of-experts* model. It has 35 B parameters in total, but only about 3 B are
used per token. That makes it fast even when part of it sits in normal RAM:

* **The whole file fits in GPU memory** (VRAM larger than the model plus about 2.5 GB): use `-ngl 99`.
  Everything runs on the GPU.
* **It does not fit:** keep `-ngl 99` and add `--n-cpu-moe N`. The attention layers stay on the GPU, and
  the expert weights of `N` layers go to RAM. CodeScope picks `N` from your VRAM. Lower `N` while the GPU
  still has free memory (Task Manager → Performance → GPU) for more speed. Raise it if the server fails
  with an out-of-memory error.
* **No GPU:** `-ngl 0` runs on the CPU. A 3B-active model is usable, a dense 30B model is slow.

The model file must fit in **RAM + VRAM** with a few GB to spare. If it does not, use a smaller
quantization (e.g. Q3 instead of Q4).

Thinking models write their reasoning first. Tick **Reasoning** in the panel to watch it. For faster but
shallower answers, lower the **Lookups per question** in AI Settings.

---

## 5. Using the assistant

* **Ask in the panel**, for example *"Where is `mass` modified, and in which order?"*, *"Explain
  `computeMassMatrixCRB`"*, *"Which functions are the most complex and why?"*,
  *"What would break if I removed `WorldT::replaceModel`?"*
* **From the code:** right-click an entity, or select code in the editor, and choose **Ask AI About
  This** or **Ask AI About Selection**.
* **Follow-up questions** keep the conversation. **New Chat** starts over.
* The *Looked up* line shows which tools the model used. Click a `file:line` link to open the code.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| *Cannot reach the AI server* | Start the server (`start-ai-server.bat`) and wait until its window says it is listening; check the address in AI Settings. |
| *The AI server does not support tool calling* | Start llama-server with `--jinja`. Without it CodeScope still answers, but only from code it looks up itself for names in your question. |
| Server window closes or shows `out of memory` | Raise `--n-cpu-moe` (or lower `-ngl`), or use a smaller `-c`. |
| `unknown model architecture` | The llama.cpp build is older than the model: run the setup again to download the latest llama.cpp. |
| Very slow answers | Check that the GPU is used (`-ngl` not 0, the CUDA/Vulkan build); lower `--n-cpu-moe`. |
| Answers ignore parts of the code | Increase the context (`-c 65536`) if you have the memory. |

Settings are stored in `%APPDATA%\CodeScope\ai.json`.
