"""Standalone browser integration checks against a running local app.

Run with uv run --with playwright --with pillow tests/check_pro_browser.py.
Uses an isolated headless Chrome profile. Output frames are intercepted in
the browser for deterministic rendering checks; no control POSTs are sent.
"""
import io
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:8000'
OUT = Path('/tmp/dmx-pro-qa')
OUT.mkdir(exist_ok=True)


def pixels(data):
    return Image.open(io.BytesIO(data)).convert('RGB')


def difference(a, b):
    return sum(ImageStat.Stat(ImageChops.difference(pixels(a), pixels(b))).mean)


with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True,
        args=['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    context = browser.new_context(viewport={'width': 1280, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    errors, posts, requests = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else requests.append(request.url))
    page.goto(BASE)
    page.wait_for_function('state && layout')
    live_state = page.evaluate('state')
    layout = page.evaluate('layout')
    assert live_state['sim'], 'Browser checks must use the fake DMX server'
    assert page.locator('#stage').is_visible()
    assert not any('/vendor/' in url or '.glb' in url for url in requests), 'Pro assets must lazy load'
    assert page.locator('#proToggle').get_attribute('aria-checked') == 'false'

    # Freeze only the browser's input stream, never the engine or the wire.
    sample = dict(live_state, frame=[0] * 512)
    page.route('**/api/state', lambda route: route.fulfill(json=sample))
    page.get_by_role('switch', name='Pro view', exact=True).click()
    page.wait_for_function('proView && proView.model && document.getElementById("proLoading").hidden')
    page.wait_for_function('proView.renderer.info.render.triangles > 20000')
    assert not page.locator('#stage').is_visible()
    page.locator('#proCanvas').scroll_into_view_if_needed()
    # Reset must also work while ordinary drag inertia is still active.
    page.emulate_media(reduced_motion='no-preference')
    home = page.evaluate('proView.controls.position0.toArray()')
    box = page.locator('#proCanvas').bounding_box()
    x, y = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 140, y, steps=3)
    page.mouse.up()
    reset_position = page.evaluate('''() => {
        document.getElementById('proReset').click();
        for (let i = 0; i < 120; i++) proView.controls.update();
        return proView.camera.position.toArray();
    }''')
    assert math.dist(reset_position, home) < 1e-6, f'Reset retained drag momentum: {reset_position}'
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT / 'desktop.png'), full_page=True)
    stage = page.locator('#proCanvas')
    off = stage.screenshot(path=str(OUT / 'unlit.png'))
    # Change actual channel-addressed RGB/dimmer samples and inspect mesh pixels.
    for fixture in layout['fixtures']:
        channels = fixture['channels']
        if fixture['name'].startswith('par'):
            sample['frame'][channels['dimmer'] - 1] = 255
            sample['frame'][channels['red'] - 1] = 255
    page.wait_for_function('proView.fixtures.filter(f => f.fixture.name.startsWith("par")).every(f => f.output.rgb[0] === 255)')
    page.wait_for_timeout(200)
    red = stage.screenshot(path=str(OUT / 'red.png'))
    assert difference(off, red) > 8, 'Red fixtures must visibly light the mesh'
    for fixture in layout['fixtures']:
        if fixture['name'].startswith('par'):
            sample['frame'][fixture['channels']['red'] - 1] = 0
            sample['frame'][fixture['channels']['blue'] - 1] = 255
    page.wait_for_function('proView.fixtures.filter(f => f.fixture.name.startsWith("par")).every(f => f.output.rgb[2] === 255)')
    page.wait_for_timeout(200)
    blue = stage.screenshot(path=str(OUT / 'blue.png'))
    assert difference(red, blue) > 10, 'Changing DMX RGB must change surface color'

    home = page.evaluate('proView.camera.position.toArray()')
    box = stage.bounding_box()
    x, y = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 120, y - 35, steps=12)
    page.mouse.up()
    assert page.evaluate('proView.camera.position.toArray()') != home, 'Mouse drag must orbit'
    dragged = stage.screenshot(path=str(OUT / 'orbit.png'))
    assert difference(blue, dragged) > 3, 'Orbit must change the rendered perspective'
    page.get_by_role('button', name='Reset view', exact=True).click()
    assert math.dist(page.evaluate('proView.camera.position.toArray()'), home) < 1e-6
    stage.focus()
    page.keyboard.press('ArrowLeft')
    assert page.evaluate('proView.camera.position.toArray()') != home
    page.keyboard.press('Home')
    assert math.dist(page.evaluate('proView.camera.position.toArray()'), home) < 1e-6
    distance = page.evaluate('proView.camera.position.distanceTo(proView.controls.target)')
    page.get_by_role('button', name='Zoom in', exact=True).click()
    assert page.evaluate('proView.camera.position.distanceTo(proView.controls.target)') < distance

    page.reload()
    page.wait_for_function('proView && proView.active')
    assert page.get_by_role('switch', name='Pro view', exact=True).get_attribute('aria-checked') == 'true'
    page.get_by_role('switch', name='Pro view', exact=True).click()
    assert page.locator('#stage').is_visible()
    assert page.evaluate('proView.raf') == 0, 'Hidden Pro view must stop animation'
    page.reload()
    page.wait_for_function('state && layout')
    assert page.get_by_role('switch', name='Pro view', exact=True).get_attribute('aria-checked') == 'false'
    assert not posts, f'Preview controls sent control requests: {posts}'
    assert not errors, errors
    assert all(url.startswith(BASE) for url in requests), 'Renderer must work without external requests'

    # A real emulated touch stream exercises OrbitControls pointer handling.
    mobile = browser.new_context(viewport={'width': 390, 'height': 844},
        device_scale_factor=2, is_mobile=True, has_touch=True, reduced_motion='reduce')
    touch = mobile.new_page()
    touch.on('pageerror', lambda error: errors.append(str(error)))
    touch.route('**/api/state', lambda route: route.fulfill(json=sample))
    touch.goto(BASE)
    touch.get_by_role('switch', name='Pro view', exact=True).tap()
    touch.wait_for_function('proView && proView.active')
    touch.locator('#proCanvas').scroll_into_view_if_needed()
    touch.wait_for_timeout(250)
    assert touch.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile layout overflows'
    before = touch.evaluate('proView.camera.position.toArray()')
    box = touch.locator('#proCanvas').bounding_box()
    x, y = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
    cdp = mobile.new_cdp_session(touch)
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x, 'y': y, 'id': 1}]})
    for step in range(1, 9):
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x + step * 10, 'y': y - step * 3, 'id': 1}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert touch.evaluate('proView.camera.position.toArray()') != before, 'Touch swipe must orbit'
    touch.screenshot(path=str(OUT / 'mobile.png'), full_page=True)
    zoom_before = touch.evaluate('proView.camera.position.distanceTo(proView.controls.target)')
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x - 25, 'y': y, 'id': 1}, {'x': x + 25, 'y': y, 'id': 2}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x - 55, 'y': y, 'id': 1}, {'x': x + 55, 'y': y, 'id': 2}]})
    cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
    assert touch.evaluate('proView.camera.position.distanceTo(proView.controls.target)') < zoom_before, 'Pinch must zoom'
    assert not errors, errors

    # Missing GLB must offer recovery while the standard view still works.
    failure = browser.new_context(viewport={'width': 1100, 'height': 900})
    broken = failure.new_page()
    broken.route('**/models/volcano.glb', lambda route: route.fulfill(status=404, body='missing'))
    broken.goto(BASE)
    broken.get_by_role('switch', name='Pro view', exact=True).click()
    broken.locator('#proRetry').wait_for(state='visible')
    assert 'Could not open' in broken.locator('#proStatus').inner_text()
    broken.unroute('**/models/volcano.glb')
    broken.get_by_role('button', name='Retry 3D view').click()
    broken.wait_for_function('proView && proView.active')
    assert broken.locator('#proCanvas').count() == 1
    broken.get_by_role('switch', name='Pro view', exact=True).click()
    assert broken.locator('#stage').is_visible()

    # A module-network failure is cached by the browser; recovery must reload.
    module_failure = browser.new_context(viewport={'width': 1100, 'height': 900})
    module_page = module_failure.new_page()
    module_page.route('**/pro-view.mjs', lambda route: route.fulfill(status=503, body='temporarily unavailable'))
    module_page.goto(BASE)
    module_page.get_by_role('switch', name='Pro view', exact=True).click()
    module_page.locator('#proRetry').wait_for(state='visible')
    module_page.unroute('**/pro-view.mjs')
    module_page.locator('#proRetry').click()
    module_page.wait_for_function('proView && proView.active')
    assert module_page.locator('#proCanvas').count() == 1
    assert module_page.get_by_role('switch', name='Pro view', exact=True).get_attribute('aria-checked') == 'true'

    print(json.dumps({'result': 'passed', 'red_surface_difference': round(difference(off, red), 2),
        'color_surface_difference': round(difference(red, blue), 2),
        'checks': ['lazy load', 'GLB rendering', 'DMX surface lighting', 'mouse orbit',
            'keyboard orbit', 'zoom', 'reset', 'persistence', 'stop when hidden',
            'local assets', 'no control POSTs', 'mobile layout', 'touch swipe', 'pinch zoom',
            'asset failure recovery', 'reset during drag inertia', 'module failure recovery'],
        'screenshots': str(OUT)}, indent=2))
    browser.close()
