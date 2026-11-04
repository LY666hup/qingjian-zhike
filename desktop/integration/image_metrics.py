"""Receiver preview statistics, not a filter, FPGA timing or quality ground truth."""
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

def preview_metrics(image):
    """Sample to identical 160x90 grid; encoded RGB luma approximation 0..255.

    Mean, population standard deviation and mean absolute horizontal/vertical
    neighbour gradient. No normalization against another frame, no PSNR/SSIM.
    Values describe decoded preview content, including codec/range/scale effects.
    """
    if image.isNull():return None
    sample=image.scaled(160,90,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.FastTransformation).convertToFormat(QImage.Format.Format_RGB888)
    rgb=np.frombuffer(sample.constBits(),np.uint8,count=sample.sizeInBytes()).reshape(90,sample.bytesPerLine())[:,:480].reshape(90,160,3).astype(np.int32)
    y=(77*rgb[:,:,0]+150*rgb[:,:,1]+29*rgb[:,:,2]+128)>>8
    edges=(np.abs(np.diff(y,axis=0)).sum()+np.abs(np.diff(y,axis=1)).sum())/((90-1)*160+90*(160-1))
    return dict(mean=float(y.mean()),contrast=float(y.std()),edge=float(edges),sample_width=160,sample_height=90)
