import pyimatrix


def test_exports() -> None:
    assert pyimatrix.DEFAULT_BASE_URL == "https://api.imatrixsys.com"
    assert pyimatrix.__version__ == "0.0.1"
