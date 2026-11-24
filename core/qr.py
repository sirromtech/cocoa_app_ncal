"""QR codes rendered server-side with ReportLab (no extra dependency)."""
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing


def qr_svg(data, size=240):
    widget = QrCodeWidget(data)
    widget.barLevel = "M"
    x0, y0, x1, y1 = widget.getBounds()
    w, h = x1 - x0, y1 - y0
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)
