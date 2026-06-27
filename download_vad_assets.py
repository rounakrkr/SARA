import urllib.request, os

DEST = "static/vad"
os.makedirs(DEST, exist_ok=True)

VAD_VERSION = "0.0.22"
ORT_VERSION = "1.14.0"

ASSETS = [
    (f"https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{VAD_VERSION}/dist/silero_vad_legacy.onnx", "silero_vad_legacy.onnx"),
    (f"https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{VAD_VERSION}/dist/vad.worklet.bundle.min.js", "vad.worklet.bundle.min.js"),
    (f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ORT_VERSION}/dist/ort-wasm-simd.wasm", "ort-wasm-simd.wasm"),
    (f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ORT_VERSION}/dist/ort-wasm.wasm", "ort-wasm.wasm"),
]

for url, fname in ASSETS:
    path = os.path.join(DEST, fname)
    print(f"Downloading {fname}...", end=" ", flush=True)
    urllib.request.urlretrieve(url, path)
    print(f"{os.path.getsize(path):,} bytes OK")

print("All done!")
