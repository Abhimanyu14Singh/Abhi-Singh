"""RC beam detail B1 (300x600, 6.0 m clear span) on an A3 sheet at 1:25, via CADCraft JSON commands."""
import json, subprocess, sys
CLI, OUT = sys.argv[1], sys.argv[2]
S = 25                       # drawing scale 1:S, model units = mm
cmds = []
def c(id, **p): cmds.append(f"{id} {json.dumps(p)}")
def L(n): c("setvar", name="CLAYER", value=n)

c("linetype", load="*")
for n, col, lt, lw in [("S-CONC",7,"Continuous",0.50),("S-REBAR",1,"Continuous",0.50),
                       ("S-STIR",3,"Continuous",0.25),("S-CENT",8,"CENTER",0.18),
                       ("S-DIMS",4,"Continuous",0.18),("S-TEXT",2,"Continuous",0.25),
                       ("S-HATCH",8,"Continuous",0.09),("S-SHEET",7,"Continuous",0.70),
                       ("S-SHEET-THIN",7,"Continuous",0.25)]:
    c("layer.new", name=n, color=col, linetype=lt, lineweight=lw)
c("ltscale", scale=0.5)  # PSLTSCALE=1: viewport scale applies
c("style", name="NOTES", font="DejaVu Sans", current=True)
c("dimstyle", name="STR", current=True, DIMSCALE=S, DIMASZ=1.8, DIMTXT=2.5, DIMBLK="ARCHTICK",
  DIMEXO=1.5, DIMEXE=1.5, DIMGAP=0.8, DIMTAD=1, DIMDEC=0, DIMTXSTY="NOTES")
c("mleaderstyle", name="STR", current=True, textHeight=2.5*S, arrowSize=2.5*S,
  landingGap=1*S, dogleg=4*S, textStyle="NOTES")

th = lambda mm: mm*S         # paper mm -> model mm
D, span, cw, cov = 600, 6000, 400, 40
# ---------- ELEVATION (origin at left column face, beam soffit)
ox, oy = 0, 3200
P = lambda x, y: [ox+x, oy+y]
L("S-CONC")
for x in (-cw, 0, span, span+cw):
    lo, hi = (-700, D+500)
    if x in (0, span):
        c("line", points=[P(x,lo), P(x,0)]); c("line", points=[P(x,D), P(x,hi)])
    else:
        c("line", points=[P(x,lo), P(x,hi)])
c("line", points=[P(0,0), P(span,0)]); c("line", points=[P(-cw,D), P(span+cw,D)])
L("S-CENT")
for x in (-cw/2, span+cw/2): c("line", points=[P(x,-900), P(x,D+700)])
L("S-REBAR")
y_t, y_b = D-cov-10-10, cov+10+10
c("pline", vertices=[P(-cw+60,y_t-500), P(-cw+60,y_t), P(span+cw-60,y_t), P(span+cw-60,y_t-500)])
c("pline", vertices=[P(-cw+90,y_b+400), P(-cw+90,y_b), P(span+cw-90,y_b), P(span+cw-90,y_b+400)])
c("line", points=[P(-cw+90,y_t-45), P(1800,y_t-45)])
c("line", points=[P(span-1800,y_t-45), P(span+cw-90,y_t-45)])
L("S-STIR")
xs = list(range(50, 1501, 100)) + list(range(1700, span-1499, 200)) + list(range(span-1450, span-49, 100))
for x in sorted(set(xs)): c("line", points=[P(x,cov), P(x,D-cov)])
L("S-DIMS")
c("dimlinear", p1=P(0,0), p2=P(span,0), at=P(span/2,-1150))
for a, b in ((0,1500),(1500,span-1500),(span-1500,span)):
    c("dimlinear", p1=P(a,0), p2=P(b,0), at=P((a+b)/2,-800))
c("dimlinear", p1=P(span+cw,0), p2=P(span+cw,D), at=P(span+cw+400,D/2))
L("S-TEXT")
c("mleader", points=[P(1200,y_t-45), P(1700,D+1100)], text="3T20 CONT. TOP + 2T20 x 1800 EXTRA")
c("mleader", points=[P(span-1000,y_b), P(span-1500,-1650)], text="4T20 CONT. BOTTOM")
c("mleader", points=[P(1250,D/2), P(1700,-1650)], text="T10 LINKS @100 (END ZONES)")
c("mleader", points=[P(3500,D/2), P(3900,D+1300)], text="T10 LINKS @200 (MID ZONE)")
# section cut A-A
L("S-SHEET-THIN")
c("pline", vertices=[P(4200,D+250), P(4200,D+450), P(4350,D+450)], width=th(0.5))
c("pline", vertices=[P(4200,-250), P(4200,-450), P(4350,-450)], width=th(0.5))
L("S-TEXT")
c("text", at=P(4450,D+450), text="A", height=th(4), justify="ML")
c("text", at=P(4450,-450), text="A", height=th(4), justify="ML")
def title(x, y, t, sc):
    L("S-TEXT"); c("text", at=[x,y], text=t, height=th(5), justify="BC")
    c("text", at=[x,y-th(2)], text=sc, height=th(3), justify="TC")
    L("S-SHEET-THIN"); c("pline", vertices=[[x-th(45),y-th(1)],[x+th(45),y-th(1)]], width=th(0.5))
title(span/2, oy-2300, "BEAM B1 - ELEVATION", "SCALE 1:25")

# ---------- SECTION A-A
sx, sy, b = 7400, 3200, 300
L("S-CONC"); c("rectang", p1=[sx,sy], p2=[sx+b,sy+D])
L("S-HATCH"); c("hatch", points=[[sx+10,sy+10]], pattern="AR-CONC", scale=0.4)
L("S-STIR"); c("rectang", p1=[sx+cov,sy+cov], p2=[sx+b-cov,sy+D-cov], fillet=20)
c("line", points=[[sx+cov+5,sy+D-cov-5],[sx+cov+60,sy+D-cov-60]])
L("S-REBAR")
for i in range(4): c("donut", center=[sx+cov+20+i*(b-2*cov-40)/3, sy+y_b], inside=0, outside=20)
for i in range(3): c("donut", center=[sx+cov+20+i*(b-2*cov-40)/2, sy+y_t], inside=0, outside=20)
L("S-DIMS")
c("dimlinear", p1=[sx,sy], p2=[sx+b,sy], at=[sx+b/2,sy-350])
c("dimlinear", p1=[sx,sy], p2=[sx,sy+D], at=[sx-350,sy+D/2])
L("S-TEXT")
c("mleader", points=[[sx+b-cov-20,sy+y_t],[sx+b+450,sy+D+400]], text="3T20")
c("mleader", points=[[sx+b-cov-20,sy+y_b],[sx+b+450,sy-400]], text="4T20")
c("mleader", points=[[sx+b-cov,sy+D/2],[sx+b+450,sy+D/2]], text="T10 LINKS")
title(sx+b/2, sy-1100, "SECTION A-A", "SCALE 1:25")

# ---------- SHEET: paper-space layout, A3 landscape, units = paper mm
c("layer.new", name="S-VPORT", color=8, linetype="Continuous", lineweight=0.13)
c("layer.set", name="S-VPORT", plot=False)
c("layout.new", name="S-201", viewport=False)
c("layout.set", name="S-201")
c("pagesetup", layout="S-201", device="CADCraft PDF", paper="A3", landscape=True, margins=[0,0,0,0],
  plotStyleTable="monochrome.ctb", displayPlotStyles=True, lineweights=True, scale=1, center=False)
W, H, m = 420, 297, 10
L("S-SHEET"); c("rectang", p1=[m+10,m], p2=[W-m,H-m])           # 20 mm binding margin
tbx = W-m-180
L("S-SHEET-THIN")
c("rectang", p1=[tbx,m], p2=[W-m,m+40])
for y in (10, 20, 30): c("line", points=[[tbx,m+y],[W-m,m+y]])
c("line", points=[[tbx+110,m],[tbx+110,m+30]])
L("S-TEXT")
row = lambda i: m+10*i+5
c("text", at=[tbx+3,row(3)], text="SAMPLE OFFICE BLOCK - LEVEL 2 FRAMING", height=3.5, justify="ML")
c("text", at=[tbx+3,row(2)], text="BEAM B1 - REINFORCEMENT DETAILS", height=3, justify="ML")
c("text", at=[tbx+3,row(1)], text="DRAWN: AS   CHECKED: --   DATE: 2026-10-22", height=2.2, justify="ML")
c("text", at=[tbx+3,row(0)], text="SCALE: 1:25 @ A3", height=2.2, justify="ML")
c("text", at=[tbx+113,row(1)], text="DWG No.  S-201", height=3.5, justify="ML")
c("text", at=[tbx+113,row(0)], text="REV  A", height=3, justify="ML")
c("mtext", at=[tbx, H-m-8], width=175, height=2.5, text=
  "GENERAL NOTES\\P1. CONCRETE: C30/37, EXPOSURE CLASS XC1.\\P2. REINFORCEMENT: B500B, T = HIGH-YIELD.\\P3. NOMINAL COVER TO LINKS: 40 mm.\\P4. LAPS: 50 x BAR DIAMETER UNLESS NOTED.\\P5. DO NOT SCALE. ALL DIMENSIONS IN mm.")
L("S-VPORT")
c("mview", p1=[m+12,m+42], p2=[W-m-2,H-m-2])
c("mspace", at=[200,150])
c("zoom", mode="center", center=[4100,3300], factor=1/S, xp=True)
c("pspace")
args = [CLI, "run", "--metric"] + sum((["--cmd", x] for x in cmds), [])
args += ["--cmd", 'exportpdf ' + json.dumps({"path": OUT+".pdf", "layout": "S-201"})]
args += ["--save", OUT+".dxf"]
r = subprocess.run(args, capture_output=True, text=True)
print(r.returncode, r.stderr[-1500:], r.stdout[-400:])
