"""Parts, purchases and installs: stock, cost and warranty are all derived."""
import io
import json
import os
from datetime import date, timedelta

import app as app_module


def _part(client, **fields):
    data = {'name': 'Brake pads'}
    data.update(fields)
    r = client.post('/api/parts', data=data, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _purchase(client, part_id, **fields):
    data = {'quantity': '1', 'unit_cost': '10'}
    data.update(fields)
    r = client.post(f'/api/parts/{part_id}/purchases', data=data, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    return r.get_json()['id']


def _service(client, vehicle_id, parts, **fields):
    data = {'vehicle_id': vehicle_id, 'date': '2024-05-01', 'cost': '100', 'service_provider': 'Shop',
            'parts': json.dumps(parts)}
    data.update(fields)
    return client.post('/api/services', data=data)


def _get(client, part_id):
    return client.get(f'/api/parts/{part_id}').get_json()


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def test_part_with_first_purchase_reports_stock_and_value(client):
    part_id = _part(client, quantity='5', unit_cost='8.50', supplier='NAPA')
    part = _get(client, part_id)
    assert part['in_stock'] == 5
    assert part['stock_value'] == 42.5
    assert part['cost'] == 8.5
    assert len(part['purchases']) == 1 and part['purchases'][0]['supplier'] == 'NAPA'
    assert part['installs'] == []


def test_part_without_purchase_has_no_stock(client):
    part = _get(client, _part(client))
    assert part['in_stock'] == 0 and part['purchases'] == []


def test_service_draws_oldest_purchase_first_and_keeps_each_cost(client, vehicle_id):
    part_id = _part(client, name='Oil')
    _purchase(client, part_id, quantity='2', unit_cost='5', purchase_date='2024-01-01')
    _purchase(client, part_id, quantity='5', unit_cost='8', purchase_date='2024-03-01')

    r = _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 3}])
    assert r.status_code == 200, r.get_json()
    service = client.get(f"/api/services/{r.get_json()['id']}").get_json()

    # 2 from the $5 purchase, then 1 from the $8 one
    assert sorted((p['quantity'], p['unit_cost']) for p in service['parts']) == [(1, 8), (2, 5)]
    assert service['supplies_cost'] == 18
    assert _get(client, part_id)['in_stock'] == 4

    stats = client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()
    assert stats['total_spent'] == 118


def test_service_cannot_use_more_than_is_in_stock(client, vehicle_id):
    part_id = _part(client, quantity='1')
    r = _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 2}])
    assert r.status_code == 400
    assert 'Only 1' in r.get_json()['error']
    # Nothing was half-saved
    assert client.get(f'/api/services?vehicle_id={vehicle_id}').get_json() == []
    assert _get(client, part_id)['in_stock'] == 1


def test_editing_a_service_keeps_untouched_installs_and_returns_removed_ones(client, vehicle_id):
    pads = _part(client, quantity='2')
    oil = _part(client, name='Oil', quantity='6')
    service_id = _service(client, vehicle_id, [{'part_id': pads, 'quantity': 2},
                                               {'part_id': oil, 'quantity': 4}]).get_json()['id']
    parts = client.get(f'/api/services/{service_id}').get_json()['parts']
    pads_install = next(p for p in parts if p['part_id'] == pads)
    client.put(f"/api/part-installs/{pads_install['id']}", json={'notes': 'front axle'})

    # Keep the pads, drop the oil, move the service date
    r = client.put(f'/api/services/{service_id}', data={
        'date': '2024-06-01', 'cost': '100', 'service_provider': 'Shop', 'odometer': '52000',
        'parts': json.dumps([{'install_id': pads_install['id'], 'quantity': 2}])})
    assert r.status_code == 200, r.get_json()

    parts = client.get(f'/api/services/{service_id}').get_json()['parts']
    assert len(parts) == 1
    assert parts[0]['id'] == pads_install['id'] and parts[0]['notes'] == 'front axle'
    assert parts[0]['install_date'] == '2024-06-01' and parts[0]['odometer'] == 52000
    assert _get(client, oil)['in_stock'] == 6


def test_editing_a_service_without_a_parts_field_leaves_parts_alone(client, vehicle_id):
    part_id = _part(client, quantity='2')
    service_id = _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}]).get_json()['id']
    client.put(f'/api/services/{service_id}', data={'date': '2024-07-01', 'cost': '1', 'service_provider': 'Shop'})
    parts = client.get(f'/api/services/{service_id}').get_json()['parts']
    assert len(parts) == 1 and parts[0]['install_date'] == '2024-07-01'


def test_deleting_a_service_puts_its_parts_back(client, vehicle_id):
    part_id = _part(client, quantity='2')
    service_id = _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 2}]).get_json()['id']
    assert _get(client, part_id)['in_stock'] == 0
    client.delete(f'/api/services/{service_id}')
    part = _get(client, part_id)
    assert part['in_stock'] == 2 and part['installs'] == []


def test_parts_are_shared_between_vehicles(client, vehicle_id):
    other = client.post('/api/vehicle', data={'manufacturer': 'Ford', 'model': 'F150', 'year': 2018}).get_json()['id']
    part_id = _part(client, name='Oil', quantity='10')
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 4}])
    _service(client, other, [{'part_id': part_id, 'quantity': 5}])
    part = _get(client, part_id)
    assert part['in_stock'] == 1
    assert {i['vehicle_id'] for i in part['installs']} == {vehicle_id, other}


def test_warranty_clock_starts_at_install_by_default(client, vehicle_id):
    part_id = _part(client, quantity='1', purchase_date=_ago(700), warranty_months='12')
    r = client.post(f'/api/parts/{part_id}/installs',
                    json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': _ago(100)})
    assert r.status_code == 200, r.get_json()
    warranty = _get(client, part_id)['installs'][0]['warranty']
    # Bought long ago, but only fitted 100 days back: still covered
    assert warranty['status'] == 'active'
    assert warranty['start_date'] == _ago(100)
    assert 255 <= warranty['days_remaining'] <= 270


def test_warranty_can_start_at_purchase(client, vehicle_id):
    part_id = _part(client, quantity='1', purchase_date=_ago(700), warranty_months='12',
                    warranty_starts='purchase')
    # Running (and here, run out) while still on the shelf
    assert _get(client, part_id)['purchases'][0]['shelf_warranty']['status'] == 'expired'
    client.post(f'/api/parts/{part_id}/installs',
                json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': _ago(100)})
    assert _get(client, part_id)['installs'][0]['warranty']['status'] == 'expired'


def test_mileage_warranty_uses_install_odometer_and_latest_reading(client, vehicle_id):
    part_id = _part(client, quantity='1', warranty_miles='12000')
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}], odometer='50000', date=_ago(30))
    warranty = _get(client, part_id)['installs'][0]['warranty']
    assert warranty['status'] == 'active' and warranty['miles_remaining'] == 12000

    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(1), 'odometer': 61500,
                                   'gallons': 10, 'cost': 30})
    warranty = _get(client, part_id)['installs'][0]['warranty']
    assert warranty['status'] == 'expiring' and warranty['miles_remaining'] == 500

    client.post('/api/fuel', json={'vehicle_id': vehicle_id, 'date': _ago(0), 'odometer': 62500,
                                   'gallons': 10, 'cost': 30})
    assert _get(client, part_id)['installs'][0]['warranty']['status'] == 'expired'


def test_whichever_limit_comes_first_ends_the_warranty(client, vehicle_id):
    part_id = _part(client, quantity='1', warranty_months='12', warranty_miles='100000')
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}], odometer='50000', date=_ago(400))
    assert _get(client, part_id)['installs'][0]['warranty']['status'] == 'expired'


def test_lifetime_warranty_and_no_warranty(client, vehicle_id):
    lifetime = _part(client, quantity='1', warranty_lifetime='on', warranty_months='12')
    plain = _part(client, name='Oil', quantity='1')
    _service(client, vehicle_id, [{'part_id': lifetime, 'quantity': 1}, {'part_id': plain, 'quantity': 1}],
             date=_ago(4000))
    assert _get(client, lifetime)['installs'][0]['warranty']['status'] == 'lifetime'
    assert _get(client, plain)['installs'][0]['warranty'] is None


def test_two_installs_from_one_purchase_have_separate_warranty_clocks(client, vehicle_id):
    part_id = _part(client, name='Spark plug', quantity='4', warranty_months='12')
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 2}], date=_ago(500))
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 2}], date=_ago(10))
    part = _get(client, part_id)
    assert sorted(i['warranty']['status'] for i in part['installs']) == ['active', 'expired']
    assert part['warranty']['active'] == 1 and part['warranty']['expired'] == 1


def test_warranties_list_is_soonest_first_and_skips_removed_and_unwarrantied(client, vehicle_id):
    soon = _part(client, name='Soon', quantity='1', warranty_months='12')
    later = _part(client, name='Later', quantity='1', warranty_months='36')
    removed = _part(client, name='Removed', quantity='1', warranty_months='36')
    plain = _part(client, name='Plain', quantity='1')
    _service(client, vehicle_id, [{'part_id': p, 'quantity': 1} for p in (soon, later, removed, plain)],
             date=_ago(350))
    install_id = _get(client, removed)['installs'][0]['id']
    client.put(f'/api/part-installs/{install_id}', json={'removed_date': _ago(5)})

    warranties = client.get(f'/api/warranties?vehicle_id={vehicle_id}').get_json()
    assert [w['part_name'] for w in warranties] == ['Soon', 'Later']
    assert warranties[0]['warranty']['status'] == 'expiring'
    assert client.get('/api/warranties?vehicle_id=9999').get_json() == []


def test_warranty_start_override_on_an_install(client, vehicle_id):
    part_id = _part(client, quantity='1', warranty_months='12')
    client.post(f'/api/parts/{part_id}/installs',
                json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': _ago(10)})
    install_id = _get(client, part_id)['installs'][0]['id']
    client.put(f'/api/part-installs/{install_id}', json={'warranty_start_date': _ago(500)})
    assert _get(client, part_id)['installs'][0]['warranty']['status'] == 'expired'


def test_low_stock_adds_one_reorder_reminder(client, vehicle_id):
    part_id = _part(client, name='Wiper', quantity='3', reorder_threshold='1')
    for _ in range(3):
        assert _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}]).status_code == 200
    reminders = client.get(f'/api/reminders?vehicle_id={vehicle_id}').get_json()
    assert [r['service_type'] for r in reminders] == ['Re-order Wiper']
    assert _get(client, part_id)['low_stock'] is True


def test_standalone_install_can_be_undone_but_service_installs_cannot(client, vehicle_id):
    part_id = _part(client, quantity='2')
    client.post(f'/api/parts/{part_id}/installs',
                json={'vehicle_id': vehicle_id, 'quantity': 1, 'install_date': '2024-01-01'})
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}])
    by_service = {bool(i['service_id']): i['id'] for i in _get(client, part_id)['installs']}

    assert client.delete(f'/api/part-installs/{by_service[True]}').status_code == 400
    assert client.delete(f'/api/part-installs/{by_service[False]}').status_code == 200
    assert _get(client, part_id)['in_stock'] == 1


def test_purchase_quantity_cannot_drop_below_what_is_installed(client, vehicle_id):
    part_id = _part(client, quantity='4')
    purchase_id = _get(client, part_id)['purchases'][0]['id']
    _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 3}])

    r = client.put(f'/api/part-purchases/{purchase_id}', data={'quantity': '2', 'unit_cost': '10'})
    assert r.status_code == 400
    assert client.put(f'/api/part-purchases/{purchase_id}', data={'quantity': '3', 'unit_cost': '10'}).status_code == 200
    assert _get(client, part_id)['in_stock'] == 0
    assert client.delete(f'/api/part-purchases/{purchase_id}').status_code == 400


def test_part_delete_is_blocked_while_installed_and_cleans_up_receipts(client, vehicle_id, tmp_path):
    part_id = _part(client, quantity='1', receipt=(io.BytesIO(b'%PDF'), 'r.pdf'))
    receipt = tmp_path / 'static' / _get(client, part_id)['purchases'][0]['receipt_path']
    assert receipt.exists()

    service_id = _service(client, vehicle_id, [{'part_id': part_id, 'quantity': 1}]).get_json()['id']
    assert client.delete(f'/api/parts/{part_id}').status_code == 400
    client.delete(f'/api/services/{service_id}')
    assert client.delete(f'/api/parts/{part_id}').status_code == 200
    assert not receipt.exists()
    assert client.get('/api/parts').get_json() == []


def test_validation_errors_are_json_400s(client, vehicle_id):
    assert client.post('/api/parts', data={'name': ''}).status_code == 400
    part_id = _part(client)
    r = client.post(f'/api/parts/{part_id}/purchases', data={'quantity': '0', 'unit_cost': '1'})
    assert r.status_code == 400 and 'error' in r.get_json()
    r = client.post(f'/api/parts/{part_id}/purchases', data={'quantity': '1', 'purchase_date': 'yesterday'})
    assert r.status_code == 400
    r = client.post(f'/api/parts/{part_id}/installs', json={'vehicle_id': vehicle_id, 'quantity': 1})
    assert r.status_code == 400


def test_parts_page_and_old_address(client):
    assert client.get('/parts').status_code == 200
    r = client.get('/supplies')
    assert r.status_code == 302 and r.headers['Location'].endswith('/parts')


def test_search_and_csv_export(client):
    _part(client, name='Cabin filter', brand='Bosch', quantity='2')
    results = client.get('/api/search?q=bosch').get_json()['results']
    assert results['parts'][0]['label'] == 'Cabin filter' and results['parts'][0]['qty'] == 2
    csv_text = client.get('/api/parts/export-csv').get_data(as_text=True)
    assert 'Cabin filter,,Bosch' in csv_text


# ── Migration from the old supplies tables ───────────────────────────────────

def _legacy_db(vehicle_id):
    """Old-model rows, as an existing install would have them: a consumable
    used on a service, a flat warrantied part, and an individually tracked
    part with units in every status."""
    db = app_module.get_db()
    db.execute("DELETE FROM settings WHERE key = 'parts_migrated'")
    db.execute('''INSERT INTO service_records (id, vehicle_id, date, cost, service_provider, odometer)
                  VALUES (1, ?, '2024-02-01', 100, 'Shop', 40000)''', (vehicle_id,))

    # 1: oil, 3 left, 2 used on service 1, re-order reminder on
    db.execute('''INSERT INTO supplies (id, vehicle_id, name, cost, quantity, supplier, purchase_date,
                                        receipt_path, remind_to_reorder, tracked_individually)
                  VALUES (1, ?, 'Oil', 6, 3, 'NAPA', '2024-01-10', 'uploads/receipts/oil.pdf', 1, 0)''',
               (vehicle_id,))
    db.execute('INSERT INTO service_supplies (service_id, supply_id, quantity_used) VALUES (1, 1, 2)')

    # 2: a flat "this part is on the car" row with a warranty
    db.execute('''INSERT INTO supplies (id, vehicle_id, name, cost, quantity, installation_date,
                                        warranty_start_date, warranty_months, warranty_mileage_limit,
                                        warranty_start_mileage, tracked_individually)
                  VALUES (2, ?, 'Alternator', 180, 1, '2023-06-01', '2023-06-01', 36, 36000, 30000, 0)''',
               (vehicle_id,))

    # 3: tracked brake pads, four bought together (one installed on service 1,
    #    one in stock, one used, one returned) and a later, dearer one
    db.execute('''INSERT INTO supplies (id, vehicle_id, name, part_number, cost, quantity, tracked_individually)
                  VALUES (3, ?, 'Brake pads', 'BP-1', 40, 0, 1)''', (vehicle_id,))
    unit = '''INSERT INTO supply_units (id, supply_id, vehicle_id, purchase_date, cost, status,
                                        installation_date, installed_service_id, warranty_start_date,
                                        warranty_months, supplier, notes)
              VALUES (?, 3, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    db.execute(unit, (1, vehicle_id, '2024-01-05', 40, 'installed', '2024-02-01', 1, '2024-02-01', 24, 'RockAuto', None))
    db.execute(unit, (2, vehicle_id, '2024-01-05', 40, 'in_stock', None, None, None, 24, 'RockAuto', 'spare'))
    db.execute(unit, (3, vehicle_id, '2024-01-05', 40, 'used', '2024-01-20', None, '2024-01-20', 24, 'RockAuto', None))
    db.execute(unit, (4, vehicle_id, '2024-01-05', 40, 'returned', None, None, None, 24, 'RockAuto', None))
    db.execute(unit, (5, vehicle_id, '2024-03-01', 55, 'in_stock', None, None, '2024-03-01', 12, 'AutoZone', None))
    db.execute('INSERT INTO service_supplies (service_id, supply_id, quantity_used, supply_unit_id) VALUES (1, 3, 1, 1)')
    db.commit()
    db.close()


def test_migration_from_supplies(client, vehicle_id):
    _legacy_db(vehicle_id)
    app_module.init_db()

    parts = {p['name']: p for p in client.get('/api/parts').get_json()}
    assert {p['id'] for p in parts.values()} == {1, 2, 3}

    oil = parts['Oil']
    assert oil['in_stock'] == 3 and oil['reorder_threshold'] == 1
    assert len(oil['purchases']) == 1
    purchase = oil['purchases'][0]
    assert (purchase['quantity'], purchase['unit_cost'], purchase['supplier']) == (5, 6, 'NAPA')
    assert purchase['receipt_path'] == 'uploads/receipts/oil.pdf'
    assert [(i['service_id'], i['quantity'], i['odometer']) for i in oil['installs']] == [(1, 2, 40000)]

    alternator = parts['Alternator']
    assert alternator['in_stock'] == 0
    install = alternator['installs'][0]
    assert install['vehicle_id'] == vehicle_id and install['install_date'] == '2023-06-01'
    assert install['warranty']['months'] == 36 and install['warranty']['expires_at_mileage'] == 66000

    pads = parts['Brake pads']
    assert pads['in_stock'] == 2
    lots = sorted(pads['purchases'], key=lambda p: p['unit_cost'])
    assert [(p['quantity'], p['unit_cost'], p['remaining']) for p in lots] == [(3, 40, 1), (1, 55, 1)]
    assert 'returned' in lots[0]['notes'] and 'spare' in lots[0]['notes']
    assert lots[0]['warranty_starts'] == 'install' and lots[1]['warranty_starts'] == 'purchase'
    assert sorted((i['install_date'], i['service_id']) for i in pads['installs']) == \
        [('2024-01-20', None), ('2024-02-01', 1)]

    # The service costs exactly what it did before: 100 + 2 x 6 oil + 1 x 40 pads
    service = client.get('/api/services/1').get_json()
    assert service['supplies_cost'] == 52
    assert client.get(f'/api/stats?vehicle_id={vehicle_id}').get_json()['total_spent'] == 152

    # The old tables are left in place as a backup
    db = app_module.get_db()
    assert db.execute('SELECT COUNT(*) FROM supplies').fetchone()[0] == 3
    assert db.execute('SELECT COUNT(*) FROM supply_units').fetchone()[0] == 5
    db.close()


def test_migration_runs_only_once(client, vehicle_id):
    _legacy_db(vehicle_id)
    app_module.init_db()
    app_module.init_db()
    assert len(client.get('/api/parts').get_json()) == 3
    assert sum(len(p['purchases']) for p in client.get('/api/parts').get_json()) == 4


def test_new_parts_get_ids_after_migrated_ones(client, vehicle_id):
    _legacy_db(vehicle_id)
    app_module.init_db()
    assert _part(client, name='New') == 4
    assert os.path.basename(app_module.DATABASE) == 'test.db'
