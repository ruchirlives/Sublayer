"""Real stdio integration check; no desktop/third-party app interaction."""
import json,os,pathlib,subprocess,tempfile
root=pathlib.Path(__file__).resolve().parents[1]
env=dict(os.environ,ORCHFLOW_PYTHON=str(root/'.venv-imaging/Scripts/python.exe'),ORCHFLOW_USD_ROOT='I:/AI/OpenUSD-runtime/25.08')
executable=max((p for p in [pathlib.Path('I:/AI/OrchFlow-build/debug/orchflow-mcp.exe'),pathlib.Path('I:/AI/OrchFlow-build/debug/orchflow-mcp-updated.exe')] if p.exists()),key=lambda p:p.stat().st_mtime_ns)
server=subprocess.Popen([str(executable)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env)
seq=0
def rpc(method,params={}):
 global seq
 seq+=1;server.stdin.write(json.dumps({'jsonrpc':'2.0','id':seq,'method':method,'params':params})+'\n');server.stdin.flush()
 reply=json.loads(server.stdout.readline());assert reply['id']==seq,reply;assert 'error' not in reply,reply;return reply['result']
def call(name,arguments={},error=False):
 result=rpc('tools/call',{'name':name,'arguments':arguments});assert result['isError']==error,result
 return result if error else json.loads(next(c['text'] for c in result['content'] if c['type']=='text'))
try:
 assert rpc('initialize',{'protocolVersion':'2025-11-25','clientInfo':{'name':'integration-test','version':'1'},'capabilities':{}})['protocolVersion']=='2025-11-25'
 server.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'})+'\n');server.stdin.flush()
 tools=rpc('tools/list')['tools'];assert len(tools)>=16
 assert not next(t for t in tools if t['name']=='export_materialx')['annotations']['readOnlyHint']
 project=call('project_new',{'sourcePath':str(root/'samples/layered.usda')})
 assert call('project_validate',{'project':project})['valid']
 assert call('scene_children',{'project':project,'nodeId':'output'})['children'][0]['path']=='/Scene'
 invalid=json.loads(json.dumps(project));invalid['edges'].append({'id':'cycle','source':'output','target':'source'})
 call('project_validate',{'project':invalid},True)
 with tempfile.TemporaryDirectory(prefix='orchflow-mcp-') as folder:
  path=str(pathlib.Path(folder)/'test.sublayer');saved=call('project_save',{'path':path,'project':project});loaded=call('project_load',{'path':path});assert loaded['revision']==saved['revision']
  call('project_save',{'path':path,'project':project},True)
  call('project_save',{'path':path,'project':project,'expectedRevision':loaded['revision']})
 caps=call('usd_capabilities');assert caps['viewport'],caps
 result=rpc('tools/call',{'name':'usd_render','arguments':{'project':project,'nodeId':'output','width':320,'height':240,'renderer':'HdStormRendererPlugin'}})
 assert not result['isError'],result;assert any(c['type']=='image' and c['mimeType']=='image/png' for c in result['content'])
 project['nodes'][1]['data']['operation']='materialx'
 project['nodes'][1]['data']['label']='MaterialX'
 assert call('project_validate',{'project':project})['valid']
 assert '<standard_surface' in call('materialx_document',{'project':project,'nodeId':'transform'})['document']
 with tempfile.TemporaryDirectory(prefix='orchflow-materialx-') as folder:
  path=str(pathlib.Path(folder)/'material.mtlx');call('export_materialx',{'project':project,'nodeId':'transform','destination':path});assert pathlib.Path(path).exists()
  call('export_materialx',{'project':project,'nodeId':'transform','destination':path},True)
 result=rpc('tools/call',{'name':'usd_render','arguments':{'project':project,'nodeId':'output','width':320,'height':240,'renderer':'HdStormRendererPlugin'}})
 assert not result['isError'],result
 print('MCP handshake, 16 tools, project generation/validation, safe saves, MaterialX document, and real Storm images passed.')
finally:
 server.stdin.close()
 try:server.wait(timeout=15)
 except subprocess.TimeoutExpired:server.kill();server.wait()
 if server.returncode:print(server.stderr.read());raise SystemExit(server.returncode)
