import tempfile
import unittest
from pathlib import Path
from app.thread_library import ThreadLibrary
from app.sessions import SessionCatalog

class Adapter:
    def sessions(self, limit=500):
        return {'sessions':[
            {'id':'new-test','createdAt':3000,'updatedAt':3000},
            {'id':'old-work','createdAt':1000,'updatedAt':5000},
            {'id':'old-pin','createdAt':100,'updatedAt':100},
        ]}

class LibraryTests(unittest.TestCase):
    def test_activity_and_pin_sort_before_pagination_and_reversible_hide(self):
        with tempfile.TemporaryDirectory() as d:
            store=ThreadLibrary(Path(d)/'library.json')
            catalog=SessionCatalog(lambda:Adapter(),library=store)
            self.assertEqual(catalog.rows(limit=1)[0]['id'],'old-work')
            store.update('old-pin',{'pinned':True,'name':'Work'})
            self.assertEqual(catalog.rows(limit=1)[0]['id'],'old-pin')
            store.update('old-pin',{'hidden':True})
            self.assertEqual([r['id'] for r in catalog.rows(limit=2)],['old-work','new-test'])
            restored=ThreadLibrary(Path(d)/'library.json')
            restored.update('old-pin',{'hidden':False})
            self.assertTrue(restored.snapshot()['old-pin']['pinned'])
            self.assertEqual(catalog.rows(limit=1)[0]['id'],'old-pin')
            self.assertEqual(catalog.rows(limit=1,offset=1)[0]['id'],'old-work')

    def test_invalid_boolean_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            store=ThreadLibrary(Path(d)/'library.json')
            with self.assertRaises(ValueError):store.update('a',{'hidden':'false'})
            self.assertEqual(store.snapshot(),{})
