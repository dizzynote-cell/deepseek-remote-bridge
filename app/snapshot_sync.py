"""Push read-only DSH snapshots; commands continue on the independent relay."""
import hashlib
import json
import threading
import time
import urllib.request
import urllib.parse

class SnapshotSync:
    def __init__(self,relay,state,origin,base_path='/dsh/'):
        self.relay,self.state,self.origin,self.base_path=relay,state,origin.rstrip('/'),base_path
        self.stop=threading.Event();self.threads=[];self.last_success=0;self.last_error=''
        self.last_read={};self.digests={};self.interests=[]

    def read(self,path):
        # Call only bridge-owned read endpoints; never follow redirects or send model credentials.
        request=urllib.request.Request(self.origin+self.base_path+path.lstrip('/'))
        with urllib.request.urlopen(request,timeout=60) as response:
            raw=response.read(32*1024*1024+1)
        if len(raw)>32*1024*1024:raise ValueError('snapshot_too_large')
        return json.loads(raw)

    def send(self,payload):
        payload=dict(payload,version=1)
        if len(json.dumps(payload).encode())>31*1024*1024:raise ValueError('snapshot_too_large')
        result=self.relay._remote('/snapshot',payload,timeout=35)
        self.last_success=time.time();self.last_error=''
        if isinstance(result.get('historyRequests'),list):
            self.interests=result['historyRequests'];self.requests_ready=True
        return result

    def history_step(self):
        now=time.time();payload={}
        requests=self.interests if getattr(self,'requests_ready',False) else self.relay._remote('/snapshot-requests',timeout=25).get('historyRequests',[])
        if now-getattr(self,'listed_at',0)>=10:
            rows=self.state.catalog.rows(limit=2000,include_hidden=True)
            for row in rows:
                row['updatedAt']=row.get('bridgeUpdatedAt')
                if self.state.tasks.is_running(row['id']):row['status']='运行中'
            payload['summaries']=rows
            payload['metadata']={'projects':self.state.catalog.projects(),'library':{'threads':self.state.thread_library.snapshot()}}
            self.rows=rows;self.listed_at=now
        candidates=[]
        for request in requests:
            identity=request.get('threadId');pending=request.get('requestId')!=request.get('completedId')
            if pending or now-float(request.get('viewedAt') or 0)<30:
                if now-self.last_read.get(identity,0)>=2:candidates.append((0 if pending else 1,self.last_read.get(identity,0),identity,request))
        for row in getattr(self,'rows',[])[:3]:
            identity=row['id'];running=self.state.tasks.is_running(identity)
            if (running and now-self.last_read.get(identity,0)>=2) or identity not in self.last_read:
                candidates.append((2 if running else 3,self.last_read.get(identity,0),identity,None))
        if candidates:
            _,_,identity,request=min(candidates,key=lambda item:(item[0],item[1],item[2] or ''))
            limit=str(request.get('turnLimit','6')) if request else '6'
            path='/api/thread/'+urllib.parse.quote(identity,safe='')+'?turnLimit='+urllib.parse.quote(limit,safe='')
            try:
                thread=self.read(path)['thread'];thread['updatedAt']=thread.get('bridgeUpdatedAt') or thread.get('createdAt')
                digest=hashlib.sha256(json.dumps(thread,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                pending=request and request.get('requestId')!=request.get('completedId')
                if pending:thread['bridgeRequestId']=request['requestId']
                if pending or self.digests.get(identity)!=digest:payload['threads']=[thread]
                payload['checkedThreads']=[identity]
                self.digests[identity]=digest;self.last_read[identity]=time.time()
            except Exception as error:
                self.last_read[identity]=time.time()
                if request:payload['historyErrors']=[{'threadId':identity,'requestId':request['requestId'],'message':'本机历史暂时无法同步（'+type(error).__name__+'）'}]
                self.last_error=type(error).__name__
        try:return self.send(payload)
        except Exception:
            if candidates:self.digests.pop(identity,None)
            raise

    def metadata_step(self):
        metadata={}
        # Each result is optional; an unavailable wallet never becomes a zero balance.
        for key,path in [('status','/api/status'),('info','/api/bridge-info'),('models','/api/models'),('preferences','/api/preferences'),('voice','/api/voice/config'),('balance','/api/balance'),('permissions','/api/permissions')]:
            if self.stop.is_set():return
            try:metadata[key]=self.read(path)
            except Exception:pass
            if key=='voice' and metadata:
                self.send({'metadata':metadata});metadata={}
        return self.send({'metadata':metadata})

    def loop(self,step,interval):
        while not self.stop.is_set():
            try:step()
            except Exception as error:self.last_error=type(error).__name__
            self.stop.wait(interval)

    def start(self):
        for name,step,interval in [('history',self.history_step,1),('metadata',self.metadata_step,15)]:
            thread=threading.Thread(target=self.loop,args=(step,interval),name='dsh-snapshot-'+name,daemon=True)
            self.threads.append(thread);thread.start()

    def shutdown(self):self.stop.set()
