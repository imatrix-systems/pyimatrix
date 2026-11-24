import pyimatrix


def test_exports() -> None:
    assert pyimatrix.DEFAULT_BASE_URL == "https://api.imatrixsys.com"
    assert pyimatrix.__version__ == "0.1.0"
    assert pyimatrix.SCHEMA_VERSION == 1
    assert set(pyimatrix.__all__) <= set(dir(pyimatrix))


def test_tracker_kind() -> None:
    assert pyimatrix.DeviceKind.parse("tracker") is pyimatrix.DeviceKind.TRACKER
    assert pyimatrix.DeviceKind.parse(None) is pyimatrix.DeviceKind.OTHER
