"""Download the Visual ID face models at image build time and check their SHA-256."""
import hashlib
import os
import sys
import urllib.request

ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MODELS = [
    ("face_detection_yunet/", "face_detection_yunet_2023mar.onnx",
     "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("face_recognition_sface/", "face_recognition_sface_2021dec.onnx",
     "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
]
dest = sys.argv[1] if len(sys.argv) > 1 else "/app/models"
os.makedirs(dest, exist_ok=True)
for folder, name, sha in MODELS:
    data = urllib.request.urlopen(ZOO + folder + name, timeout=300).read()
    if hashlib.sha256(data).hexdigest() != sha:
        sys.exit(f"{name}: checksum mismatch ({len(data)} bytes)")
    with open(os.path.join(dest, name), "wb") as f:
        f.write(data)
    print(f"{name}: ok ({len(data)} bytes)")
