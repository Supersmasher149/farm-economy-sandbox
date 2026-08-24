/* The dashboard's stylesheet and renderer, as C string literals.
 *
 * Included by src/dashboard.c and nowhere else -- these are `static`
 * deliberately, since the only consumer is the single translation unit that
 * writes the page.
 *
 * C11 has no raw string literals, so each source line is one adjacent
 * string literal ending in an explicit newline; the compiler concatenates
 * them into one constant. Two rules keep that bearable, and breaking either
 * one is a bug rather than a style preference:
 *
 *   1. The JS uses single-quoted strings throughout, so no double quote
 *      ever needs escaping here. A stray " in this file must be written \"
 *      and is easy to get wrong.
 *   2. Nothing may contain the byte sequence </script -- it would close the
 *      block early. (dashboard.c's escaper enforces the same rule for the
 *      data it emits, where the strings are not under our control.)
 *
 * The alternative -- keeping real .js/.css files and generating this header
 * from them at build time -- buys editor tooling at the cost of a codegen
 * step and a stale-artifact failure mode. Not worth it at this size; revisit
 * if the renderer grows past a few hundred lines.
 */
#ifndef FARM_DASHBOARD_JS_H
#define FARM_DASHBOARD_JS_H

static const char DASHBOARD_CSS[] =
    ":root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--ink:#14171a;--muted:#5b6570;\n"
    "--line:#e3e6ea;--accent:#2f6f4f;--warn:#8a5a00;--warnbg:#fff6e0;--bad:#b3392f;\n"
    "--grid:#eceff2;--hi:#dceee4}\n"
    "@media(prefers-color-scheme:dark){:root{--bg:#14171a;--card:#1c2024;--ink:#e8eaed;\n"
    "--muted:#98a2ad;--line:#2c3238;--accent:#6bbf8f;--warn:#e0b45e;--warnbg:#2a2213;\n"
    "--bad:#e8796d;--grid:#252b31;--hi:#22332a}}\n"
    "*{box-sizing:border-box}\n"
    "body{margin:0;padding:24px;background:var(--bg);color:var(--ink);\n"
    "font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}\n"
    "h1{font-size:20px;margin:0 0 4px}\n"
    "h2{font-size:14px;margin:0 0 12px;font-weight:600}\n"
    ".sub{color:var(--muted);font-size:13px;margin-bottom:20px}\n"
    ".sub code{font-size:12px}\n"
    ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:16px}\n"
    ".card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}\n"
    ".card.wide{grid-column:1/-1}\n"
    "table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}\n"
    "th,td{padding:6px 10px;text-align:right;border-bottom:1px solid var(--line);\n"
    "white-space:nowrap}\n"
    "th{cursor:pointer;user-select:none;color:var(--muted);font-weight:600;font-size:12px}\n"
    "th:hover{color:var(--ink)}\n"
    "th:first-child,td:first-child{text-align:left}\n"
    "tbody tr{cursor:pointer}\n"
    "tbody tr:hover{background:var(--grid)}\n"
    "tbody tr.on{background:var(--hi)}\n"
    ".warn{background:var(--warnbg);border:1px solid var(--line);border-left:3px solid var(--warn);\n"
    "border-radius:6px;padding:10px 14px;margin-bottom:16px}\n"
    ".warn ul{margin:8px 0 0;padding-left:18px}\n"
    ".warn li{margin:2px 0;font-size:13px}\n"
    ".warn .none{color:var(--muted)}\n"
    ".warn summary{cursor:pointer;font-weight:600;color:var(--warn)}\n"
    ".pick{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}\n"
    ".pick input{flex:1;min-width:280px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;\n"
    "padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);\n"
    "color:var(--ink)}\n"
    "button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;\n"
    "background:var(--card);color:var(--ink);cursor:pointer}\n"
    "button:hover{background:var(--grid)}\n"
    "svg{display:block;width:100%;height:auto;overflow:visible}\n"
    ".ax{stroke:var(--line);stroke-width:1}\n"
    ".gl{stroke:var(--grid);stroke-width:1}\n"
    ".tk{fill:var(--muted);font-size:10px}\n"
    ".lb{fill:var(--ink);font-size:11px}\n"
    ".sm{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}\n"
    ".sm figure{margin:0}\n"
    ".sm figcaption{color:var(--muted);font-size:11px;margin-bottom:2px;overflow:hidden;\n"
    "text-overflow:ellipsis;white-space:nowrap}\n"
    ".dim{opacity:.18}\n"
    "#tip{position:fixed;pointer-events:none;background:var(--card);color:var(--ink);\n"
    "border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12px;\n"
    "box-shadow:0 4px 14px rgba(0,0,0,.18);opacity:0;transition:opacity .1s;z-index:9}\n"
    ".lg{display:flex;gap:12px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:var(--muted)}\n"
    ".lg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}\n";

static const char DASHBOARD_JS[] =
    "var PAL=['#4C78A8','#F58518','#54A24B','#E45756','#72B7B2','#EECA3B','#B279A2','#9D755D'];\n"
    /* sortKey must name a real SUMMARY field -- a typo here does not throw,
       it just silently leaves the table in payload order. */
    "var sel=null,sortKey='avgFinalMoney',sortAsc=false;\n"
    "function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')\n"
    ".replace(/>/g,'&gt;');}\n"
    "function num(v,d){if(v===null||v===undefined||!isFinite(v))return '--';\n"
    "return v.toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});}\n"
    "function col(i){return PAL[i%PAL.length];}\n"
    /* Per-run derived values the payload deliberately does not duplicate:
       both are ratios of columns already present, and both are undefined
       rather than zero when their denominator never occurred. */
    "function wrate(r){return r[24]>0?100*r[15]/r[24]:null;}\n"
    "function lrate(r){return r[23]>0?100*r[22]/r[23]:null;}\n"
    "function rowsFor(i){var o=[];for(var k=0;k<RUNS.length;k++)if(RUNS[k][0]===i)o.push(RUNS[k]);\n"
    "return o;}\n"
    "function quant(a,p){if(!a.length)return 0;var i=(a.length-1)*p,lo=Math.floor(i),hi=Math.ceil(i);\n"
    "return lo===hi?a[lo]:a[lo]+(a[hi]-a[lo])*(i-lo);}\n"
    "function svg(w,h,inner){return '<svg viewBox=\"0 0 '+w+' '+h+'\" role=\"img\">'+inner+'</svg>';}\n"
    "function txt(x,y,s,c,a){return '<text x=\"'+x+'\" y=\"'+y+'\" class=\"'+(c||'tk')+'\"'+\n"
    "(a?' text-anchor=\"'+a+'\"':'')+'>'+esc(s)+'</text>';}\n"
    "function line(x1,y1,x2,y2,c){return '<line x1=\"'+x1+'\" y1=\"'+y1+'\" x2=\"'+x2+'\" y2=\"'+y2+\n"
    "'\" class=\"'+(c||'gl')+'\"/>';}\n"
    "function rect(x,y,w,h,f,o){return '<rect x=\"'+x+'\" y=\"'+y+'\" width=\"'+Math.max(0,w)+\n"
    "'\" height=\"'+Math.max(0,h)+'\" fill=\"'+f+'\"'+(o?' opacity=\"'+o+'\"':'')+' rx=\"2\"/>';}\n"
    "function act(i){return sel===null||sel===i;}\n"
    /* ---- overview table ---- */
    "var COLDEF=[['name','strategy',0],['runs','runs',0],['bankruptRate','bankrupt%',2],\n"
    "['avgFinalMoney','final money',2],['avgNetProfit','net profit',2],\n"
    "['avgProfitPerDay','profit/day',2],['avgDays','avg days',1],\n"
    "['avgCropLossRate','crop loss%',2],['firstUpgradeRate','upgrade%',1]];\n"
    "function table(){var s=SUMMARY.slice();\n"
    "s.sort(function(a,b){var x=a[sortKey],y=b[sortKey];\n"
    "if(x===null||x===undefined)return 1;if(y===null||y===undefined)return -1;\n"
    "if(typeof x==='string')return sortAsc?x.localeCompare(y):y.localeCompare(x);\n"
    "return sortAsc?x-y:y-x;});\n"
    "var h='<tr>';for(var c=0;c<COLDEF.length;c++)h+='<th data-k=\"'+COLDEF[c][0]+'\">'+\n"
    "esc(COLDEF[c][1])+(sortKey===COLDEF[c][0]?(sortAsc?' \\u2191':' \\u2193'):'')+'</th>';\n"
    "h+='</tr>';var b='';\n"
    "for(var i=0;i<s.length;i++){var r=s[i];b+='<tr data-i=\"'+r.index+'\"'+\n"
    "(sel===r.index?' class=\"on\"':'')+'>';\n"
    "for(var c2=0;c2<COLDEF.length;c2++){var k=COLDEF[c2][0],v=r[k];\n"
    "b+='<td>'+(k==='name'?esc(v):num(v,COLDEF[c2][2]))+'</td>';}b+='</tr>';}\n"
    "document.getElementById('tbl').innerHTML='<thead>'+h+'</thead><tbody>'+b+'</tbody>';\n"
    "var th=document.querySelectorAll('#tbl th');\n"
    "for(var t=0;t<th.length;t++)th[t].onclick=function(){var k=this.getAttribute('data-k');\n"
    "if(sortKey===k)sortAsc=!sortAsc;else{sortKey=k;sortAsc=(k==='name');}table();};\n"
    "var tr=document.querySelectorAll('#tbl tbody tr');\n"
    "for(var u=0;u<tr.length;u++)tr[u].onclick=function(){\n"
    "var i=+this.getAttribute('data-i');pick(sel===i?null:i);};}\n"
    /* ---- horizontal bars, shared by bankruptcy rate and profit/day ---- */
    "function bars(id,key,opt){var s=SUMMARY.slice().sort(function(a,b){return b[key]-a[key];});\n"
    "var W=460,rh=22,pl=132,pr=52,H=s.length*rh+26;var vals=s.map(function(r){return r[key]||0;});\n"
    "var mx=Math.max.apply(null,vals.concat([0])),mn=Math.min.apply(null,vals.concat([0]));\n"
    "var span=Math.max(Math.abs(mx),Math.abs(mn))||1;var pw=W-pl-pr;\n"
    "var zero=opt.diverge?pl+pw/2:pl,sc=opt.diverge?(pw/2)/span:pw/(mx||1);var g='';\n"
    "g+=line(zero,4,zero,H-22,'ax');\n"
    "for(var i=0;i<s.length;i++){var v=s[i][key]||0,y=8+i*rh,w=Math.abs(v)*sc;\n"
    "var x=v<0?zero-w:zero;var c=opt.color(v);\n"
    "g+=rect(x,y,w,rh-8,c,act(s[i].index)?1:.18);\n"
    "g+=txt(pl-8,y+rh-11,s[i].name,'lb','end');\n"
    "g+=txt(v<0?x-4:x+w+4,y+rh-11,num(v,opt.dp)+(opt.unit||''),'tk',v<0?'end':'start');}\n"
    "if(opt.mark!==undefined&&!opt.diverge){var mxp=pl+opt.mark*sc;\n"
    "g+='<line x1=\"'+mxp+'\" y1=\"2\" x2=\"'+mxp+'\" y2=\"'+(H-22)+\n"
    "'\" stroke=\"var(--bad)\" stroke-width=\"1\" stroke-dasharray=\"3 3\"/>';\n"
    "g+=txt(mxp+3,H-12,opt.markLabel,'tk','start');}\n"
    "document.getElementById(id).innerHTML=svg(W,H,g);}\n"
    /* ---- box plot of per-run final money ---- */
    "function box(){var W=460,pl=132,pr=20,rh=24;var s=SUMMARY.slice();\n"
    "var data=s.map(function(r){var v=rowsFor(r.index).map(function(x){return x[3];});\n"
    "v.sort(function(a,b){return a-b;});return {n:r.name,i:r.index,v:v};});\n"
    "data.sort(function(a,b){return quant(b.v,.5)-quant(a.v,.5);});\n"
    "var H=data.length*rh+26,pw=W-pl-pr;\n"
    "var hi=0;for(var d=0;d<data.length;d++)if(data[d].v.length)\n"
    "hi=Math.max(hi,quant(data[d].v,.95));hi=hi||1;\n"
    "var g='';for(var t=0;t<=4;t++){var gx=pl+pw*t/4;g+=line(gx,4,gx,H-22);\n"
    "g+=txt(gx,H-10,num(hi*t/4,0),'tk','middle');}\n"
    "for(var i=0;i<data.length;i++){var v=data[i].v,y=8+i*rh,mid=y+(rh-8)/2;\n"
    "g+=txt(pl-8,mid+4,data[i].n,'lb','end');if(!v.length)continue;\n"
    "var c=col(i),o=act(data[i].i)?1:.18;\n"
    "var q1=Math.min(quant(v,.25)/hi,1)*pw,q2=Math.min(quant(v,.5)/hi,1)*pw,\n"
    "q3=Math.min(quant(v,.75)/hi,1)*pw,lo=Math.min(quant(v,.05)/hi,1)*pw,\n"
    "up=Math.min(quant(v,.95)/hi,1)*pw;\n"
    "g+='<g opacity=\"'+o+'\">';\n"
    "g+='<line x1=\"'+(pl+lo)+'\" y1=\"'+mid+'\" x2=\"'+(pl+up)+'\" y2=\"'+mid+\n"
    "'\" stroke=\"'+c+'\" stroke-width=\"1\"/>';\n"
    "g+=rect(pl+q1,y,q3-q1,rh-8,c,.55);\n"
    "g+='<line x1=\"'+(pl+q2)+'\" y1=\"'+y+'\" x2=\"'+(pl+q2)+'\" y2=\"'+(y+rh-8)+\n"
    "'\" stroke=\"'+c+'\" stroke-width=\"2\"/></g>';}\n"
    "document.getElementById('box').innerHTML=svg(W,H,g);}\n"
    /* ---- crop mix: stacked shares of each cohort's plantings ---- */
    "function mix(){var W=460,pl=132,pr=20,rh=22,s=SUMMARY;var H=s.length*rh+26,pw=W-pl-pr;\n"
    "var g='';for(var i=0;i<s.length;i++){var y=8+i*rh,x=pl,o=act(s[i].index)?1:.18;\n"
    "g+=txt(pl-8,y+rh-11,s[i].name,'lb','end');\n"
    "if(!s[i].cropUsageObserved){g+=txt(pl+4,y+rh-11,'no plantings','tk','start');continue;}\n"
    "for(var c=0;c<META.crops.length;c++){var w=pw*(s[i].cropPct[c]||0)/100;\n"
    "if(w>0)g+=rect(x,y,w,rh-8,col(c),o);x+=w;}}\n"
    "document.getElementById('mix').innerHTML=svg(W,H,g);\n"
    "var l='';for(var k=0;k<META.crops.length;k++)\n"
    "l+='<span><i style=\"background:'+col(k)+'\"></i>'+esc(META.crops[k])+'</span>';\n"
    "document.getElementById('mixlg').innerHTML=l;}\n"
    /* ---- watering rate vs crop loss rate, one panel per strategy ---- */
    "function scatter(){var out='';\n"
    "for(var i=0;i<SUMMARY.length;i++){var s=SUMMARY[i],rs=rowsFor(s.index);\n"
    "var W=200,H=150,pl=30,pb=22,pt=6,pr=6,pts='';\n"
    "for(var k=0;k<rs.length;k++){var wr=wrate(rs[k]),lr=lrate(rs[k]);\n"
    "if(wr===null||lr===null)continue;\n"
    "var x=pl+(W-pl-pr)*Math.min(wr,100)/100,y=H-pb-(H-pb-pt)*Math.min(lr,100)/100;\n"
    "pts+='<circle cx=\"'+x.toFixed(1)+'\" cy=\"'+y.toFixed(1)+'\" r=\"2.4\" fill=\"'+col(i)+\n"
    "'\" opacity=\".5\" data-r=\"'+rs[k][1]+'\" data-s=\"'+s.index+'\"/>';}\n"
    "var g='';for(var t=0;t<=2;t++){var gy=H-pb-(H-pb-pt)*t/2;g+=line(pl,gy,W-pr,gy);\n"
    "g+=txt(pl-4,gy+3,(t*50)+'%','tk','end');}\n"
    "g+=line(pl,H-pb,W-pr,H-pb,'ax');\n"
    "g+=txt(pl,H-8,'0','tk','middle')+txt(W-pr,H-8,'100% watered','tk','end');\n"
    "out+='<figure'+(act(s.index)?'':' class=\"dim\"')+'><figcaption>'+esc(s.name)+\n"
    "'</figcaption>'+svg(W,H,g+pts)+'</figure>';}\n"
    "document.getElementById('sc').innerHTML=out;\n"
    "var cs=document.querySelectorAll('#sc circle');\n"
    "for(var c=0;c<cs.length;c++){cs[c].onmouseover=hover;cs[c].onmouseout=unhover;\n"
    "cs[c].onclick=grab;}}\n"
    /* ---- the reproduce-this-run affordance ---- */
    "function cmdFor(si,seed){return './farm-c single --strategy '+SUMMARY_BY_I[si].name+\n"
    "' --seed '+seed+' --verbose';}\n"
    "var tip=null;\n"
    "function hover(e){var s=+this.getAttribute('data-s'),r=this.getAttribute('data-r');\n"
    "tip.innerHTML='seed '+esc(r)+'<br>'+esc(SUMMARY_BY_I[s].name)+'<br>click to copy command';\n"
    "tip.style.opacity=1;tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';}\n"
    "function unhover(){tip.style.opacity=0;}\n"
    "function grab(){var s=+this.getAttribute('data-s'),r=this.getAttribute('data-r');\n"
    "var f=document.getElementById('cmd');f.value=cmdFor(s,r);f.select();}\n"
    /* ---- filter, applied to every panel at once ---- */
    "function pick(i){sel=i;draw();}\n"
    "function draw(){table();\n"
    "bars('bank','bankruptRate',{dp:2,unit:'%',mark:20,markLabel:'20% threshold',\n"
    "color:function(v){return v>20?'var(--bad)':'var(--accent)';}});\n"
    "bars('ppd','avgProfitPerDay',{dp:2,diverge:true,\n"
    "color:function(v){return v<0?'var(--bad)':'var(--accent)';}});\n"
    "box();mix();scatter();\n"
    "document.getElementById('flt').textContent=sel===null?'all strategies':\n"
    "SUMMARY_BY_I[sel].name;}\n"
    "var SUMMARY_BY_I={};\n"
    "function boot(){for(var i=0;i<SUMMARY.length;i++)SUMMARY_BY_I[SUMMARY[i].index]=SUMMARY[i];\n"
    "tip=document.getElementById('tip');\n"
    "document.getElementById('clr').onclick=function(){pick(null);};\n"
    /* Collapsed by default: a full batch trips 30+ rules, which is a wall
       of text tall enough to push every chart below the fold. The count in
       the summary is the part you need at a glance. */
    "var w=document.getElementById('warns');\n"
    "w.innerHTML=WARNINGS.length?'<details><summary>'+WARNINGS.length+\n"
    "' balance warning'+(WARNINGS.length===1?'':'s')+'</summary><ul>'+\n"
    "WARNINGS.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul></details>':\n"
    "'<div class=\"none\">No balance warnings.</div>';\n"
    "draw();}\n"
    "boot();\n";

#endif /* FARM_DASHBOARD_JS_H */
