import pymupdf
import pytest

from blackpdf.core.errors import InvalidRangeError
from blackpdf.procworker import ProcessJob, run_blocking

from .conftest import page_labels


def test_runs_tool_in_subprocess(make_pdf, tmp_path):
    output = tmp_path / "extraido.pdf"
    result = run_blocking("blackpdf.core.split:extract_pages", str(make_pdf(pages=3)), "3", str(output))
    assert result == output
    assert page_labels(output) == ["P3"]


def test_errors_cross_the_process_boundary(make_pdf, tmp_path):
    with pytest.raises(InvalidRangeError) as info:
        run_blocking("blackpdf.core.split:extract_pages", str(make_pdf(pages=3)), "9", str(tmp_path / "x.pdf"))
    assert info.value.params["page_count"] == 3


def test_reports_progress(make_pdf, tmp_path):
    sources = [str(make_pdf(pages=1)) for _ in range(4)]
    job = ProcessJob("blackpdf.core.merge:merge_pdfs", (sources, str(tmp_path / "m.pdf")))
    job.start()
    kinds = []
    try:
        while True:
            message = job.next_message(0.5)
            if message is None:
                continue
            kinds.append(message.kind)
            if message.kind != "progress":
                break
    finally:
        job.close()
    assert kinds[-1] == "done"
    assert "progress" in kinds
    assert pymupdf.open(tmp_path / "m.pdf").page_count == 4


def test_rejects_foreign_targets():
    with pytest.raises(RuntimeError):
        run_blocking("os:system", "echo")
