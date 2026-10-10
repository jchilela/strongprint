"""Gera documentos de teste com cobertura de tinta conhecida."""

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

A4 = (595, 842)  # pontos a 72 dpi


def page(background="white", box=None, box_area=(50, 50, 545, 300)):
    img = Image.new("RGB", A4, background)
    if box:
        ImageDraw.Draw(img).rectangle(box_area, fill=box)
    return img


def make_pdf(path, pages):
    pages[0].save(path, "PDF", resolution=72, save_all=True, append_images=pages[1:])
    return path


def encrypt_pdf(src, dst, password="secret"):
    writer = PdfWriter()
    for p in PdfReader(str(src)).pages:
        writer.add_page(p)
    writer.encrypt(password)
    with open(dst, "wb") as fh:
        writer.write(fh)
    return dst
