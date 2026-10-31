# Modelle für snapmem

| Datei | Zweck | Quelle | Größe | im Git |
|---|---|---|---|---|
| face_detection_yunet_2023mar.onnx | Gesichter | OpenCV Zoo | 0,2 MB | ja |
| yolox.onnx | ganze Personen (auch von hinten) | OpenCV Zoo `object_detection_yolox_2022nov_int8.onnx` (Apache-2.0), sha256 01a3b0f4… | 8,7 MB | ja |
| clip_vision_q.onnx | Bildmerkmale für „eingebrannt/Linse“ | Hugging Face `Xenova/clip-vit-base-patch32`, `onnx/vision_model_quantized.onnx` (OpenAI CLIP, MIT), sha256 583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299 | 85 MB | **nein** (.gitignore) |
| einbrand.npz | Gewichte des Erkenners (aus training/einbrand_trainieren.py) | selbst trainiert | klein | ja |

`clip_vision_q.onnx` neu holen:
`curl -L -o models/clip_vision_q.onnx https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model_quantized.onnx`
