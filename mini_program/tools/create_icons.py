"""Project-native line icons, supersampled for native Mini Program tab bars."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / 'assets' / 'icons'
root.mkdir(parents=True, exist_ok=True)
def draw_icon(name, color, suffix):
    im = Image.new('RGBA', (288, 288))
    d = ImageDraw.Draw(im)
    def line(points):
        d.line([(x*12,y*12) for x,y in points], fill=color, width=17, joint='curve')
    def box(bounds, radius=2):
        d.rounded_rectangle(tuple(v*12 for v in bounds), radius=radius*12, outline=color, width=17)
    if name == 'home':
        line([(3,10),(12,3),(21,10)])
        line([(5,9),(5,21),(10,21),(10,15),(14,15),(14,21),(19,21),(19,9)])
    elif name == 'classroom':
        box((3,3,21,17)); line([(8,21),(16,21)]); line([(12,17),(12,21)])
        line([(7,12),(10,9),(13,12),(17,7)])
    elif name == 'history':
        box((5,3,20,21)); line([(9,8),(16,8)]); line([(9,12),(16,12)]); line([(9,16),(13,16)])
        line([(2,7),(5,7)]);line([(2,12),(5,12)]);line([(2,17),(5,17)])
    elif name == 'system':
        box((6,6,18,18)); box((10,10,14,14),0)
        for k in [9,15]:
            line([(k,2),(k,6)]); line([(k,18),(k,22)]);line([(2,k),(6,k)]);line([(18,k),(22,k)])
    elif name == 'students':
        d.ellipse((8*12,3*12,16*12,11*12),outline=color,width=17)
        d.arc((4*12,13*12,20*12,26*12),180,360,fill=color,width=17)
        line([(4,19),(4,21),(20,21),(20,19)])
    elif name == 'arrow':
        line([(4,12),(20,12)]);line([(14,6),(20,12),(14,18)])
    im.resize((72,72),Image.Resampling.LANCZOS).save(root/(name+suffix+'.png'))
for name in ['home','classroom','history','system','students','arrow']:
    draw_icon(name,'#8c9a9e','')
    draw_icon(name,'#137c70','-active')
print('Generated 12 local PNG icons')
