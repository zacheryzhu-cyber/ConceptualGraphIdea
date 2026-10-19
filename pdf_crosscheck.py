"""Cross-check DEXPI logical connections against the pipes drawn in the COMOS vector PDF of the same sheet.
Generic: uses only DEXPI object extents + PDF line geometry (no tags hard-coded)."""
import fitz, xml.etree.ElementTree as ET, collections, itertools, sys, json
pdf, dexpi = sys.argv[1], sys.argv[2]
page=fitz.open(pdf)[0]; H=page.rect.height
root=ET.parse(dexpi).getroot()
S=page.rect.width/float(root.find('Drawing/Extent/Max').get('X'))      # pt per mm
parent={c:p for p in root.iter() for c in p}
def fl(e):
    for g in e.findall('GenericAttributes'):
        for a in g.findall('GenericAttribute'):
            if a.get('Name')=='FullLabel': return a.get('Value')
def name(e):
    return e.get('TagName') or fl(e) or e.get('ID')
# --- objects that can be connection ends: equipment, piping components, off-page connectors (not nozzles: they map to owner)
KEEP=('Equipment','PipingComponent','PipeOffPageConnector')
objs={}
lib={x for sc in root.findall('ShapeCatalogue') for x in sc.iter()}
for e in root.iter():
    if e in lib: continue
    if e.tag in KEEP and e.get('ID') and e.find('Extent') is not None:
        mn,mx=e.find('Extent/Min'),e.find('Extent/Max')
        x0,y0,x1,y1=float(mn.get('X'))*S,H-float(mx.get('Y'))*S,float(mx.get('X'))*S,H-float(mn.get('Y'))*S
        if e.tag=='Equipment' and parent.get(e) is not None and parent[e].tag=='Equipment': continue
        objs[e.get('ID')]={'name':name(e),'cls':e.get('ComponentClass'),'box':(x0,y0,x1,y1)}
# nozzles inherit their owner equipment; extend owner's box to cover nozzle boxes
for e in root.iter():
    if e.tag in('Nozzle','Nozzles') and e.find('Extent') is not None:
        own=parent.get(e)
        if own is not None and own.get('ID') in objs:
            mn,mx=e.find('Extent/Min'),e.find('Extent/Max')
            b=objs[own.get('ID')]['box']
            objs[own.get('ID')]['box']=(min(b[0],float(mn.get('X'))*S),min(b[1],H-float(mx.get('Y'))*S),max(b[2],float(mx.get('X'))*S),max(b[3],H-float(mn.get('Y'))*S))
# --- process pipe lines from the PDF: solid, the heaviest width used for lines
segs=[]
for d in page.get_drawings():
    if d.get('dashes') not in (None,'[] 0',''): continue
    for it in d['items']:
        if it[0]=='l': segs.append(((it[1].x,it[1].y),(it[2].x,it[2].y),round(d.get('width') or 0,2)))
# calibrate: the pipe line style is the width whose segment ends best match DEXPI pipe centre-line points
cl=[(float(c.get('X'))*S,H-float(c.get('Y'))*S) for seg in root.iter('PipingNetworkSegment') for c in seg.iter('Coordinate')]
def hits(w):
    n=0
    for a,b,ww in segs:
        if ww!=w: continue
        n+=any(abs(a[0]-x)<1.5 and abs(a[1]-y)<1.5 for x,y in cl)+any(abs(b[0]-x)<1.5 and abs(b[1]-y)<1.5 for x,y in cl)
    return n
score={w:hits(w) for w in {w for *_,w in segs}}
PW=max(score,key=score.get)
print('line width vs DEXPI pipe-route matches:',{k:v for k,v in sorted(score.items())})
pipes=[(a,b) for a,b,w in segs if w==PW]
tol=1.5
def inbox(pt,b,m=2.0): return b[0]-m<=pt[0]<=b[2]+m and b[1]-m<=pt[1]<=b[3]+m
# union-find over pipe segments: join if endpoints touch or an endpoint lies on another segment (T-junction)
par=list(range(len(pipes)))
def f(i):
    while par[i]!=i: par[i]=par[par[i]]; i=par[i]
    return i
def onseg(pt,a,b):
    (x,y),(x1,y1),(x2,y2)=pt,a,b
    if min(x1,x2)-tol<=x<=max(x1,x2)+tol and min(y1,y2)-tol<=y<=max(y1,y2)+tol:
        cross=abs((x2-x1)*(y1-y)-(x1-x)*(y2-y1)); L=((x2-x1)**2+(y2-y1)**2)**.5 or 1
        return cross/L<=tol
    return False
inside_obj=lambda pt: any(inbox(pt,o['box'],0) for o in objs.values())
for i,j in itertools.combinations(range(len(pipes)),2):
    a,b=pipes[i];c,d=pipes[j]
    if any(onseg(q,c,d) and not inside_obj(q) for q in (a,b)) or any(onseg(q,a,b) and not inside_obj(q) for q in (c,d)):
        par[f(i)]=f(j)
runs=collections.defaultdict(list)
for i in range(len(pipes)): runs[f(i)].append(i)
# objects touched by each drawn pipe run (pipe endpoint inside / at the object's box)
geo=set()
for rid,ids in runs.items():
    touched=set()
    for i in ids:
        for q in pipes[i]:
            for oid,o in objs.items():
                if inbox(q,o['box']): touched.add(oid)
    for a,b in itertools.combinations(sorted(touched),2): geo.add(frozenset((a,b)))
# objects drawn directly against each other (e.g. reducer touching a pump flange)
for (a,oa),(b,ob) in itertools.combinations(objs.items(),2):
    A,B=oa['box'],ob['box']
    if not oa['cls'] or not ob['cls']: continue            # untyped items (e.g. motors) drawn on their equipment = composition, not piping
    if A[0]-1<=B[2] and B[0]-1<=A[2] and A[1]-1<=B[3] and B[1]-1<=A[3]: geo.add(frozenset((a,b)))
# --- DEXPI logical neighbours along piping segments (chain From -> items -> To, nozzles -> owner)
byid={e.get('ID'):e for e in root.iter() if e.get('ID')}
def owner(i):
    e=byid.get(i)
    while e is not None and e.get('ID') not in objs: e=parent.get(e)
    return e.get('ID') if e is not None else None
logic=set()
for seg in root.iter('PipingNetworkSegment'):
    c=seg.find('Connection')
    chain=[owner(c.get('FromID'))] if c is not None and c.get('FromID') else []
    chain+=[x.get('ID') for x in seg if x.get('ID') in objs]
    if c is not None and c.get('ToID'): chain.append(owner(c.get('ToID')))
    chain=[x for x in chain if x]
    for a,b in zip(chain,chain[1:]):
        if a!=b: logic.add(frozenset((a,b)))
N=lambda pair: ' — '.join(sorted(f"{objs[x]['name']} [{objs[x]['cls']}, {fl(byid[x]) or ''}]" for x in pair))
conf=geo&logic; only_geo=geo-logic; only_logic=logic-geo
print(f"pipe line width chosen: {PW} pt, {len(pipes)} segments in {len(runs)} drawn runs; {len(objs)} DEXPI objects placed on the drawing")
print(f"confirmed by both: {len(conf)}   drawn only (missing in DEXPI): {len(only_geo)}   DEXPI only (not seen as drawn): {len(only_logic)}")
print("\nDRAWN BUT MISSING IN DEXPI:");[print('  ',N(p)) for p in sorted(only_geo,key=N)]
print("\nIN DEXPI BUT NOT MATCHED ON THE DRAWING:");[print('  ',N(p)) for p in sorted(only_logic,key=N)]

# ---- overlay picture: green = confirmed by DEXPI and drawing, red = drawn but missing in DEXPI
if len(sys.argv) > 3:
    doc2 = fitz.open(pdf); pg = doc2[0]
    def ctr(o): b = objs[o]['box']; return fitz.Point((b[0]+b[2])/2, (b[1]+b[3])/2)
    for pair, col, wd in [(p, (0, .6, 0), 3) for p in conf] + [(p, (.9, 0, 0), 6) for p in only_geo]:
        a, b = sorted(pair)
        pg.draw_line(ctr(a), ctr(b), color=col, width=wd)
        for o in (a, b):
            pg.draw_rect(fitz.Rect(objs[o]['box']), color=col, width=1.5)
    pg.insert_text((60, 160), "Green: connection in DEXPI and drawn in PDF    Red: drawn in PDF but missing in DEXPI", fontsize=22, color=(0, 0, 0))
    pg.get_pixmap(dpi=110, clip=fitz.Rect(0, 130, page.rect.width, page.rect.height*0.80)).save(sys.argv[3])
    print('overlay ->', sys.argv[3])
