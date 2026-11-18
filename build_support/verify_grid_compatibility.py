"""Read-only image replay: no desktop capture, windows or mouse input."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image
from recognition import PieceRecognizer
from grid_geometry import audit_grid


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--image',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--require-grid',action='store_true')
    parser.add_argument('--desktop',action='store_true',help='Embed screenshots into synthetic 2560x1440 frames')
    args=parser.parse_args()
    recognizer=PieceRecognizer()
    rows=[]
    for path in args.image:
        original=Image.open(path).convert('RGB')
        baseline=None
        variants=[('original',original),('rotated',original.transpose(Image.Transpose.ROTATE_180)),
                  ('scaled',original.resize((round(original.width*.7),round(original.height*.7))))]
        if args.desktop:
            variants=[]
            for x in (0,960,1700):
                canvas=Image.new('RGB',(2560,1440),(40,40,40))
                if x+original.width>2560 or original.height+24>1440:
                    raise ValueError('Screenshot does not fit synthetic desktop')
                canvas.paste(original,(x,24))
                variants.append((f'desktop-{x}',canvas))
        for name,image in variants:
            start=time.monotonic()
            try:
                _,detections=recognizer.recognize(image,require_grid=args.require_grid,
                    minimum_geometry_confidence=.1 if args.require_grid else 0.,
                    cancelled=lambda:False)
                board={d.square:d.piece for d in detections if d.piece}
                if baseline is None:
                    baseline=board
                audit=audit_grid(image,recognizer.last_geometry,board)
                rows.append(dict(image=path.name,variant=name,pieces=len(board),same_board=board==baseline,
                                 audit=audit.metadata(),stages=dict(recognizer.neural.last_timings),
                                 elapsed_ms=round((time.monotonic()-start)*1000)))
            except Exception as exc:
                rows.append(dict(image=path.name,variant=name,error=str(exc)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(rows,ensure_ascii=False))


if __name__=='__main__':
    main()
