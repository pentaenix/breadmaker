"""Rasterized Canvas dots stay visible across themes, zoom and navigation."""
from io import BytesIO
from PIL import Image, ImageChops
from tests.test_v10569_narration_browser import browser
from tests.test_v10581_canvas_navigation_browser import _board


def sample(page):
    image=Image.open(BytesIO(page.locator('#canvasStage').screenshot())).convert('RGB')
    # The fixture's nearest card starts beyond this empty board patch at all
    # tested zooms. Exclude the stage border; count real pixels, not CSS text.
    patch=image.crop((12,12,120,120))
    colors=patch.getcolors(108*108);background=max(colors,key=lambda item:item[0])[1]
    return sum(count for count,color in colors if max(abs(a-b) for a,b in zip(color,background))>=10)


def exercise(home,local,engine,theme):
    with browser(home,local=local,engine=engine,page_options={'viewport':{'width':1200,'height':844},'device_scale_factor':1}) as (_,page,_):
        ids,errors=_board(page)
        page.evaluate('(theme)=>document.documentElement.dataset.theme=theme',theme)
        counts=[]
        for zoom in (.5,1,1.75):
            page.evaluate('(zoom)=>{applyCanvasZoom(zoom,{persist:false});const s=document.querySelector("#canvasStage");s.scrollLeft=0;s.scrollTop=0}',zoom)
            counts.append(sample(page))
        assert min(counts)>70,(theme,engine,counts)
        # Navigation remains a viewport operation; it does not alter the cards.
        before=page.evaluate('()=>state.canvasNodes.map(n=>[n.id,n.x,n.y])')
        stage=page.locator('#canvasStage');box=stage.bounding_box()
        page.keyboard.down('Space');page.mouse.move(box['x']+100,box['y']+100);page.mouse.down();page.mouse.move(box['x']+30,box['y']+30,steps=5);page.mouse.up();page.keyboard.up('Space')
        assert stage.evaluate('el=>el.scrollLeft')>0
        assert page.evaluate('()=>state.canvasNodes.map(n=>[n.id,n.x,n.y])')==before
        assert not errors,errors


def test_canvas_dots_light_chromium_at_pixel_ratio_one(tmp_path):exercise(tmp_path,True,'chromium','light')
def test_canvas_dots_dusk_hosted_chromium_at_pixel_ratio_one(tmp_path):exercise(tmp_path,False,'chromium','dusk')
def test_canvas_dots_dark_hosted_webkit_at_pixel_ratio_one(tmp_path):exercise(tmp_path,False,'webkit','dark')


def _empty_patch(page):
    image=Image.open(BytesIO(page.locator('#canvasStage').screenshot())).convert('RGB')
    return image.crop((12,12,120,120))


def _ink(image):
    colors=image.getcolors(image.width*image.height)
    background=max(colors,key=lambda item:item[0])[1]
    return sum(count*max(abs(a-b) for a,b in zip(color,background))
               for count,color in colors)


def test_canvas_dots_retain_original_retina_weight_and_quiet_standard_density(tmp_path):
    for density in (1,2):
        with browser(tmp_path,local=True,engine='chromium',page_options={
            'viewport':{'width':1200,'height':844},'device_scale_factor':density}) as (_,page,_):
            _board(page)
            stage=page.locator('#canvasStage')
            actual=_empty_patch(page)
            if density==1:
                stage.evaluate("el=>el.style.backgroundImage='radial-gradient(var(--line2) 1px,transparent 1px)'")
                strong=_empty_patch(page)
                assert 0<_ink(actual)<.75*_ink(strong),(_ink(actual),_ink(strong))
            else:
                stage.evaluate("el=>el.style.backgroundImage='radial-gradient(var(--line2) .7px,transparent .7px)'")
                original=_empty_patch(page)
                assert ImageChops.difference(actual,original).getbbox() is None
