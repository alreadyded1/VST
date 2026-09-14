import functools

import pytest
from werkzeug.security import generate_password_hash

import app as app_module

# macOS system Python is built against LibreSSL, which lacks hashlib.scrypt
# (Werkzeug's default). Hash with pbkdf2 in tests so the default admin user
# can be created; check_password_hash reads the method from the hash itself.
app_module.generate_password_hash = functools.partial(generate_password_hash, method='pbkdf2')


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client backed by a fresh SQLite DB and upload dir under tmp_path."""
    monkeypatch.setattr(app_module, 'DATABASE', str(tmp_path / 'test.db'))
    static_dir = tmp_path / 'static'
    for sub in ('receipts', 'vehicles', 'documents'):
        (static_dir / 'uploads' / sub).mkdir(parents=True)
    monkeypatch.setattr(app_module.app, 'static_folder', str(static_dir))
    monkeypatch.setitem(app_module.app.config, 'UPLOAD_FOLDER', str(static_dir / 'uploads'))
    monkeypatch.setattr(app_module.app, 'testing', True)
    app_module.init_db()

    with app_module.app.test_client() as c:
        c.post('/login', data={'username': 'admin', 'password': 'admin'})
        yield c


@pytest.fixture
def vehicle_id(client):
    r = client.post('/api/vehicle', data={'manufacturer': 'Honda', 'model': 'Civic', 'year': 2020})
    return r.get_json()['id']
