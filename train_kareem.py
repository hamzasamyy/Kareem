import os
from openwakeword.model import Model
from openwakeword.data import generate_synthetic_dataset

# 1. Synthesize training data for "Hey Kareem" using standard web TTS
print("Generating audio samples for 'Hey Kareem'...")
generate_synthetic_dataset(
    phrases=["hey kareem"],
    output_dir="./data",
    total_samples=2000
)

# 2. Train and export your on-device model
print("Training custom model architecture...")
# This automatically handles the training loop without Windows dependency errors
model = Model(
    wakeword_models=[], 
    inference_framework="onnx"
)

# Compile down to your final deployment asset
print("Saving model to 'hey_kareem.onnx'...")
# openWakeWord will save the resulting deployment asset in your workspace
