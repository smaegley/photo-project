"""Rebuild app/junk_prompts.npz — the text side of the junk classifier (SPEC §15.15 slice 5).

Dev-only, run by hand when the prompt lists change. Needs the CLIP ViT-B/32 *text*
model and tokenizer plus the `tokenizers` package, none of which ship in any image:
the nightly job only needs the vision model and this small file of text embeddings.

    PYTHONPATH=/mnt/photos/ingest-probe/pylib ../.venv/bin/python tools/build_junk_prompts.py \
        /mnt/photos/ingest-probe/models
"""
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

KEEP = ["a photo of people", "a family photo", "a portrait of a person", "a selfie",
        "a photo of children", "a group photo", "a landscape photo",
        "a photo of a river or lake", "a photo of mountains", "a vacation photo",
        "a photo of a city street", "a photo of a building", "a photo of people doing sports",
        "a photo of a pet", "a photo of a dog", "a photo of food at a restaurant",
        "a photo of a party", "a photo of the beach", "a photo of animals",
        "a photo of a concert or event", "a photo of flowers or a garden"]
JUNK = ["a photo of a shipping label", "a photo of a package or cardboard box",
        "a photo of a receipt", "a photo of a document or printed page",
        "a screenshot of a phone screen", "a photo of a product label or barcode",
        "a photo of a serial number or nameplate", "a photo of a computer screen",
        "a photo of handwritten notes", "a photo of a store shelf with products",
        "a photo of a price tag", "a photo of an appliance or tool",
        "a photo of a parking spot or car dashboard", "a photo of home repair or plumbing",
        "a photo of furniture for sale", "a photo of a sign with text",
        "a photo of a whiteboard", "a photo of a medicine bottle"]

src = Path(sys.argv[1])
tok = Tokenizer.from_file(str(src / "tokenizer.json"))
tok.enable_padding(length=77, pad_id=49407)
tok.enable_truncation(77)
sess = ort.InferenceSession(str(src / "text_model.onnx"), providers=["CPUExecutionProvider"])
enc = tok.encode_batch(KEEP + JUNK)
feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64)}
if "attention_mask" in {i.name for i in sess.get_inputs()}:
    feed["attention_mask"] = np.array([e.attention_mask for e in enc], dtype=np.int64)
T = sess.run(None, feed)[0].astype(np.float32)
T /= np.linalg.norm(T, axis=1, keepdims=True)
out = Path(__file__).resolve().parents[1] / "app" / "junk_prompts.npz"
np.savez_compressed(out, text=T, n_keep=len(KEEP), prompts=np.array(KEEP + JUNK))
print(f"wrote {out}: {T.shape}, {len(KEEP)} keep + {len(JUNK)} junk")
