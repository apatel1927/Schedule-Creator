import contextlib
import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ShiftLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DATA_DIR = Path(self.temp.name)
        app.DB_FILE = app.DATA_DIR / 'shiftledger.sqlite3'
        os.environ['ADMIN_USERNAME'] = 'admin'
        os.environ['ADMIN_PASSWORD'] = 'testing-password-very-long'
        app.init_db()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.owner = self.client()
        self.csrf = self.login(self.owner, 'admin', 'testing-password-very-long')['csrf']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def client(self):
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, client, path, body=None, csrf=None, expect=200):
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        if csrf:
            headers['X-CSRF-Token'] = csrf
        req = urllib.request.Request(self.base + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method='POST' if body is not None else 'GET')
        try:
            resp = client.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            resp = exc
        with resp:
            raw = resp.read()
            self.assertEqual(resp.status, expect, raw.decode())
            return json.loads(raw)

    def login(self, client, username, password):
        return self.request(client, '/api/login', {'username': username, 'password': password})

    def create_store(self, name):
        return self.request(self.owner, '/api/stores', {'name': name}, self.csrf)['id']

    def create_employee(self, stores, rate='20.00'):
        return self.request(self.owner, '/api/employees', {'name': 'Test Cashier', 'role': 'Cashier', 'rate': rate,
                             'store_ids': stores}, self.csrf)['id']

    def test_login_and_csrf(self):
        anon = self.client()
        self.request(anon, '/api/data', expect=401)
        self.request(anon, '/api/login', {'username':'admin', 'password':'bad'}, expect=401)
        self.request(self.owner, '/api/stores', {'name':'NoCSRF'}, expect=403)
        self.assertTrue(self.request(self.owner, '/api/session')['authenticated'])

    def test_repeating_schedule_and_actual_hours(self):
        store = 1
        emp = self.create_employee([store])
        request = {'store_id': store, 'employee_id': emp, 'date': '2026-11-02', 'start_date': '2026-11-02',
                   'end_date':'2026-11-16', 'weekdays':[0], 'start_time':'09:00', 'end_time':'17:00',
                   'break_minutes':30, 'status':'draft'}
        self.assertEqual(self.request(self.owner,'/api/shifts/bulk',request,self.csrf)['created'],3)
        self.assertEqual(self.request(self.owner,'/api/shifts/bulk',request,self.csrf)['created'],0)
        shifts=self.request(self.owner,'/api/data')['shifts']
        self.assertEqual(len(shifts),3)
        self.assertEqual(self.request(self.owner,'/api/shifts/publish',{'start':'2026-11-02','end':'2026-11-16','store_id':1},self.csrf)['published'],3)
        self.request(self.owner,'/api/entries/from-shift',{'shift_id':shifts[0]['id']},self.csrf)
        self.request(self.owner,'/api/entries/from-shift',{'shift_id':shifts[0]['id']},self.csrf,expect=400)
        self.request(self.owner,'/api/entries',{'employee_id':emp,'store_id':store,'date':'2026-11-02','category':'Shopping','hours':'2.50'},self.csrf)
        report=self.request(self.owner,'/api/payroll?start=2026-11-02&end=2026-11-08')
        self.assertEqual(report['total_minutes'],600)
        self.assertEqual(report['total_gross_cents'],20000)
        self.assertEqual(report['employees'][0]['categories']['Shopping'],150)

    def test_manager_store_scope_and_rate_updates(self):
        store2=self.create_store('Uptown')
        emp=self.create_employee([1,store2])
        self.request(self.owner,'/api/users',{'username':'manager1','password':'manager-password-1234',
                     'store_ids':[1]},self.csrf)
        manager=self.client()
        token=self.login(manager,'manager1','manager-password-1234')['csrf']
        data=self.request(manager,'/api/data')
        self.assertEqual([s['id'] for s in data['stores']],[1])
        self.assertNotIn('rate_cents',data['employees'][0])
        self.request(manager,'/api/payroll?start=2026-11-02&end=2026-11-08',expect=403)
        self.request(manager,'/api/stores',{'name':'Not allowed'},token,expect=403)
        self.request(manager,'/api/store-rate',{'employee_id':emp,'store_id':store2,'rate':'25.00'},token,expect=400)
        self.request(manager,'/api/store-rate',{'employee_id':emp,'store_id':1,'rate':'25.00'},token)
        data=self.request(manager,'/api/data')
        self.assertEqual(data['employee_stores'][0]['rate_cents'],2500)
        self.request(manager,'/api/entries',{'employee_id':emp,'store_id':1,'date':'2026-11-02',
                      'category':'Pulling orders','hours':'3'},token)
        self.request(manager,'/api/entries',{'employee_id':emp,'store_id':store2,'date':'2026-11-02',
                      'category':'Shopping','hours':'1'},token,expect=400)
        self.assertEqual(self.request(self.owner,'/api/data')['entries'][0]['rate_cents'],2500)

    def test_cross_store_overtime_and_historical_rates(self):
        store2=self.create_store('Second store')
        emp=self.create_employee([1,store2])
        for date_str,hours,store in [('2026-11-02','20',1),('2026-11-03','20',store2)]:
            self.request(self.owner,'/api/entries',{'employee_id':emp,'store_id':store,
                         'date':date_str,'category':'Shift','hours':hours},self.csrf)
        self.request(self.owner,'/api/store-rate',{'employee_id':emp,'store_id':store2,'rate':'25.00'},self.csrf)
        self.request(self.owner,'/api/entries',{'employee_id':emp,'store_id':store2,'date':'2026-11-04',
                     'category':'Shopping','hours':'2'},self.csrf)
        records=self.request(self.owner,'/api/data')['entries']
        self.assertEqual(sorted([e['rate_cents'] for e in records]),[2000,2000,2500])
        report=self.request(self.owner,'/api/payroll?start=2026-11-02&end=2026-11-08')
        self.assertEqual(report['total_minutes'],2520)
        self.assertEqual(report['total_overtime_minutes'],120)
        # $850 straight + half-time at 850 / 42 per hour for 2 OT hours.
        self.assertEqual(report['total_gross_cents'],87024)

    def test_time_record_archive_preserves_audit(self):
        emp=self.create_employee([1])
        record=self.request(self.owner,'/api/entries',{'employee_id':emp,'store_id':1,
                    'date':'2026-11-02','category':'Shopping','hours':'1.25'},self.csrf)['id']
        self.request(self.owner,'/api/entries/delete',{'id':record},self.csrf)
        self.assertEqual(self.request(self.owner,'/api/data')['entries'],[])
        with app.db() as con:
            self.assertIsNotNone(con.execute('SELECT deleted_at FROM time_entries WHERE id=?',(record,)).fetchone()[0])
            self.assertGreaterEqual(con.execute('SELECT count(*) FROM audit_log').fetchone()[0],2)

    def test_owner_can_create_store_and_manager_login_end_to_end(self):
        store=self.create_store('Northside Market')
        store_names=[s['name'] for s in self.request(self.owner,'/api/data')['stores']]
        self.assertIn('Northside Market',store_names)
        result=self.request(self.owner,'/api/users',{'username':'north_manager','password':'very-secure-password-001',
                            'store_ids':[store]},self.csrf)
        accounts=self.request(self.owner,'/api/users')
        self.assertIn(result['id'],[u['id'] for u in accounts['users']])
        manager=self.client()
        csrf=self.login(manager,'north_manager','very-secure-password-001')['csrf']
        self.assertEqual([s['id'] for s in self.request(manager,'/api/data')['stores']],[store])
        # Store and login creation must remain restricted to the owner.
        self.request(manager,'/api/users',{'username':'unauthorized','password':'very-secure-password-222',
                     'store_ids':[store]},csrf,expect=403)
        self.request(manager,'/api/stores',{'name':'Unauthorized Store'},csrf,expect=403)

    def test_week_generation_is_draft_editable_idempotent_and_estimated(self):
        store=1
        employees=[self.create_employee([store], rate='20.00') for _ in range(3)]
        plan={'store_id':store,'open_time':'09:00','close_time':'21:00','slot_minutes':480,
              'break_minutes':30,'staffing':[2,2,2,2,2,0,0]}
        self.request(self.owner,'/api/store-staffing',plan,self.csrf)
        # Existing manual shift occupies a slot and must not be overwritten.
        prior=self.request(self.owner,'/api/shifts',{'employee_id':employees[0],'store_id':store,
                'date':'2026-11-02','start_time':'10:00','end_time':'16:00','break_minutes':0,
                'status':'published','notes':'Set by manager'},self.csrf)['id']
        result=self.request(self.owner,'/api/schedule/generate-week',
                            {'store_id':store,'week_start':'2026-11-02'},self.csrf)
        self.assertEqual(result['created'],9)
        self.assertEqual(result['unfilled'],[])
        self.assertEqual(self.request(self.owner,'/api/schedule/generate-week',
                          {'store_id':store,'week_start':'2026-11-02'},self.csrf)['created'],0)
        shifts=self.request(self.owner,'/api/data')['shifts']
        self.assertEqual(len(shifts),10)
        original=next(s for s in shifts if s['id']==prior)
        self.assertEqual(original['notes'],'Set by manager')
        self.assertEqual(original['status'],'published')
        self.assertEqual(original['generated'],0)
        self.assertTrue(all(s['status']=='draft' and s['generated']==1 for s in shifts if s['id']!=prior))
        weekly=self.request(self.owner,'/api/schedule/estimate?store_id=1&week_start=2026-11-02')
        self.assertEqual(weekly['shift_count'],10)
        self.assertEqual(weekly['staffing_target'],10)
        self.assertEqual(weekly['unfilled_slots'],0)
        self.assertEqual(weekly['planned_minutes'],9*450+360)
        self.assertEqual(weekly['estimated_base_cents'],9*15000+12000)
        generated=next(s for s in shifts if s['generated'])
        self.request(self.owner,'/api/shifts',{'id':generated['id'],'employee_id':generated['employee_id'],
                'store_id':store,'date':generated['shift_date'],'start_time':'11:00','end_time':'16:00',
                'break_minutes':0,'status':'draft','notes':'Manual change'},self.csrf)
        self.assertEqual(next(s for s in self.request(self.owner,'/api/data')['shifts'] if s['id']==generated['id'])['notes'],'Manual change')
        self.assertEqual(self.request(self.owner,'/api/schedule/generate-week',
                          {'store_id':store,'week_start':'2026-11-02'},self.csrf)['created'],0)

    def test_generation_staff_shortage_and_manager_store_boundaries(self):
        store2=self.create_store('Two')
        employee=self.create_employee([1,store2])
        self.request(self.owner,'/api/store-staffing',{'store_id':1,'open_time':'08:00','close_time':'17:00',
                     'slot_minutes':480,'break_minutes':0,'staffing':[2,0,0,0,0,0,0]},self.csrf)
        self.request(self.owner,'/api/users',{'username':'manager2','password':'very-secure-password-789',
                     'store_ids':[1]},self.csrf)
        manager=self.client()
        csrf=self.login(manager,'manager2','very-secure-password-789')['csrf']
        self.request(manager,'/api/schedule/generate-week',{'store_id':store2,'week_start':'2026-11-02'},csrf,expect=400)
        self.request(manager,'/api/store-staffing',{'store_id':store2,'open_time':'09:00','close_time':'17:00',
                     'slot_minutes':480,'break_minutes':0,'staffing':[1]*7},csrf,expect=400)
        result=self.request(manager,'/api/schedule/generate-week',
                            {'store_id':1,'week_start':'2026-11-02'},csrf)
        self.assertEqual(result['created'],1)
        self.assertEqual(result['unfilled'],[{'date':'2026-11-02','remaining':1}])
        self.request(manager,'/api/schedule/estimate?store_id='+str(store2)+'&week_start=2026-11-02',expect=400)
        self.request(self.owner,'/api/schedule/generate-week',{'store_id':1,'week_start':'2026-11-03'},self.csrf,expect=400)

    def test_existing_database_migrates_without_losing_login_or_shifts(self):
        emp=self.create_employee([1])
        shift=self.request(self.owner,'/api/shifts',{'employee_id':emp,'store_id':1,'date':'2026-11-03',
                     'start_time':'09:00','end_time':'17:00','break_minutes':30},self.csrf)['id']
        with app.db() as con:
            con.execute('UPDATE stores SET open_time=? WHERE id=1',('08:00',))
        app.init_db()
        self.assertEqual(self.request(self.owner,'/api/session')['username'],'admin')
        data=self.request(self.owner,'/api/data')
        self.assertEqual(data['shifts'][0]['id'],shift)
        self.assertEqual(data['stores'][0]['open_time'],'08:00')


if __name__ == '__main__':
    unittest.main()
