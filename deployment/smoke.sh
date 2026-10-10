#!/usr/bin/env bash
set -euo pipefail
image=${1:?image required}
revision=${2:?revision required}
smoke_dir=$(mktemp -d)
container="jokes-arena-smoke-$$"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$smoke_dir"
}
trap cleanup EXIT
python3 - "$smoke_dir/receipt.jsonl" <<'PY'
import json,sys
from arena.config import load_config,active_profiles
profiles=list(active_profiles(load_config()).values())[:2]
records=[dict(status='validated',word='coffee',requested_model=p['api_model'],returned_model=p['api_model'],profile_id=p['profile_id'],reasoning=p['reasoning'],prompt_version='joke-v1',joke=f"A coffee joke by {p['name']}.") for p in profiles]
with open(sys.argv[1],'w') as file:
 file.write('\n'.join(json.dumps(r) for r in records))
PY
docker run --rm --network none --user "$(id -u):$(id -g)" --mount "type=bind,src=$smoke_dir,dst=/data" "$image" python -m arena.seed --database /data/arena.db --receipt /data/receipt.jsonl
docker run --detach --name "$container" --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --mount "type=bind,src=$smoke_dir,dst=/data" --publish 127.0.0.1::8080 --env ARENA_ORIGIN=http://arena.test "$image" >/dev/null
address=$(docker port "$container" 8080/tcp)
python3 - "$address" "$revision" <<'PY'
import json,sys,time,urllib.request,urllib.error
base='http://'+sys.argv[1]
for attempt in range(30):
 try:
  with urllib.request.urlopen(base+'/api/ready',timeout=3) as r: ready=json.load(r)
  assert ready=={'status':'ready','revision':sys.argv[2],'pool_pairs':1},ready
  break
 except (OSError,ValueError):
  time.sleep(1)
else: raise SystemExit('Image readiness did not pass')
with urllib.request.urlopen(base+'/preview/') as r: assert b'Next pair' in r.read()
request=urllib.request.Request(base+'/api/battles',data=b'{}',headers={'Content-Type':'application/json','Origin':'http://arena.test'})
with urllib.request.urlopen(request) as r:
 battle=json.load(r);cookie=r.headers['Set-Cookie'].split(';')[0]
assert set(battle)=={'id','word','jokes'}
request=urllib.request.Request(base+'/api/battles/'+battle['id']+'/vote',data=b'{"choice":"left"}',headers={'Content-Type':'application/json','Origin':'http://arena.test','Cookie':cookie})
with urllib.request.urlopen(request) as r: reveal=json.load(r)
assert reveal['saved'] is True and len(reveal['models'])==2
with urllib.request.urlopen(base+'/api/leaderboard') as r: assert json.load(r)['total_votes']==1
try: urllib.request.urlopen(base+'/config/models.json')
except urllib.error.HTTPError as error: assert error.code==404
else: raise AssertionError('Private route was exposed')
print('Docker smoke passed: frontend, SQLite readiness, blind battle, saved vote, model reveal, and private-route rejection.')
PY
