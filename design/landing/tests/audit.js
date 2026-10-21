/* Test helper (not part of the page). Load in the browser console on landing-*.html:
   fetch('tests/audit.js').then(r=>r.text()).then(eval)  then  __sweep()  or  __page()
   Contrast is computed analytically: text colour vs. the composited fill of every painted rect under it, at each replay frame. */
window.__A=(function(){
 function parse(c){var m=c.match(/rgba?\(([^)]+)\)/);if(!m)return null;var p=m[1].split(/[ ,\/]+/).map(parseFloat);return [p[0],p[1],p[2],p.length>3?p[3]:1];}
 function lum(c){var a=c.slice(0,3).map(function(v){v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*a[0]+.7152*a[1]+.0722*a[2];}
 function over(fg,bg,al){var a=fg[3]*(al===undefined?1:al);return [fg[0]*a+bg[0]*(1-a),fg[1]*a+bg[1]*(1-a),fg[2]*a+bg[2]*(1-a),1];}
 function ratio(a,b){var l1=lum(a),l2=lum(b);if(l1<l2){var t=l1;l1=l2;l2=t}return (l1+.05)/(l2+.05);}
 function eff(el){var o=1;for(var e=el;e&&e.nodeType==1;e=e.parentElement){o*=parseFloat(getComputedStyle(e).opacity);}return o;}
 function vis(el){for(var e=el;e&&e.nodeType==1;e=e.parentElement){var cs=getComputedStyle(e);if(cs.display==='none'||cs.visibility==='hidden')return false;}return true;}
 function inter(a,b){return a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;}
 function cssv(n){return parse(getComputedStyle(document.querySelector('.lp')).getPropertyValue('--'+n).trim().replace(/^#(..)(..)(..)$/,function(m,a,b,c){return 'rgb('+parseInt(a,16)+','+parseInt(b,16)+','+parseInt(c,16)+')'}));}
 function htmlBg(el){var chain=[];for(var e=el;e&&e.nodeType==1;e=e.parentElement){var cs=getComputedStyle(e);var bg=parse(cs.backgroundColor);if(bg&&bg[3]>0){chain.push(bg);if(bg[3]>=1)break;}}
   var base=cssv('bg');for(var i=chain.length-1;i>=0;i--)base=over(chain[i],base);return base;}
 function svgTexts(svg){var out=[];var rects=[].slice.call(svg.querySelectorAll('rect.base,rect.fl,.file rect,.url-tag rect'));var cells=[].slice.call(svg.querySelectorAll('rect.gc'));var ac=cssv('ac');
   var sheetBg=parse(getComputedStyle(svg.closest('.sheet')).backgroundColor);
   [].forEach.call(svg.querySelectorAll('text,tspan'),function(t){
     if(t.tagName==='text'&&t.querySelector('tspan'))return; if(!(t.textContent||'').trim())return; if(!vis(t))return;
     var o=eff(t);if(o<=0.001)return; var cs=getComputedStyle(t);var fg=parse(cs.fill);if(!fg)return;
     var fo=parseFloat(cs.fillOpacity);var tb=t.getBoundingClientRect();if(tb.width===0)return;
     var bg=sheetBg.slice();var best=0;
     cells.forEach(function(r){if(!inter(r.getBoundingClientRect(),tb))return;var ro=eff(r)*parseFloat(getComputedStyle(r).fillOpacity);if(ro>best)best=ro;});
     if(best>0)bg=over(ac.concat([1]).slice(0,3).concat([1]),bg,best);
     rects.forEach(function(r){ if(!(r.compareDocumentPosition(t)&Node.DOCUMENT_POSITION_FOLLOWING))return; if(!vis(r))return; var ro=eff(r);if(ro<=0.001)return;
       if(!inter(r.getBoundingClientRect(),tb))return; var rc=getComputedStyle(r);var f=parse(rc.fill);if(!f)return; bg=over(f,bg,parseFloat(rc.fillOpacity)*ro);});
     var c=over(fg,bg,fo*o);var fs=parseFloat(cs.fontSize)*(svg.getBoundingClientRect().width/svg.viewBox.baseVal.width);
     out.push({txt:t.textContent.trim().slice(0,24),r:ratio(c,bg),fs:fs});});
   return out;}
 function htmlTexts(root){var out=[];var w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);var n;var seen=new Set();
   while(n=w.nextNode()){if(!n.nodeValue.trim())continue;var el=n.parentElement;if(seen.has(el))continue;seen.add(el);if(el.closest('svg')||el.closest('script,style'))continue;if(!vis(el))continue;
     var cs=getComputedStyle(el);var fg=parse(cs.color);var o=eff(el);var bg=htmlBg(el);
     if(el.closest('.cta')){var A=cssv('ac'),B=cssv('ac2');[A,B].forEach(function(g){var gg=[g[0],g[1],g[2],1];out.push({txt:el.textContent.trim().slice(0,24),r:ratio(over(fg,gg),gg),fs:parseFloat(cs.fontSize),hid:'cta'});});continue;}
     out.push({txt:el.textContent.trim().slice(0,24),r:ratio(over(fg,bg,o),bg),fs:parseFloat(cs.fontSize),hid:el.className});}
   return out;}
 return {svgTexts:svgTexts,htmlTexts:htmlTexts,ratio:ratio};
})();
window.__sweep=function(step){step=step||0.2;var api=window.__lp[0];var S=api.sheets[0];var svg=[].slice.call(S.el.querySelectorAll('svg.scene')).filter(function(s){return s.getBoundingClientRect().width>0})[0];var worst={r:99},n=0,frames=0,per={};
 for(var t=0;t<=30.001;t+=step){S.t=-1;api.render(S,t);frames++;window.__A.svgTexts(svg).concat(window.__A.htmlTexts(S.el)).forEach(function(x){n++;if(!per[x.txt]||per[x.txt].r>x.r)per[x.txt]={r:+x.r.toFixed(2),fs:+x.fs.toFixed(1),t:+t.toFixed(1)};if(x.r<worst.r)worst={r:+x.r.toFixed(2),txt:x.txt,t:+t.toFixed(1),fs:+x.fs.toFixed(1)}});}
 var arr=Object.keys(per).map(function(k){return [k,per[k].r,per[k].fs,per[k].t]}).sort(function(a,b){return a[1]-b[1]});
 return {frames:frames,samples:n,worst:worst,lowest4:arr.slice(0,4),below45:arr.filter(function(a){return a[1]<4.5}).length};};
window.__page=function(){var api=window.__lp[0];var res={fails:[],min:99,n:0};
 api.sheets.forEach(function(S,i){[26.4,10.5,19.5,5.5].forEach(function(t){S.t=-1;api.render(S,t);[].slice.call(S.el.querySelectorAll('svg.scene')).filter(function(s){return s.getBoundingClientRect().width>0}).forEach(function(sv){window.__A.svgTexts(sv).forEach(function(x){res.n++;if(x.r<res.min)res.min=+x.r.toFixed(2);if(x.r<4.5)res.fails.push([x.txt,+x.r.toFixed(2),x.fs,'sheet'+i+'@'+t])});});});});
 window.__A.htmlTexts(document.querySelector('.lp')).forEach(function(x){res.n++;if(x.r<res.min)res.min=+x.r.toFixed(2);if(x.r<(x.fs>=24?3:4.5))res.fails.push([x.txt,+x.r.toFixed(2),x.fs,'html '+x.hid])});
 res.fails=res.fails.slice(0,25);return res;};
