import unittest
from types import SimpleNamespace
from app.snapshot_sync import SnapshotSync

class Relay:
    def __init__(self):self.sent=[];self.requests=[];self.fail=False
    def _remote(self,path,payload=None,timeout=None):
        if path=='/snapshot-requests':return {'historyRequests':self.requests}
        self.sent.append(payload)
        if self.fail:raise ConnectionError('synthetic')
        return {}

class SyncTests(unittest.TestCase):
    def make(self):
        relay=Relay();catalog=SimpleNamespace(rows=lambda **kw:[{'id':'session-demo','name':'Synthetic','bridgeUpdatedAt':1700000000000}],projects=lambda:[])
        state=SimpleNamespace(catalog=catalog,tasks=SimpleNamespace(is_running=lambda _:False),thread_library=SimpleNamespace(snapshot=lambda:{}))
        sync=SnapshotSync(relay,state,'http://127.0.0.1:1')
        paths=[]
        def read(path):
            paths.append(path)
            return {'thread':{'id':'session-demo','turns':[],'bridgeTotalTurns':0}} if path.startswith('/api/thread/') else {}
        sync.read=read;return relay,sync,paths
    def test_initial_push_and_on_demand_all_history(self):
        relay,sync,paths=self.make();sync.history_step()
        self.assertIn('summaries',relay.sent[-1]);self.assertEqual(relay.sent[-1]['version'],1)
        self.assertIn('threads',relay.sent[-1]);self.assertTrue(all(path.startswith('/api/') for path in paths))
        relay.requests=[{'threadId':'session-demo','turnLimit':'all','requestId':'new','completedId':'old','viewedAt':1}];sync.last_read.clear();sync.history_step()
        self.assertIn('turnLimit=all',paths[-1]);self.assertEqual(relay.sent[-1]['threads'][0]['bridgeRequestId'],'new')
    def test_transport_failure_does_not_fabricate_local_history_error(self):
        relay,sync,paths=self.make();relay.fail=True
        with self.assertRaises(ConnectionError):sync.history_step()
        self.assertNotIn('historyErrors',relay.sent[-1]);self.assertNotIn('session-demo',sync.digests)
    def test_fast_metadata_publishes_before_wallet(self):
        relay,sync,paths=self.make();sync.metadata_step()
        self.assertIn('voice',relay.sent[0]['metadata']);self.assertNotIn('balance',relay.sent[0]['metadata']);self.assertIn('balance',relay.sent[-1]['metadata'])
