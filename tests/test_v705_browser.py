from __future__ import annotations
import requests
from test_v41812_browser import scene_live_server, scene_browser, _new_project, _page, _shutdown_page


def _release(base):
    try: requests.post(base+'/api/lease/release',headers={'X-Breadmaker-Client':'v41812-browser'},timeout=2)
    except Exception: pass


def _switch_spanish(page):
    page.locator('#brandButton').click()
    page.locator('#projectMenuLanguage').click()
    page.locator('#languageSearch').fill('espa')
    page.locator('[data-language-code="es"]').click()
    page.wait_for_timeout(150)
    assert page.evaluate('document.documentElement.lang')=='es'


def test_v705_dynamic_defaults_errors_and_locale_helpers_in_spanish(scene_live_server,scene_browser):
    base=scene_live_server
    _new_project(base,'Localization Hardening 7.0.5')
    page,errors=_page(base,scene_browser)
    try:
        page.wait_for_timeout(300)
        _switch_spanish(page)

        # Runtime accessibility text is interpolated in the selected Breadmaker locale.
        aria=page.locator('.document-more').first.get_attribute('aria-label')
        assert aria and aria.startswith('Acciones de la pestaña ')

        # Generated defaults are localized when the object is created, not renamed later.
        page.evaluate('createTab()')
        assert page.locator('#newDocumentName').input_value()=='Notas'
        page.locator('#modalClose').click()
        scene=page.evaluate("""async()=>api('/api/scenes',{method:'POST',body:JSON.stringify({tab_id:state.activeDocumentId,default_title_prefix:i18nSource('Scene')})})""")
        assert scene['title'].startswith('Escena ')

        # Representative dynamic/interpolated surfaces.
        values=page.evaluate("""()=>({
          objects:i18nText('canvas.objects_selected',{count:i18nNumber(3)}),
          connectors:i18nText('canvas.connectors_selected',{count:i18nNumber(2)}),
          heading:i18nText('import.heading',{level:i18nNumber(1)}),
          attach:i18nText('map.attach_start',{title:'Acto I'}),
          linked:i18nText('links.map_named',{name:'Trama'}),
          sync:i18nText('map.line_synced_changes',{added:i18nNumber(2),removed:i18nNumber(1)}),
          err:breadmakerErrorFromPayload({detail:{code:'MAP_021',message:'A junction must connect two different Lines.',params:{}}},400).message,
          uncoded:breadmakerErrorFromPayload({detail:'Chapter not found'},404).message,
        })""")
        assert values=={
            'objects':'3 objetos seleccionados',
            'connectors':'2 conectores seleccionados',
            'heading':'Encabezado 1',
            'attach':'Adjuntar como inicio de “Acto I”',
            'linked':'Mapa · Trama',
            'sync':'Línea sincronizada · Agregados: 2 · Eliminados: 1',
            'err':'Una unión debe conectar dos líneas diferentes.',
            'uncoded':'No se encontró el capítulo.',
        }

        # Dates and numbers follow Breadmaker's selected locale rather than the OS locale.
        formatted=page.evaluate("""()=>{
          const date='2026-09-03T17:06:00-06:00';
          return {
            actualDate:i18nDateTime(date),
            expectedDate:new Intl.DateTimeFormat('es',{dateStyle:'medium',timeStyle:'short'}).format(new Date(date)),
            actualComment:i18nDateTime(date,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}),
            expectedComment:new Intl.DateTimeFormat('es',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(date)),
            actualNumber:i18nNumber(1234567.5),
            expectedNumber:Number(1234567.5).toLocaleString('es'),
          }
        }""")
        assert formatted['actualDate']==formatted['expectedDate']
        assert formatted['actualComment']==formatted['expectedComment']
        assert formatted['actualNumber']==formatted['expectedNumber']
        assert not errors,errors
    finally:
        _release(base);_shutdown_page(page)
