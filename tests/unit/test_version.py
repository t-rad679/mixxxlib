import io
from contextlib import redirect_stdout

from mixxxlib import __version__
from mixxxlib.cli import main


def test_version_flag_prints_version():
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(["--version"])
    assert code == 0
    assert out.getvalue().strip() == __version__
