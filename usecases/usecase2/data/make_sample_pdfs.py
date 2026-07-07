"""make_sample_pdfs.py — generate PDF versions of the sample remittances.

Renders selected .txt samples to .pdf (monospace, like a printed remittance) so
you can test the PDF-upload path in the review UI. Uses reportlab.

Run:  python -m usecases.usecase2.data.make_sample_pdfs
"""

from __future__ import annotations

from pathlib import Path

from ..config import SAMPLE_PAYMENTS_DIR

# Which text samples to also emit as PDFs (a representative spread).
_TO_PDF = [
    "clean_exact",
    "short_pay_damage",
    "multi_invoice",
    "no_reference",
    "wire_multi_pricing",
]


def _txt_to_pdf(txt_path: Path, pdf_path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    text_obj = c.beginText(54, 740)
    text_obj.setFont("Courier", 10)
    for line in txt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text_obj.textLine(line)
    c.drawText(text_obj)
    c.showPage()
    c.save()


def main() -> None:
    made = []
    for name in _TO_PDF:
        txt = SAMPLE_PAYMENTS_DIR / f"{name}.txt"
        if not txt.exists():
            continue
        pdf = SAMPLE_PAYMENTS_DIR / f"{name}.pdf"
        _txt_to_pdf(txt, pdf)
        made.append(pdf.name)
    print(f"Wrote {len(made)} PDFs to {SAMPLE_PAYMENTS_DIR}:")
    for m in made:
        print("  -", m)


if __name__ == "__main__":
    main()
