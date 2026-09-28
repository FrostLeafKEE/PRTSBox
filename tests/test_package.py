"""Guard local settings and models when preparing a portable package."""

import zipfile

from scripts import package as packaging


def test_package_excludes_data_without_deleting_it(tmp_path, monkeypatch):
    app_dir = tmp_path / "PRTSBox"
    app_dir.mkdir()
    (app_dir / "PRTSBox.exe").write_bytes(b"program")
    data_dir = app_dir / "data"
    (data_dir / "models").mkdir(parents=True)
    (data_dir / "config.json").write_text('{"saved": true}', encoding="utf-8")
    model = data_dir / "models" / "model.gguf"
    model.write_bytes(b"model")

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    monkeypatch.setattr(packaging, "DIST", dist_dir)
    archive = packaging.make_zip(app_dir)

    with zipfile.ZipFile(archive) as bundle:
        assert bundle.namelist() == ["PRTSBox.exe"]
    assert (data_dir / "config.json").read_text(encoding="utf-8") == '{"saved": true}'
    assert model.read_bytes() == b"model"
