// Reusable deterministic financial-story vocabulary. No trading API or live market data.
// All amounts, labels and prices are supplied by the authored shot, never inferred here.
import { S, E, clamp, ease, lerp, circle, ring, poly, seg, rr, text, alpha, glow } from './core.js';

export const MARKET_CAST = {
  trump: { suit:'#233c61', tie:'#e45049', skin:'#e8ad80', hair:'#f2ce76', light:'#ffe3a8' },
  nora: { suit:'#43958e', tie:'#a8e2ce', skin:'#c89170', hair:'#302639', light:'#b0e1da' },
  viewer: { suit:'#667994', tie:'#9daec6', skin:'#d2a184', hair:'#41352e', light:'#c7dbff' },
};
export function panel(g,x,y,w,h,{color=S().ui.card,line=S().ui.cardLine,r=24}={}) {
  g.save();rr(g,x,y,w,h,r);g.fillStyle=color;g.fill();
  if(line){g.strokeStyle=line;g.lineWidth=2;g.stroke();}g.restore();
}
export function label(g,str,x,y,size=38,color=S().ui.cream,align='center') {
  text(g,str,x,y,{size,color,align,font:'Nunito',weight:900});
}
export function fitLabel(g,str,x,y,width,size=60,color=S().ui.cream) {
  g.save();g.font=`900 ${size}px Nunito`;
  const k=Math.min(1,width/Math.max(1,g.measureText(str).width));g.restore();
  label(g,str,x,y,size*k,color);
}
function path(g,points,fill,stroke=null) {
  g.beginPath();for(const [i,p] of points.entries()) {
    if(i===0)g.moveTo(...p);else if(p.length===6)g.bezierCurveTo(...p);else g.lineTo(...p);
  }g.closePath();g.fillStyle=fill;g.fill();
  if(stroke){g.strokeStyle=stroke;g.lineWidth=2;g.stroke();}
}
export function portrait(g,{x=0,y=0,r=100,who='trump',expression='confident',t=0,turn=1}={}) {
  const c=MARKET_CAST[who]||MARKET_CAST.trump;
  g.save();g.translate(x,y);g.scale(r/100,r/100);
  // Flat face planes, heavy brows and a recognizable swept forelock.
  circle(g,-78,9,18,c.skin);circle(g,78,9,18,c.skin);
  path(g,[[-73,-65],[-40,-100,50,-100,77,-56],[92,-10,78,62,44,83],
    [16,101,-36,96,-65,63],[-86,34,-90,-22,-73,-65]],c.skin,c.light);
  if(who==='trump'){
    path(g,[[-87,-26],[-96,-70,-60,-104,-9,-104],[21,-127,92,-112,97,-73],
      [79,-94,24,-74,-21,-58],[16,-57,43,-58,72,-48],
      [50,-27,8,-45,-31,-33],[-56,-24,-58,-42,-65,-48],[-76,-32]],c.hair);
    seg(g,[[-71,-63],[-28,-84],[27,-91],[74,-84]],5,'#ffe6a5');
  }else{
    path(g,[[-82,5],[-112,-90,26,-120,73,-70],[92,-47,93,12,80,32],
      [63,-9,64,-38,40,-49],[-2,-22,-53,-34,-65,-19],[-71,12]],c.hair);
    if(who==='nora')circle(g,-90,-32,28,c.hair);
  }
  const blink=(t%3.73)>3.61;
  const eyeY=expression==='worried'?4:-2;
  const brow=expression==='worried'?9:expression==='angry'?-7:2;
  for(const side of [-1,1]){
    const ex=side*33+turn*3;
    seg(g,[[ex-17,eyeY-17+side*brow],[ex+16,eyeY-17-side*brow]],7,c.hair);
    if(blink)seg(g,[[ex-11,eyeY],[ex+11,eyeY]],3,'#283140');
    else {g.fillStyle='#fff8e8';g.beginPath();g.ellipse(ex,eyeY,14,8,0,0,Math.PI*2);g.fill();circle(g,ex+turn*3,eyeY,4,'#253a4d');}
  }
  if(who==='nora'){ring(g,-31,eyeY,22,3,c.hair);ring(g,35,eyeY,22,3,c.hair);seg(g,[[-9,eyeY],[13,eyeY]],3,c.hair);}
  seg(g,[[turn*3,8],[turn*12,27],[turn*1,30]],3,'#c48261');
  if(expression==='shock'){g.fillStyle='#683d38';g.beginPath();g.ellipse(4,53,12,17,0,0,7);g.fill();}
  else if(expression==='worried')seg(g,[[-21,62],[0,54],[24,60]],4,'#8e5046');
  else if(expression==='angry')seg(g,[[-25,54],[25,54]],4,'#8e5046');
  else {seg(g,[[-24,49],[4,56],[30,43]],4,'#8e5046');}
  if(who==='trump') {seg(g,[[-51,30],[-42,34]],2,'#ca8c6b');seg(g,[[47,28],[55,23]],2,'#ca8c6b');}
  g.restore();
}
export function character(g,{x,y,scale=1,who='trump',pose='stand',expression='confident',t=0,flip=false}={}) {
  const c=MARKET_CAST[who]||MARKET_CAST.trump;
  g.save();g.translate(x,y);g.scale(scale*(flip?-1:1),scale);
  g.fillStyle='rgba(0,0,0,.25)';g.beginPath();g.ellipse(0,1,100,16,0,0,7);g.fill();
  const b=Math.sin(t*2.3)*2;
  seg(g,[[-31,-142],[-38,-70],[-43,-9],[-64,-9]],23,'#17253a');
  seg(g,[[30,-142],[39,-73],[43,-9],[66,-9]],23,'#1b2d45');
  const arm=pose==='point'?[[62,-274],[100,-255],[146,-299]]:
    pose==='phone'?[[62,-274],[99,-235],[113,-305]]:
    pose==='shrug'?[[62,-274],[110,-238],[151,-263]]:
    pose==='celebrate'?[[62,-274],[104,-331],[131,-371]]:
    [[62,-274],[77,-207],[79,-160]];
  if(pose!=='phone'){
    const gesture=Math.sin(t*2.5)*5;
    arm[1][1]+=gesture*.5;arm[2][1]+=gesture;
  }
  seg(g,[[-60,-273],[-84,-212],[-88,-166]],25,c.light);
  seg(g,[[-60,-273],[-84,-212],[-88,-166]],20,c.suit);
  circle(g,-88,-162,13,c.skin);
  seg(g,arm,25,c.light);seg(g,arm,20,c.suit);circle(g,...arm.at(-1),14,c.skin);
  path(g,[[-55,-292+b],[0,-312+b],[57,-291+b],[67,-151],[-65,-151]],c.suit,c.light);
  poly(g,[[-24,-300+b],[0,-245+b],[24,-300+b]],'#f5eee2');
  poly(g,[[-6,-285+b],[7,-285+b],[14,-182],[-1,-167],[-13,-184]],c.tie);
  seg(g,[[-39,-291],[-17,-248],[-31,-231],[-6,-171]],2,'#587196');
  seg(g,[[38,-291],[18,-250],[30,-231],[7,-173]],2,'#587196');
  if(pose==='phone'){
    panel(g,99,-350,36,63,{r:6,color:'#0b182b',line:c.light});
    panel(g,105,-342,24,45,{r:3,color:'#7fbbd8',line:null});
  }
  portrait(g,{x:0,y:-352+b,r:76,who,expression,t,turn:flip?-1:1});
  g.restore();
}
export function coin(g,x,y,r,t=0,{face=true,spin=false}={}) {
  g.save();g.translate(x,y);if(spin)g.scale(.3+.7*Math.abs(Math.cos(t*1.9)),1);
  glow(g,0,0,r*1.6,S().ui.gold,.10);
  circle(g,0,0,r,S().ui.gold);ring(g,0,0,r*.88,r*.045,'#b78336');
  ring(g,0,0,r*.78,r*.018,'#fff0b5');
  for(let i=0;i<16;i++){const a=i*Math.PI/8;circle(g,Math.cos(a)*r*.93,Math.sin(a)*r*.93,r*.016,'#a97936');}
  if(face)portrait(g,{x:0,y:r*.03,r:r*.53,t});else label(g,'$',0,3,r*1.1,'#604422');
  g.restore();
}
export function priceSeries(seed=1,count=22,fall=false) {
  const rows=[];let close=30;
  for(let i=0;i<count;i++){
    const open=close;
    const noise=Math.sin(i*3.47+seed)*5;
    close=Math.max(4,open+((fall&&i>count*.38)?-7:4)+noise);
    rows.push({open,close,high:Math.max(open,close)+3+Math.abs(noise),low:Math.min(open,close)-3});
  }return rows;
}
export function candleChart(g,{x,y,w,h,t=0,duration=3,fall=false,seed=1,rows=null,heading='',value='',sub='ILLUSTRATIVE',progress=null}={}) {
  panel(g,x,y,w,h,{color:'#102239',line:'#36516b'});
  label(g,heading,x+30,y+37,28,S().ui.text,'left');
  label(g,sub,x+w-28,y+37,18,S().ui.sub,'right');
  const top=y+110,bottom=y+h-65,left=x+40,right=x+w-38;
  const data=rows||priceSeries(seed,24,fall),max=Math.max(...data.map(d=>d.high))+10;
  const sy=v=>bottom-(bottom-top)*v/max;
  for(let i=0;i<5;i++)seg(g,[[left,top+i*(bottom-top)/4],[right,top+i*(bottom-top)/4]],1,'#284158');
  const p=progress===null?ease.inOut(t/Math.max(.5,duration)):clamp(progress);
  const reveal=p*(data.length-1)+1;const step=(right-left)/data.length;
  for(let i=0;i<Math.min(data.length,Math.ceil(reveal));i++){
    const d=data[i],v=clamp(reveal-i),end=lerp(d.open,d.close,v),cx=left+(i+.5)*step;
    const color=end>=d.open?S().ui.good:S().ui.accent;
    seg(g,[[cx,sy(lerp(d.open,d.high,v))],[cx,sy(lerp(d.open,d.low,v))]],2,color);
    g.fillStyle=color;g.fillRect(cx-step*.29,Math.min(sy(d.open),sy(end)),step*.58,Math.max(3,Math.abs(sy(end)-sy(d.open))));
  }
  if(value)fitLabel(g,value,x+w*.73,y+88,w*.45,49,fall?S().ui.accent:S().ui.good);
}
export function postCard(g,{x,y,w=560,h=500,title='FICTIONAL POST',lines=['POLICY UPDATE'],t=0,badge='STORY SIMULATION',portraitFace=true}={}) {
  panel(g,x,y,w,h,{color:'#f4eedf',line:null,r:28});
  circle(g,x+55,y+58,33,'#d9e1dc');
  if(portraitFace)portrait(g,{x:x+55,y:y+58,r:23,t});
  label(g,title,x+104,y+44,26,'#22354a','left');
  label(g,badge,x+104,y+77,16,'#7c7e7b','left');
  seg(g,[[x+30,y+110],[x+w-30,y+110]],2,'#d5d4ca');
  lines.forEach((line,i)=>fitLabel(g,line,x+w/2,y+160+i*57,w-65,36,'#243d53'));
  const cy=y+h-56;
  for(let i=0;i<3;i++){ring(g,x+55+i*(w-100)/3,cy,10,2,'#879b9e');seg(g,[[x+75+i*(w-100)/3,cy],[x+125+i*(w-100)/3,cy]],6,'#bbc8c5');}
  circle(g,x+w-35,y+38,5,'#96acac');
}
export function moneyFlow(g,{x,y,w=1000,t=0,left='AUDIENCE',right='TOKEN BUSINESS',value='',color=S().ui.gold}={}) {
  panel(g,x,y,270,190);panel(g,x+w-270,y,270,190);
  fitLabel(g,left,x+135,y+61,235,28);fitLabel(g,right,x+w-135,y+61,235,28);
  for(let i=0;i<5;i++){
    const p=(t*.38+i/5)%1;const cx=x+290+(w-580)*p;
    panel(g,cx-22,y+108,44,27,{r:5,color,line:null});label(g,'$',cx,y+121,19,'#302b26');
  }
  seg(g,[[x+300,y+95],[x+w-290,y+95]],2,alpha(color,.6));
  poly(g,[[x+w-284,y+95],[x+w-300,y+86],[x+w-300,y+104]],color);
  if(value)fitLabel(g,value,x+w/2,y+238,w*.7,62,color);
}
export function balanceCard(g,{x,y,w=470,h=250,title,value,note='',color=S().ui.gold,accent=false}={}) {
  panel(g,x,y,w,h,{line:accent?color:S().ui.cardLine});
  label(g,title,x+28,y+42,25,S().ui.text,'left');
  fitLabel(g,value,x+w/2,y+h*.52,w-45,78,color);
  if(note)fitLabel(g,note,x+w/2,y+h-39,w-38,23,S().ui.sub);
}
export function countdown(g,x,y,remaining,{size=58,labelText='TIME LEFT'}={}) {
  label(g,labelText,x,y-size*.78,19,S().ui.sub);
  label(g,remaining,x,y,size,S().ui.gold);
}
export function globe(g,x,y,r,t,{a='WASHINGTON',b='HONG KONG',active=true}={}) {
  circle(g,x,y,r,'#16334d');ring(g,x,y,r,3,S().ui.blueprint);
  g.save();g.beginPath();g.arc(x,y,r,0,7);g.clip();
  for(let i=-2;i<=2;i++){
    g.strokeStyle='#346074';g.lineWidth=2;g.beginPath();g.ellipse(x,y,r*Math.max(.12,Math.abs(Math.sin(i*.5+t*.04))),r,0,0,7);g.stroke();
    const yy=y+i*r*.28;seg(g,[[x-r,yy],[x+r,yy]],2,'#346074');
  }
  poly(g,[[x-r*.7,y-r*.5],[x-r*.35,y-r*.73],[x-r*.1,y-r*.28],[x-r*.35,y-r*.08],[x-r*.17,y+r*.51],[x-r*.43,y+r*.21]],'#45796d');
  poly(g,[[x+r*.03,y-r*.43],[x+r*.58,y-r*.6],[x+r*.8,y-r*.12],[x+r*.3,y+r*.03],[x+r*.2,y+r*.55],[x-r*.05,y+r*.15]],'#45796d');
  g.restore();
  const l=[x-r*.55,y-r*.23],q=[x+r*.6,y-r*.23];
  g.strokeStyle=S().ui.gold;g.lineWidth=4;g.beginPath();g.moveTo(...l);g.quadraticCurveTo(x,y-r*.95,...q);g.stroke();
  if(active){const p=(t*.26)%1;const xx=(1-p)**2*l[0]+2*(1-p)*p*x+p*p*q[0],yy=(1-p)**2*l[1]+2*(1-p)*p*(y-r*.95)+p*p*q[1];circle(g,xx,yy,8,S().ui.gold);}
  circle(g,...l,7,S().ui.gold);circle(g,...q,7,S().ui.gold);
  label(g,a,x-r*.9,y+r+54,26);label(g,b,x+r*.9,y+r+54,26);
}
export function office(g,t,{day=false,desk=true}={}) {
  const u=S().ui;
  g.fillStyle=day?'#223c52':'#15263c';g.fillRect(-200,-100,2320,1400);
  for(let i=0;i<3;i++){
    const x=280+i*520;panel(g,x,160,380,500,{r:180,color:day?'#8ca6a8':'#334d6a',line:'#cfb576'});
    seg(g,[[x+190,160],[x+190,660]],13,'#18304b');seg(g,[[x,400],[x+380,400]],13,'#18304b');
  }
  g.fillStyle='#142136';g.fillRect(-200,730,2320,500);
  g.fillStyle='#213b51';g.beginPath();g.ellipse(970,880,610,130,0,0,7);g.fill();
  ring(g,970,880,80,3,'#486879');
  // Curtains, pilasters, a fictional seal and the desk form a consistent physical set.
  for(const x of [180,730,1250,1690]){g.fillStyle='#c8a76a';g.fillRect(x,130,30,590);g.fillStyle='#65553e';g.fillRect(x+30,130,22,590);}
  for(const x of [120,1790]){seg(g,[[x,225],[x,790]],7,u.gold);poly(g,[[x,230],[x+105,260],[x+105,470],[x,430]],'#bc5750');}
  if(desk){panel(g,570,650,800,145,{color:'#694838',line:'#b79361',r:13});panel(g,630,790,680,156,{color:'#443529',line:null,r:5});}
  glow(g,960,310,440,day?'#ffd899':'#a4c7e9',.065);
}
export function skyline(g,{city='HONG KONG',t=0,day=false}={}) {
  g.fillStyle=day?'#506b84':'#14273e';g.fillRect(-100,-100,2120,1280);
  circle(g,1490,225,76,day?'#f2cb87':'#bdd4df');
  for(let i=0;i<22;i++){
    const h=130+Math.abs(Math.sin(i*7.31))*320,x=i*94;
    g.fillStyle=i%2?'#1e3b51':'#25485d';g.fillRect(x,780-h,80,h);
    for(let row=0;row<h/32-1;row++)for(let col=0;col<3;col++){
      if((row+col+i)%3){g.fillStyle='#bcac78';g.globalAlpha=.55;g.fillRect(x+14+col*21,800-h+row*28,8,12);}
    }g.globalAlpha=1;
  }
  if(city==='LONDON'){g.fillStyle='#b39970';g.fillRect(400,330,95,450);poly(g,[[390,330],[448,200],[505,330]],'#b39970');circle(g,447,395,34,'#f2dfab');seg(g,[[447,371],[447,395],[467,405]],5,'#283a4c');}
  if(city==='NEW YORK'){g.fillStyle='#7a8796';g.fillRect(870,310,90,470);g.fillRect(890,220,50,100);seg(g,[[915,220],[915,145]],6,'#7a8796');}
  g.fillStyle='#101e31';g.fillRect(-100,780,2120,400);
  label(g,city,960,875,80,S().ui.cream);
  seg(g,[[520,935],[1400,935]],3,S().ui.gold);
}
export function particles(g,t,seed=1) {
  g.save();for(let i=0;i<22;i++){
    const x=((Math.sin(i*31+seed)+1)*960+t*(3+i%5))%1920;
    const y=((Math.cos(i*17+seed)+1)*450+t*2)%1000;
    circle(g,x,y,1.2+(i%3)*.4,alpha(S().ui.gold,.16));
  }g.restore();
}
export function documentCard(g,{x,y,w=700,h=640,title='PUBLIC RECORD',lines=[],stampText='',t=0}={}) {
  panel(g,x+12,y+12,w,h,{color:'#0a1422',line:null});
  panel(g,x,y,w,h,{color:'#e8e4d5',line:null,r:6});
  fitLabel(g,title,x+w/2,y+65,w-60,38,'#273a4d');
  seg(g,[[x+45,y+110],[x+w-45,y+110]],2,'#a5afa8');
  lines.forEach((line,i)=>fitLabel(g,line,x+w/2,y+170+i*65,w-75,30,'#3c5060'));
  if(stampText){g.save();g.translate(x+w*.55,y+h-92);g.rotate(-.08);panel(g,-w*.38,-36,w*.76,72,{r:2,color:'rgba(0,0,0,0)',line:'#ad5350'});fitLabel(g,stampText,0,0,w*.7,30,'#ad5350');g.restore();}
}
