"""Explicit, source-preserving geometry for photographed label preparation."""
import math


def perspective_coefficients(quad, size, bounds):
    """Map output pixels into a convex source quad ordered TL, TR, BR, BL."""
    if len(quad) != 8 or any(not math.isfinite(x) for x in quad):
        raise ValueError('Quad requires eight finite coordinates')
    if len(size) != 2 or any(not isinstance(x, int) or x < 1 or x > 8192 for x in size):
        raise ValueError('Rectified size must be two integers in [1, 8192]')
    points = list(zip(quad[::2], quad[1::2]))
    if any(not (0 <= x <= bounds[0] and 0 <= y <= bounds[1]) for x, y in points):
        raise ValueError('Quad lies outside source image')
    cross = []
    for i in range(4):
        a, b, c = points[i], points[(i+1)%4], points[(i+2)%4]
        cross.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
    if not all(x > 1e-6 for x in cross):
        raise ValueError('Quad must be convex and ordered TL, TR, BR, BL')
    width, height = size
    rows = []
    for (u, v), (x, y) in zip([(0,0),(width,0),(width,height),(0,height)], points):
        rows.extend([[u,v,1,0,0,0,-x*u,-x*v,x], [0,0,0,u,v,1,-y*u,-y*v,y]])
    for column in range(8):
        pivot = max(range(column,8), key=lambda i: abs(rows[i][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        if abs(divisor) < 1e-12:
            raise ValueError('Degenerate perspective quad')
        rows[column] = [x/divisor for x in rows[column]]
        for i in range(8):
            if i != column:
                factor = rows[i][column]
                rows[i] = [x-factor*y for x,y in zip(rows[i], rows[column])]
    return tuple(row[-1] for row in rows)
