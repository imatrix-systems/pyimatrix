import pyimatrix


def test_exports() -> None:
    assert pyimatrix.DEFAULT_BASE_URL == "https://api.imatrixsys.com"
    assert pyimatrix.__version__ == "0.1.0"
    assert pyimatrix.SCHEMA_VERSION == 1
    assert set(pyimatrix.__all__) <= set(dir(pyimatrix))
