"""Independent MediaPipe CPU pose module for RK3576 (no camera, no NPU, no YOLO conversion).

Deploy directory layout (nothing here touches the YOLO environment or the system Python):

    pose/            this package
    bin/             the four steps
    wheels/          local ARM64 wheels (manylinux, glibc)
    models/          pose_landmarker_full.task (not shipped, verified by sha256)
    samples/         one real JPEG + its detection box + expected outputs
    logs/            step logs written by the scripts
"""
__version__ = '0.6.0'
SCHEMA = 'qjzc.pose.panel.v1'