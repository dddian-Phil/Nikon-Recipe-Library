"""Render the library's aperture mark as SVG, PNG and a Windows multi-size icon."""
from math import cos, sin, radians
from pathlib import Path

from PIL import Image, ImageDraw


def point(radius, angle):
    return (128 + radius * cos(radians(angle)), 128 + radius * sin(radians(angle)))


def build():
    output = Path(__file__).resolve().parent / "assets"
    output.mkdir(exist_ok=True)
    scale = 4
    background = "#191919"
    yellow = "#fff700"
    image = Image.new("RGBA", (256 * scale, 256 * scale), (0, 0, 0, 0))
    drawing = ImageDraw.Draw(image)

    def scaled(points):
        return [(round(x * scale), round(y * scale)) for x, y in points]

    drawing.rounded_rectangle((6 * scale, 6 * scale, 250 * scale, 250 * scale),
                              radius=54 * scale, fill=background)
    drawing.ellipse((27 * scale, 27 * scale, 229 * scale, 229 * scale),
                    outline="#51514b", width=scale)
    paths = []
    for blade in range(6):
        angle = -90 + blade * 60
        outer = [point(84, angle + step) for step in range(61)]
        inner_end = point(33, angle + 90)
        inner_start = point(33, angle + 30)
        polygon = outer + [inner_end, inner_start]
        drawing.polygon(scaled(polygon), fill=yellow)
        drawing.line(scaled(polygon + [polygon[0]]), fill=background,
                     width=5 * scale, joint="curve")
        x, y = outer[0]
        ex, ey = outer[-1]
        ix, iy = inner_end
        sx, sy = inner_start
        paths.append(f'<path d="M{x:.3f},{y:.3f} A84,84 0 0 1 {ex:.3f},{ey:.3f}'
                     f' L{ix:.3f},{iy:.3f} L{sx:.3f},{sy:.3f} Z"/>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
           '<rect x="6" y="6" width="244" height="244" rx="54" fill="#191919"/>'
           '<circle cx="128" cy="128" r="101" fill="none" stroke="#51514b"/>'
           '<g fill="#fff700" stroke="#191919" stroke-width="5" stroke-linejoin="round">'
           + "".join(paths) + "</g></svg>\n")
    (output / "library-icon.svg").write_text(svg, encoding="utf-8")
    image.resize((512, 512), Image.Resampling.LANCZOS).save(output / "library-icon.png")
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        output / "library-icon.ico", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    with Image.open(output / "library-icon.ico") as icon:
        assert len(icon.ico.sizes()) == 7
    print(output / "library-icon.ico")


if __name__ == "__main__":
    build()
