"""Platform-independent visual evidence for a clickable 9x10 lattice.

This validates geometry, not chess rules. No window titles, colours, or screen
coordinates are assumed. A model's confidence alone is never click evidence.
"""
from dataclasses import dataclass, asdict
import math

import cv2
import numpy as np


def refine_grid_lines(image, geometry, diagnostics=None):
    """Fit evenly spaced line families before classifying any pieces.

    The pose is only a search proposal. This works with incomplete back ranks
    and non-circular pieces; a weak or ambiguous lattice leaves it unchanged.
    """
    from dataclasses import replace
    if audit_grid(image, geometry, {}).safe:
        return geometry
    matrix = np.array(geometry.inverse_matrix).reshape(3,3)
    view = cv2.warpPerspective(np.asarray(image.convert('RGB')), np.linalg.inv(matrix), (450,500))
    gray = cv2.cvtColor(view,cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(2.,(8,8)).apply(gray)
    gray = cv2.GaussianBlur(gray,(3,3),.8)
    lines = cv2.HoughLinesP(cv2.Canny(gray,15,45),1,np.pi/720,20,minLineLength=45,maxLineGap=10)
    if lines is None:
        return geometry
    families = [[],[]]
    for x1,y1,x2,y2 in lines.reshape(-1,4):
        dx,dy=float(x2-x1),float(y2-y1)
        if abs(dy) < abs(dx)*.25:
            slope=dy/dx
            families[1].append((y1+slope*(225-x1),slope))
        elif abs(dx) < abs(dy)*.25:
            slope=dx/dy
            families[0].append((x1+slope*(250-y1),slope))

    def fit(lines,count,nominal):
        # Merge both edges of a thin grid stroke before measuring support.
        if not lines:
            return None
        slopes=np.array([p[1] for p in lines])
        counts=[int((np.abs(slopes-s)<.04).sum()) for s in slopes]
        dominant=slopes[int(np.argmax(counts))]
        lines=[p for p in lines if abs(p[1]-dominant)<.065]
        groups=[]
        for value,slope in sorted(lines):
            if groups and value-np.mean([p[0] for p in groups[-1]])<4:
                groups[-1].append((value,slope))
            else:
                groups.append([(value,slope)])
        positions=np.array([np.mean([p[0] for p in group]) for group in groups])
        if len(positions)<count-3 or len(positions)>count*3:
            return None
        best=None
        for a in positions:
            for b in positions:
                for gap in range(1,count):
                    step=(b-a)/gap
                    if not nominal*.75<step<nominal*1.2:
                        continue
                    for start_index in range(count-gap):
                        origin=a-start_index*step
                        if not 15<origin<85 or not 330<origin+(count-1)*step<480:
                            continue
                        predicted=origin+np.arange(count)*step
                        distances=np.abs(predicted[:,None]-positions[None,:])
                        residuals=distances.min(axis=1)
                        hits=residuals<4
                        quality=int(hits.sum())*10-float(np.minimum(residuals,8).sum())
                        if best is None or quality>best[0]:
                            best=(quality,predicted,distances.argmin(axis=1),hits)
        if best is None or int(best[3].sum())<count-2:
            return None
        _,predicted,indices,hits=best
        index=np.arange(count)[hits]
        slope,origin=np.polyfit(index,positions[indices[hits]],1)
        # Fit each family's tilt from its observed long segments.
        tilts=np.array([np.median([p[1] for p in groups[j]]) for j in indices[hits]])
        tilt_step,tilt_origin=np.polyfit(index,tilts,1)
        return [(origin+i*slope,tilt_origin+i*tilt_step) for i in (0,count-1)]

    vertical,horizontal=fit(families[0],9,350/8),fit(families[1],10,400/9)
    if diagnostics is not None:
        diagnostics.update(vertical=vertical, horizontal=horizontal, families=families)
    if vertical is None or horizontal is None:
        return geometry
    corners=[]
    for y,hs in horizontal:
        for x,vs in vertical:
            # x = x_mid + vs*(y-250), y = y_mid + hs*(x-225)
            point=np.linalg.solve([[1,-vs],[-hs,1]],[x-vs*250,y-hs*225])
            corners.append(point)
    corners=np.float32(corners)
    canonical=np.float32([[50,50],[400,50],[50,450],[400,450]])
    if np.linalg.norm(corners-canonical,axis=1).max()>65:
        return geometry
    correction=cv2.getPerspectiveTransform(canonical,corners)
    result=matrix@correction
    proposed = replace(geometry,inverse_matrix=tuple(result.reshape(-1)),piece_centers=())
    # Independent post-fit check: texture/ornaments must not become a lattice.
    return proposed if audit_grid(image, proposed, {}).safe else geometry


@dataclass(frozen=True)
class GridAudit:
    safe: bool
    reason: str
    vertical: int = 0
    horizontal: int = 0
    anchors: int = 0
    anchor_fraction: float = 0.0

    def metadata(self):
        return asdict(self)


def audit_grid(image, geometry, board, source=None):
    if geometry is None or geometry.image_size != image.size:
        return GridAudit(False, "画面尺寸与棋盘坐标不一致")
    points = np.array([geometry.point_for_square((x, y)) for y in range(10) for x in range(9)])
    if not np.isfinite(points).all():
        return GridAudit(False, "棋盘映射包含无效坐标")
    w, h = image.size
    if (points[:, 0] < 1).any() or (points[:, 0] >= w-1).any() or (points[:, 1] < 1).any() or (points[:, 1] >= h-1).any():
        return GridAudit(False, "棋盘交叉点超出画面")
    steps = np.linalg.norm(np.diff(points.reshape(10, 9, 2), axis=1), axis=2)
    if steps.min() < 12 or steps.max() / steps.min() > 2:
        return GridAudit(False, "棋盘网格尺度异常")
    matrix = np.array(geometry.inverse_matrix).reshape(3, 3)
    if abs(np.linalg.det(matrix)) < 1e-9:
        return GridAudit(False, "棋盘透视矩阵退化")
    normalized = cv2.warpPerspective(np.asarray(image.convert("RGB")), np.linalg.inv(matrix), (450, 500))
    gray = cv2.cvtColor(normalized, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).apply(gray)
    gray = cv2.GaussianBlur(gray,(3,3),.8)
    edges = cv2.Canny(gray, 15, 45)
    # Exclude occupied discs: glyph strokes must not masquerade as grid lines.
    for x, rank in board:
        col, row = (8-x, rank) if geometry.rotated else (x, 9-rank)
        cv2.circle(edges, (round(50+col*350/8), round(50+row*400/9)), 17, 0, -1)
    segments = cv2.HoughLinesP(edges, 1, np.pi/180, 18, minLineLength=26, maxLineGap=5)
    vertical, horizontal = set(), set()
    for x1,y1,x2,y2 in ([] if segments is None else segments.reshape(-1,4)):
        if abs(x2-x1) <= 3 and 40 <= min(y1,y2) and max(y1,y2) <= 460:
            col = round(((x1+x2)/2-50)/(350/8))
            if 0 <= col <= 8 and abs((x1+x2)/2-(50+col*350/8)) <= 5:
                vertical.add(col)
        if abs(y2-y1) <= 3 and 40 <= min(x1,x2) and max(x1,x2) <= 410:
            row = round(((y1+y2)/2-50)/(400/9))
            if 0 <= row <= 9 and abs((y1+y2)/2-(50+row*400/9)) <= 5:
                horizontal.add(row)
    # Independent observed disc centres are useful for themes with faint lines.
    matched = set()
    for x, rank, px, py in geometry.piece_centers:
        if (x,rank) in board and math.dist(geometry.point_for_square((x,rank)), (px,py)) <= float(np.median(steps))*.18:
            matched.add((x,rank))
    fraction = len(matched)/max(1,len(board))
    distributed = (len({x for x,_ in matched}) >= 4 and len({y for _,y in matched}) >= 4
                   and any(y<=2 for _,y in matched) and any(y>=7 for _,y in matched))
    lines_ok = len(vertical)>=6 and len(horizontal)>=7
    anchors_ok = len(matched)>=10 and fraction>=.75 and distributed
    safe = lines_ok or anchors_ok
    reason = "网格校验通过" if safe else "棋盘线/棋子圆心与网格不吻合，禁止点击"
    # Circle fitting is supporting evidence, not a mandatory per-piece gate.
    # Selection glows and glyph artwork can hide one disc while the complete
    # lattice, the decoded position and nearly every other disc still agree.
    # In that case point_for_piece() deliberately falls back to the verified
    # intersection.  Keep rejecting a missing source when the global evidence
    # is weaker, so this relaxation cannot authorize a stale or skewed board.
    if source is not None and len(matched)>=6 and source not in matched:
        complete_lattice = len(vertical) >= 8 and len(horizontal) >= 9
        broad_anchor_support = anchors_ok and fraction >= .80
        source_is_decoded = source in board
        source_fallback_safe = complete_lattice and broad_anchor_support and source_is_decoded
        safe = safe and source_fallback_safe
        if safe:
            reason = "完整网格与全盘棋子一致；起点圆心漏检，使用网格交点"
        else:
            reason = "起点棋子圆心缺失且全盘证据不足，禁止点击"
    return GridAudit(safe, reason,
                     len(vertical), len(horizontal), len(matched), fraction)
