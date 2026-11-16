"""使用几何坐标选择矩形边界，不通过额外求解推断边界编号。"""
import math


def rectangle_boundary_map(component, width: float, height: float):
    if not all(math.isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError('Rectangle extents must be finite and positive')
    tolerance = min(width, height) * 1e-6
    boxes = {
        'left': (-tolerance, tolerance, -tolerance, height+tolerance),
        'right': (width-tolerance, width+tolerance, -tolerance, height+tolerance),
        'bottom': (-tolerance, width+tolerance, -tolerance, tolerance),
        'top': (-tolerance, width+tolerance, height-tolerance, height+tolerance),
    }
    mapping = {}
    for side, bounds in boxes.items():
        selection = component.selection().create('wb_' + side, 'Box')
        selection.geom('geom1', 1)
        selection.set('condition', 'inside')
        # Box 坐标使用 geometry 的长度单位，不能暗自改成 SI 数值。
        for name, value in zip(('xmin','xmax','ymin','ymax'), bounds):
            selection.set(name, value)
        entities = [int(entity) for entity in selection.entities()]
        if len(entities) != 1 or entities[0] <= 0:
            raise ValueError(f'Expected one coordinate-selected {side} edge')
        mapping[side] = entities[0]
    if len(set(mapping.values())) != 4:
        raise ValueError('Rectangle boundary selections overlap')
    return mapping
