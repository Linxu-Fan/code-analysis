import struct

from codescope.core import ai_setup as S

GIB = 1024 ** 3


def write_gguf(path, meta, pad=0):
    """A minimal GGUF v3 file with the given metadata (and *pad* bytes of fake tensor data)."""
    def s(text):
        b = text.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(meta))
    for key, value in meta.items():
        out += s(key)
        if isinstance(value, str):
            out += struct.pack("<I", 8) + s(value)
        elif isinstance(value, bool):
            out += struct.pack("<IB", 7, value)
        elif isinstance(value, int):
            out += struct.pack("<II", 4, value)
        elif isinstance(value, float):
            out += struct.pack("<If", 6, value)
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            out += struct.pack("<IIQ", 9, 8, len(value)) + b"".join(s(v) for v in value)
        elif isinstance(value, list):
            out += struct.pack("<IIQ", 9, 5, len(value)) + b"".join(struct.pack("<i", v) for v in value)
    with open(path, "wb") as fh:
        fh.write(out + b"\0" * pad)


def test_gguf_info(tmp_path):
    p = tmp_path / "m.gguf"
    write_gguf(p, {"general.architecture": "qwen3moe", "general.name": "Qwen Test",
                   "tokenizer.ggml.tokens": [f"tok{i}" for i in range(5000)],
                   "tokenizer.ggml.token_type": list(range(5000)),
                   "qwen3moe.block_count": 40, "qwen3moe.expert_count": 256, "qwen3moe.expert_used_count": 8,
                   "qwen3moe.context_length": 262144, "general.flag": True, "general.temp": 0.5})
    info = S.gguf_info(str(p))
    assert (info["architecture"], info["name"], info["block_count"], info["expert_count"],
            info["expert_used_count"], info["context_length"]) == ("qwen3moe", "Qwen Test", 40, 256, 8, 262144)
    bad = tmp_path / "x.gguf"
    bad.write_bytes(b"nope")
    try:
        S.gguf_info(str(bad))
        raise AssertionError("expected an error")
    except ValueError as exc:
        assert "not a GGUF" in str(exc)


RELEASE = {"tag_name": "b7000", "assets": [{"name": n, "browser_download_url": "https://x/" + n} for n in (
    "llama-b7000-bin-win-cuda-12.4-x64.zip", "llama-b7000-bin-win-cuda-13.1-x64.zip",
    "cudart-llama-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-13.1-x64.zip",
    "llama-b7000-bin-win-vulkan-x64.zip", "llama-b7000-bin-win-cpu-x64.zip", "llama-b7000-bin-win-cpu-arm64.zip",
    "llama-b7000-bin-ubuntu-x64.zip")]}


def test_pick_assets():
    names = lambda a: [n for n, _ in a]  # noqa: E731
    assert names(S.pick_assets(RELEASE, "cuda", "12.8")) == ["llama-b7000-bin-win-cuda-12.4-x64.zip",
                                                            "cudart-llama-bin-win-cuda-12.4-x64.zip"]
    assert names(S.pick_assets(RELEASE, "cuda", "13.1"))[0].endswith("cuda-13.1-x64.zip")
    assert names(S.pick_assets(RELEASE, "cuda", ""))[0].endswith("cuda-13.1-x64.zip")
    assert names(S.pick_assets(RELEASE, "vulkan")) == ["llama-b7000-bin-win-vulkan-x64.zip"]
    assert names(S.pick_assets(RELEASE, "cpu")) == ["llama-b7000-bin-win-cpu-x64.zip"]
    assert S.pick_assets({"assets": []}, "cuda") == []


def test_plan():
    info = {"name": "Qwen", "architecture": "qwen3moe", "size": int(22 * GIB), "block_count": 40,
            "expert_count": 256, "expert_used_count": 8, "context_length": 262144}
    hw = {"gpus": [{"name": "RTX", "vram": 12 * GIB, "vendor": "nvidia"}], "cuda": "12.8", "ram": 64 * GIB}
    args, notes = S.plan(info, hw, "cuda")
    assert args[:3] == ["--jinja", "-c", "32768"] and "-ngl" in args and "--n-cpu-moe" in args
    n = int(args[args.index("--n-cpu-moe") + 1])
    assert 0 < n < 40                                  # part of the experts on the GPU
    big = dict(hw, gpus=[{"name": "RTX", "vram": 32 * GIB, "vendor": "nvidia"}])
    args, _ = S.plan(info, big, "cuda")
    assert args[-2:] == ["-ngl", "99"] and "--n-cpu-moe" not in args
    args, notes = S.plan(info, dict(hw, gpus=[], ram=16 * GIB), "cpu")
    assert args[-2:] == ["-ngl", "0"] and any("Warning" in x for x in notes)
    dense = dict(info, expert_count=0)
    args, _ = S.plan(dense, hw, "cuda")
    assert int(args[args.index("-ngl") + 1]) < 40


def test_start_script_and_cli(tmp_path, monkeypatch, capsys):
    from codescope.cli import main
    monkeypatch.setenv("CODESCOPE_CONFIG_DIR", str(tmp_path / "cfg"))
    model = tmp_path / "My Models" / "m.gguf"
    model.parent.mkdir()
    write_gguf(model, {"general.architecture": "llama", "llama.block_count": 8}, pad=1024)
    server = tmp_path / "llama-server"
    server.write_text("")
    monkeypatch.setattr(S, "detect_hardware", lambda: {"gpus": [], "cuda": "", "ram": 32 * GIB})
    assert main(["ai-setup", str(model), "--server", str(server), "--port", "8181"]) in (0, None)
    out = capsys.readouterr().out
    assert "GPU: none detected" in out and "Start script:" in out
    script = next((tmp_path / "cfg").glob("start-ai-server.*"))
    text = script.read_text()
    assert f'"{model}"' in text and "--jinja" in text and "--port 8181" in text
    from codescope.core import assistant as A
    assert A.load_settings()["url"] == "http://127.0.0.1:8181/v1"


def test_cli_dry_run(tmp_path, monkeypatch, capsys):
    from codescope.cli import main
    monkeypatch.setenv("CODESCOPE_CONFIG_DIR", str(tmp_path / "cfg"))
    model = tmp_path / "m.gguf"
    write_gguf(model, {"general.architecture": "qwen3moe", "qwen3moe.block_count": 40,
                       "qwen3moe.expert_count": 128}, pad=1024)
    monkeypatch.setattr(S, "detect_hardware", lambda: {"gpus": [{"name": "RTX", "vram": 8 * GIB, "vendor": "nvidia"}],
                                                       "cuda": "12.8", "ram": 32 * GIB})
    monkeypatch.setattr(S, "fetch_release", lambda: RELEASE)
    assert main(["ai-setup", str(model), "--dry-run"]) in (0, None)
    out = capsys.readouterr().out
    assert "llama-b7000-bin-win-cuda-12.4-x64.zip, cudart-llama-bin-win-cuda-12.4-x64.zip" in out
    assert "llama-server -m" in out and "--jinja" in out
    assert not (tmp_path / "cfg").exists()                 # nothing written
