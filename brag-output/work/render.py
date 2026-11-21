"""Render brag.html frame by frame (pure function of time) with headless Chromium.

usage: python render.py stills 0.5 2.5 ...   -> work/stills/t_<time>.png
       python render.py frames               -> work/frames/f_00000.png ... (22 s @ 30 fps)
"""
import pathlib, sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
FPS, DUR = 30, 22.0


def main():
    mode = sys.argv[1]
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        pg.goto((HERE / "brag.html").as_uri())
        pg.wait_for_load_state("networkidle")
        pg.evaluate("document.fonts.ready.then(() => true)")
        # make sure every face we use is actually loaded before capturing
        pg.evaluate("""Promise.all(['700 100px "Bricolage Grotesque"','500 20px Inter','600 20px "IBM Plex Mono"',
            '600 20px "Anek Tamil"','600 20px "Anek Devanagari"'].map(f => document.fonts.load(f, 'அ हि A')))""")
        if mode == "stills":
            out = HERE / "stills"; out.mkdir(exist_ok=True)
            for s in sys.argv[2:]:
                pg.evaluate(f"render({float(s)})")
                pg.screenshot(path=str(out / f"t_{float(s):05.2f}.png"))
        else:
            out = HERE / "frames"; out.mkdir(exist_ok=True)
            n = int(round(FPS * DUR))
            for i in range(n):
                pg.evaluate(f"render({i / FPS})")
                pg.screenshot(path=str(out / f"f_{i:05d}.png"))
                if i % 60 == 0:
                    print(i, "/", n, flush=True)
        b.close()


if __name__ == "__main__":
    main()
