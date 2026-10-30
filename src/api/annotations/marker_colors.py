"""Native equivalent of the editor's recommended marker colour picker."""
import colorsys
import math


def rgb(color):
    return tuple(int(color[i:i+2],16) for i in (1,3,5))


def contrast(a,b):
    def luminance(color):
        channels=[v/255 for v in rgb(color)]
        linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
    x,y=sorted((luminance(a),luminance(b)))
    return (y+.05)/(x+.05)


def recommend_marker_color(colors):
    used=[color.lower() for color in colors];taken=set(used)
    best=None;best_distance=-1
    for step in range(900):
        n=len(used)+step
        channels=colorsys.hls_to_rgb(((31+n*137.507764)%360)/360,(45+(n*7)%20)/100,(58+(n*13)%23)/100)
        candidate='#'+''.join(f'{math.floor(channel*255+.5):02x}' for channel in channels)
        if candidate in taken:continue
        if contrast(candidate,'#ffffff')<1.32 or contrast(candidate,'#222322')<1.32:continue
        if max(contrast(candidate,'#202020'),contrast(candidate,'#ececea'))<4.5:continue
        distance=min((math.dist(rgb(candidate),rgb(color)) for color in used),default=math.inf)
        if distance>best_distance:best,best_distance=candidate,distance
        if distance>=48:return candidate
    # Crowded projects cannot have infinitely distinguishable colours. Prefer the
    # most distinct readable candidate, but never reuse an exact colour.
    if best:return best
    for value in range(len(taken)+1):
        candidate=f'#{value:06x}'
        if candidate not in taken:return candidate
    raise ValueError('No unused marker colour')
