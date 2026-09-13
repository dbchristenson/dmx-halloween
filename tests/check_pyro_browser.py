"""Visual regression checks for Pro pyro, using browser-only DMX frames.

uv run --with playwright --with pillow tests/check_pyro_browser.py
No show-control commands or physical outputs are changed by these checks.
"""
import io
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

OUT = Path('/tmp/dmx-pyro-qa')
OUT.mkdir(exist_ok=True)

def diff(a, b):
    first = Image.open(io.BytesIO(a)).convert('RGB')
    second = Image.open(io.BytesIO(b)).convert('RGB')
    return sum(ImageStat.Stat(ImageChops.difference(first, second)).mean)

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True,
        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    context = browser.new_context(viewport={'width': 1280, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    errors, posts = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append({'text': message.text, 'url': message.location.get('url')}) if message.type == 'error' else None)
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    page.goto('http://localhost:8000')
    page.wait_for_function('state && layout')
    sample = page.evaluate('state')
    layout = page.evaluate('layout')
    sample['frame'] = [0] * 512
    flame = next(f['channels'] for f in layout['fixtures'] if f['name'] == 'flame')
    co2 = next(f['channels'] for f in layout['fixtures'] if f['name'] == 'co2')
    page.route('**/api/state', lambda route: route.fulfill(json=sample))
    page.get_by_role('switch', name='Pro view', exact=True).click()
    page.wait_for_function('proView && proView.active')
    page.locator('#proCanvas').scroll_into_view_if_needed()
    page.wait_for_function('proView.fixtures.filter(f => f.hardware).length === 2')
    hardware = page.evaluate('''() => proView.fixtures.filter(f => f.hardware).map(f => ({
      name: f.fixture.name, visible: f.hardware.visible,
      meshes: (() => { let n=0; f.hardware.traverse(o => {if(o.isMesh) n++;}); return n; })(),
      source: (f.flame || f.jet).position.toArray(),
    }))''')
    assert all(h['visible'] and h['meshes'] >= 10 for h in hardware), hardware
    assert all(3.2 < h['source'][1] < 3.4 for h in hardware), 'Emitters must sit at the modeled nozzle outlets'
    assert page.evaluate('proView.fixtures.every(f => !f.flame?.visible && !f.jet?.visible)')
    page.get_by_role('button', name='Crater close-up').click()
    page.wait_for_timeout(150)
    page.locator('#proCanvas').screenshot(path=str(OUT / 'machines.png'))
    print('Machine models and nozzle placement passed', flush=True)

    # A fire channel alone must never display flame.
    sample['frame'][flame['fire'] - 1] = 255
    page.wait_for_timeout(150)
    assert page.evaluate('!proView.fixtures.find(f => f.flame).flame.visible')
    page.get_by_role('button', name='Reset view', exact=True).click()
    page.wait_for_timeout(150)
    off = page.locator('#proCanvas').screenshot()
    sample['frame'][flame['safety'] - 1] = 127
    page.wait_for_function('proView.fixtures.find(f => f.flame).flame.visible')
    page.wait_for_timeout(150)
    fire = page.locator('#proCanvas').screenshot(path=str(OUT / 'flame.png'))
    assert diff(off, fire) > .7, 'Flame and its light must render visibly'
    page.emulate_media(reduced_motion='no-preference')
    first_fire = page.locator('#proCanvas').screenshot()
    page.wait_for_timeout(200)
    moving_fire = page.locator('#proCanvas').screenshot()
    assert diff(first_fire, moving_fire) > .02, 'Flame turbulence must animate'
    page.emulate_media(reduced_motion='reduce')
    sample['frame'][flame['safety'] - 1] = 0
    page.wait_for_function('!proView.fixtures.find(f => f.flame).flame.visible')
    assert page.evaluate('proView.fixtures.find(f => f.flame).flameLight.intensity') == 0
    print('Flame rendering and safety gating passed', flush=True)

    page.get_by_role('button', name='Entire effect').click()
    page.wait_for_timeout(150)
    off_wide = page.locator('#proCanvas').screenshot()
    sample['frame'][co2['jet'] - 1] = 255
    page.wait_for_function('proView.fixtures.find(f => f.jet).jet.visible && proView.fixtures.find(f => f.jet).jet.reach === 1')
    page.wait_for_timeout(150)
    cloud = page.locator('#proCanvas').screenshot(path=str(OUT / 'co2.png'))
    assert diff(off_wide, cloud) > 1.5, 'The nine-meter gas column must be visibly rendered'
    page.emulate_media(reduced_motion='no-preference')
    first_cloud = page.locator('#proCanvas').screenshot()
    page.wait_for_timeout(200)
    moving_cloud = page.locator('#proCanvas').screenshot()
    assert diff(first_cloud, moving_cloud) > .02, 'CO2 must have rising movement'
    page.emulate_media(reduced_motion='reduce')
    projection = page.evaluate('''() => {
      proView.scene.updateMatrixWorld(true);
      const jet = proView.fixtures.find(f => f.jet).jet;
      const top = jet.position.clone(); top.y += 9;
      return {top: top.project(proView.camera).toArray(),
        base: jet.position.clone().project(proView.camera).toArray()};
    }''')
    assert all(abs(projection[key][axis]) < .9 for key in ['top', 'base'] for axis in [0, 1]), projection
    print('Full-height CO2 plume and camera framing passed', flush=True)

    # Camera fitting must continue to contain the tall column at mobile widths.
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#proCanvas').scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.evaluate('''() => {
      const top = proView.fixtures.find(f => f.jet).jet.position.clone(); top.y += 9;
      top.project(proView.camera); return Math.abs(top.y) < .9 && Math.abs(top.x) < .9;
    }''')
    page.locator('.stage-panel').screenshot(path=str(OUT / 'mobile-co2.png'))
    sample['frame'][co2['jet'] - 1] = 0
    page.wait_for_function('!proView.fixtures.find(f => f.jet).jet.visible')
    sample['frame'][flame['fire'] - 1] = 0
    page.wait_for_timeout(150)
    for label, prop in [('Preview flame', 'flame'), ('Preview CO₂', 'jet')]:
        page.get_by_role('button', name=label, exact=True).click()
        page.wait_for_function(f'proView.fixtures.find(f => f.{prop}).{prop}.visible')
        assert 'Visual preview' in page.locator('#proConnection').inner_text()
        assert page.evaluate('proView.fixtures.every(f => f.output.fire === 0 && f.output.jet === 0)'), 'Preview mutated the live frame'
        page.wait_for_function('proView.preview === null', timeout=5000)
        assert page.evaluate(f'!proView.fixtures.find(f => f.{prop}).{prop}.visible')
    page.get_by_role('button', name='Preview CO₂', exact=True).click()
    page.wait_for_function('proView.preview !== null')
    page.get_by_role('switch', name='Pro view', exact=True).click()
    assert page.evaluate('proView.preview === null && proView.previewTimer === 0')
    # Favicon is absent in the existing app; all rendering errors still fail.
    errors = [e for e in errors if not isinstance(e, dict) or not str(e['url']).endswith('/favicon.ico')]
    assert not posts, posts
    assert not errors, errors
    print(json.dumps({'result': 'passed', 'hardware': hardware,
        'flame_pixel_difference': round(diff(off, fire), 3),
        'co2_pixel_difference': round(diff(off_wide, cloud), 3),
        'screenshots': str(OUT)}, indent=2), flush=True)
    browser.close()
