import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
FEES={"MNQ":1.86,"NNQ":1.68,"MES":1.88,"M2K":1.88,"MYM":1.88,"MGC":5.88,"MCL":3.02,"NQ":5.14,"ES":5.14,"RTY":5.14,"YM":5.14,"GC":8.08,"CL":8.08}
MULTS={"MNQ":2.0,"NNQ":0.2,"MES":5.0,"M2K":5.0,"MYM":0.5,"MGC":10.0,"MCL":100.0,"NQ":20.0,"ES":50.0,"RTY":50.0,"YM":5.0,"GC":100.0,"CL":1000.0}
PROFILES=["OPTION_A","OPTION_B","OPTION_C","SNAPPED","LEAN_2_RUNG","OPTION_F_TREND_BREAKER","OPTION_G_MT_CORE","OPTION_H_MT_SPAN"]
def resolve_v5_economics(inst, low, high):
    s=str(inst or "MNQ").upper()
    return float(MULTS.get(s,2.0)), float(FEES.get(s,1.88))
@dataclass
class V5SelectionContext:
    range_low:float; range_high:float; multiplier:float; fee_per_rt:float
    max_fee_drag_pct:float=0.25; s_bias:float=0.0; macro_regime:str="RANGE"
    d_front:Optional[float]=None; d_back:Optional[float]=None; s_front:Optional[float]=None; s_back:Optional[float]=None
    demand_mit:str="FRESH"; supply_mit:str="FRESH"; drawdown_type:str="EOD"
    elapsed_seconds:float=0.0; max_lifespan_seconds:float=3840.0
    empirical_stats:Dict[str,Dict[str,float]]=field(default_factory=dict)
class V5ProfileSelector:
    W={"geometry":0.35,"symmetry":0.25,"mitigation":0.25,"lifecycle":0.15}
    def __init__(self,tau=0.15,lmax=0.25,n0=20.0): self.tau,self.lmax,self.n0=tau,lmax,n0
    def evaluate(self,ctx):
        span=max(1e-9,ctx.range_high-ctx.range_low)
        dp_min=(8.0*ctx.fee_per_rt)/max(1e-9,ctx.multiplier*ctx.max_fee_drag_pct)
        wd=abs(ctx.d_front-ctx.d_back)/span if (ctx.d_front is not None and ctx.d_back is not None) else 0.1875
        ws=abs(ctx.s_back-ctx.s_front)/span if (ctx.s_front is not None and ctx.s_back is not None) else 0.1875
        wl=max(wd,ws)*16.0
        df=ctx.d_front if ctx.d_front is not None else (ctx.range_low+0.3125*span)
        sf=ctx.s_front if ctx.s_front is not None else (ctx.range_high-0.3125*span)
        rc=max(0.0,(sf-df)/span)
        sym=0.5*((1.0-abs(wd-ws)/max(wd,ws,1e-6))+max(0.0,1.0-abs(0.5*(df+sf)-0.5*(ctx.range_low+ctx.range_high))/(0.5*span)))
        age=min(1.0,max(0.0,ctx.elapsed_seconds/max(1.0,ctx.max_lifespan_seconds)))
        ranks,utils={},{}
        for p in PROFILES:
            ok,why=self._gate(p,ctx,span,dp_min)
            if not ok: ranks[p]={"feasible":False,"utility":0.0,"probability":0.0,"reasons":why}; continue
            u=max(0.0,self.W["geometry"]*self._p2(p,wl,rc)+self.W["symmetry"]*self._p3(p,sym)+self.W["mitigation"]*self._p4(p,ctx.demand_mit,ctx.supply_mit)+self.W["lifecycle"]*self._p5(p,ctx.drawdown_type,age)+self._emp(p,ctx.empirical_stats))
            utils[p]=u; ranks[p]={"feasible":True,"utility":round(u,4),"reasons":self._tags(p,ctx,wl,rc,sym,age)}
        if utils:
            mu=max(utils.values()); ex={p:math.exp((u-mu)/self.tau) for p,u in utils.items()}; den=sum(ex.values())
            for p,v in ex.items(): ranks[p]["probability"]=round(v/den,4)
            best=max(utils.items(),key=lambda kv:kv[1])[0]
        else: best="NONE_BLOCKED"
        return {"best_fit":best,"diagnostics":{"dp_min_fee_floor":round(dp_min,2),"symmetry_score":round(sym,3)},"rankings":ranks}
    def _gate(self,p,ctx,span,dp_min):
        if ctx.demand_mit=="BREACHED" and ctx.supply_mit=="BREACHED": return False,["Both MTs BREACHED"]
        if (ctx.demand_mit=="BREACHED" or ctx.supply_mit=="BREACHED") and p!="OPTION_F_TREND_BREAKER": return False,["One MT breached"]
        if span<dp_min and p not in ("LEAN_2_RUNG","OPTION_F_TREND_BREAKER"): return False,[f"Span {span:.2f} < 25% drag floor {dp_min:.2f}"]
        if ctx.drawdown_type.upper()=="INTRADAY" and p in ("OPTION_A","OPTION_H_MT_SPAN"): return False,["Intraday DD heat limit"]
        return True,[]
    def _p1(self,p,ctx):
        ab=abs(ctx.s_bias)
        if "EXPANSION" in ctx.macro_regime.upper() or ab>0.35: return min(1.0,0.65+0.5*ab) if p=="OPTION_F_TREND_BREAKER" else (0.35 if p in ("LEAN_2_RUNG","OPTION_B") else 0.10)
        return 0.15 if p=="OPTION_F_TREND_BREAKER" else (0.90-0.4*ab)
    def _p2(self,p,wl,rc):
        if wl>5.0 or rc<0.25: d={"OPTION_B":1.0,"LEAN_2_RUNG":0.8,"OPTION_G_MT_CORE":0.65,"SNAPPED":0.45,"OPTION_C":0.3,"OPTION_A":0.2,"OPTION_H_MT_SPAN":0.25,"OPTION_F_TREND_BREAKER":0.5}
        elif wl<3.0: d={"OPTION_C":0.95,"OPTION_G_MT_CORE":0.9,"SNAPPED":0.85,"LEAN_2_RUNG":0.75,"OPTION_B":0.6,"OPTION_A":0.55,"OPTION_H_MT_SPAN":0.65,"OPTION_F_TREND_BREAKER":0.6}
        else: d={"SNAPPED":0.95,"OPTION_G_MT_CORE":0.95,"OPTION_A":0.85,"OPTION_H_MT_SPAN":0.8,"OPTION_B":0.75,"OPTION_C":0.7,"LEAN_2_RUNG":0.65,"OPTION_F_TREND_BREAKER":0.7}
        return d.get(p,0.5)
    def _p3(self,p,sym): return sym if p in ("OPTION_A","OPTION_B","OPTION_C") else (1.0-0.35*sym if p in ("SNAPPED","OPTION_G_MT_CORE","OPTION_H_MT_SPAN","LEAN_2_RUNG") else 0.6)
    def _p4(self,p,dm,sm):
        st={dm,sm}
        if st=={"FRESH"}: return {"OPTION_A":0.95,"SNAPPED":0.95,"OPTION_G_MT_CORE":0.9,"OPTION_H_MT_SPAN":0.9,"OPTION_B":0.85,"OPTION_C":0.85,"LEAN_2_RUNG":0.7,"OPTION_F_TREND_BREAKER":0.85}[p]
        if "MT_MITIGATED" in st: return {"OPTION_G_MT_CORE":0.95,"LEAN_2_RUNG":0.9,"OPTION_B":0.7,"OPTION_F_TREND_BREAKER":0.65,"OPTION_C":0.55,"SNAPPED":0.5,"OPTION_A":0.25,"OPTION_H_MT_SPAN":0.3}[p]
        return {"OPTION_G_MT_CORE":0.9,"SNAPPED":0.85,"LEAN_2_RUNG":0.8,"OPTION_B":0.8,"OPTION_C":0.75,"OPTION_H_MT_SPAN":0.7,"OPTION_A":0.55,"OPTION_F_TREND_BREAKER":0.75}[p]
    def _p5(self,p,dd,age):
        b={"LEAN_2_RUNG":0.95,"OPTION_B":0.9,"OPTION_F_TREND_BREAKER":0.85,"OPTION_G_MT_CORE":0.75,"OPTION_C":0.65,"SNAPPED":0.6,"OPTION_A":0.0,"OPTION_H_MT_SPAN":0.0}[p] if dd.upper()=="INTRADAY" else {"OPTION_A":0.9,"SNAPPED":0.9,"OPTION_G_MT_CORE":0.9,"OPTION_B":0.85,"OPTION_C":0.85,"OPTION_H_MT_SPAN":0.85,"LEAN_2_RUNG":0.75,"OPTION_F_TREND_BREAKER":0.8}[p]
        return min(1.0,b*max(0.35,1.0-(age-0.65)*1.5)) if (age>0.65 and p in ("OPTION_A","SNAPPED","OPTION_H_MT_SPAN")) else b
    def _emp(self,p,stats):
        r=stats.get(p)
        if not r or r.get("n",0)<=0: return 0.0
        n=float(r["n"]); sh=max(-1.0,min(1.0,float(r.get("mean_alpha",0.0))/(float(r.get("std_pnl",100.0))+1e-6)))
        return (self.lmax*(n/(n+self.n0)))*sh*(1.0-float(r.get("blowout_rate",0.0)))
    def _tags(self,p,ctx,wl,rc,sym,age):
        t=[]
        if (wl>5.0 or rc<0.25) and p in ("OPTION_B","LEAN_2_RUNG"): t.append(f"+Wide breakers ({wl:.1f}L): skips chop")
        elif wl<3.0 and p in ("OPTION_C","OPTION_G_MT_CORE"): t.append(f"+Tight shelf ({wl:.1f}L): core density")
        elif 3.0<=wl<=5.0 and p in ("SNAPPED","OPTION_G_MT_CORE","OPTION_A"): t.append(f"+Balanced width ({wl:.1f}L)")
        if sym>=0.80 and p in ("OPTION_A","OPTION_B","OPTION_C"): t.append(f"+High symmetry ({sym*100:.0f}%)")
        elif sym<0.70 and p in ("SNAPPED","OPTION_G_MT_CORE","LEAN_2_RUNG"): t.append(f"+Asymmetric ({sym*100:.0f}%) favors snap")
        return t or [f"Geom {wl:.1f}L • Sym {sym*100:.0f}%"]


def apply_selector(inst,low,high,df,db,sf,sb,data,tel,cards):
    m,fee=resolve_v5_economics(inst,low,high); b=tel.get("bias") or {}
    res=V5ProfileSelector().evaluate(V5SelectionContext(range_low=low,range_high=high,multiplier=m,fee_per_rt=fee,s_bias=0.0,macro_regime="RANGE",d_front=df,d_back=db,s_front=sf,s_back=sb,demand_mit=str(data.get("demand_mit","FRESH")).upper(),supply_mit=str(data.get("supply_mit","FRESH")).upper(),drawdown_type=str(data.get("drawdown_type","EOD")).upper()))
    tel["staged_proposal"]["selector_eval"]=res
    for c in cards:
        rk=res.get("rankings",{}).get((c.get("key") or c.get("id")),{})
        if rk:
            c["fit_probability"]=round(float(rk.get("probability",0))*100.0,1); c["fit_reasons"]=rk.get("reasons",[])
            if (c.get("key") or c.get("id"))==res.get("best_fit"): c["recommended"]=True; c["is_best_fit"]=True
