// SPDX-License-Identifier: MPL-2.0
// Timings/curves from the original rennsandroidkitchen animation classes.
.pragma library

function clamp(value) { return Math.max(0, Math.min(1, value)); }
function mix(a, b, progress) { return a + (b - a) * progress; }
function outCubic(progress) { return 1 - Math.pow(1 - clamp(progress), 3); }
function remainingDuration(fullDuration, current, base, target) {
    if (fullDuration <= 0) return 0;
    const span = Math.abs(target - base);
    if (span <= 0.00001) return 0;
    const remaining = clamp(Math.abs(target - current) / span);
    if (remaining <= 0.0001) return 0;
    return Math.max(1, Math.round(fullDuration * Math.cbrt(remaining)));
}
function outQuint(progress) { return 1 - Math.pow(1 - clamp(progress), 5); }
function inOutSine(progress) { return (1-Math.cos(Math.PI*clamp(progress)))/2; }
function roundRect(r) { return Qt.rect(roundEven(r.x),roundEven(r.y),roundEven(r.width),roundEven(r.height)); }
function inOutCubic(progress) { let p = clamp(progress); return p < .5 ? 4*p*p*p : 1 - Math.pow(-2*p+2, 3)/2; }
function opacity(progress, start, end, frames) {
    let previousAt = 0, previousValue = start;
    let points = (frames || []).concat([[1, end]]);
    for (let i = 0; i < points.length; ++i) {
        let at = points[i][0], value = points[i][1];
        if (progress <= at) return mix(previousValue, value, (progress - previousAt) / Math.max(.00001, at - previousAt));
        previousAt = at; previousValue = value;
    }
    return end;
}
function spring(progress) {
    if (progress <= 0) return 0;
    if (progress >= 1) return 1;
    const damping = .78, frequency = 12, oscillation = Math.sqrt(1 - damping*damping);
    function response(at) {
        const phase = frequency * oscillation * at;
        return 1 - Math.exp(-damping*frequency*at) * (Math.cos(phase) + damping/oscillation*Math.sin(phase));
    }
    return response(progress) / response(1);
}
function rect(a, b, progress) {
    return Qt.rect(mix(a.x,b.x,progress), mix(a.y,b.y,progress), mix(a.width,b.width,progress), mix(a.height,b.height,progress));
}
function driveProgress(elapsed, index) {
    const local = Math.max(0, Math.min(840, elapsed - index*42));
    return local <= 420 ? 1 - Math.pow(1-local/700,3) : 1 - Math.pow(.4,3)*Math.pow(1-(local-420)/420,4.5);
}
function travel(a, b, progress) {
    const t = clamp(progress), u = 1-t, dx = b.x-a.x, dy=b.y-a.y;
    return Qt.point(u*u*u*a.x + 3*u*u*t*(a.x+dx*.25) + 3*u*t*t*(a.x+dx*.8) + t*t*t*b.x,
                    u*u*u*a.y + 3*u*u*t*(a.y+dy*.65) + 3*u*t*t*(a.y+dy*.95) + t*t*t*b.y);
}

// Python round() in the reference uses ties to even (including the 15.5 ms stagger).
function roundEven(n) { const floor=Math.floor(n); return n-floor===.5 ? floor+(floor%2) : Math.round(n); }
function keyed(rows) {
    let counts={}, result=[];
    for (let i=0;i<rows.length;++i) {
        const base=String(rows[i]._trash_id || rows[i].key || rows[i].path || "").toLowerCase();
        const occurrence=counts[base] || 0; counts[base]=occurrence+1;
        result.push({key:base+"\u001f"+occurrence,row:rows[i],index:i});
    }
    return result;
}
function projectScene(before, after, width, height, scroll, marginBefore, marginAfter) {
    const old=keyed(before), next=keyed(after), oldMap={}, newMap={};
    const visibleTop=-(marginBefore || 0), visibleBottom=height+(marginAfter || 0);
    // The last row retains its eight-pixel bottom margin, just like QListWidget.
    const targetScroll=Math.min(scroll,Math.max(0,after.length*74-height));
    let keys=[], removed=false, added=false, scene=[];
    for (let i=0;i<old.length;++i) { oldMap[old[i].key]=old[i]; keys.push(old[i].key); }
    for (let i=0;i<next.length;++i) { newMap[next[i].key]=next[i]; if (!oldMap[next[i].key]) keys.push(next[i].key); }
    for (let i=0;i<keys.length;++i) {
        const a=oldMap[keys[i]], b=newMap[keys[i]], kind=!b?"remove":!a?"add":"keep";
        removed=removed || kind==="remove"; added=added || kind==="add";
        const sy=a?a.index*74-scroll:b.index*74-targetScroll;
        const ty=b?b.index*74-targetScroll:sy;
        if ((sy+74<=visibleTop || sy>=visibleBottom) && (ty+74<=visibleTop || ty>=visibleBottom)) continue;
        scene.push({row:(a||b).row,sourceY:sy,targetY:ty,kind:kind,delay:0,index:(a||b).index,order:i});
    }
    scene.sort((a,b)=>({keep:0,remove:1,add:2}[a.kind]-{keep:0,remove:1,add:2}[b.kind]) || a.order-b.order);
    const removalOnly=removed&&!added, baseDelay=removalOnly?168:removed?240:0;
    let moves=0, end=removed?240:0;
    for (let i=0;i<scene.length;++i) {
        const row=scene[i]; row.delay=baseDelay;
        if (row.kind==="keep" && row.sourceY!==row.targetY) {
            if (removalOnly) row.delay=roundEven(baseDelay+moves*15.5);
            ++moves; end=Math.max(end,row.delay+310);
        }
    }
    const enterDelay=baseDelay+(moves?155:0);
    if (added) end=Math.max(end,enterDelay+180);
    return {rows:scene,duration:end,enterDelay:enterDelay,targetScroll:targetScroll};
}

function diskScene(before, after, position, viewport) {
    const old=keyed(before), next=keyed(after), a={}, b={}, keys=[];
    for (let i=0;i<old.length;++i) { a[old[i].key]=old[i]; keys.push(old[i].key); }
    for (let i=0;i<next.length;++i) { b[next[i].key]=next[i]; if(!a[next[i].key]) keys.push(next[i].key); }
    const removed=old.some(row=>!b[row.key]), added=next.some(row=>!a[row.key]);
    let scene=[], moveIndex=0, duration=removed?380:310;
    for (let i=0;i<keys.length;++i) {
        const from=a[keys[i]], to=b[keys[i]], kind=!to?"remove":!from?"add":"keep";
        const source=position((from||to).index,(from||to).row), target=to?position(to.index,to.row):source;
        function visible(p) { return p.x+128>viewport.x && p.x<viewport.x+viewport.width && p.y+44>viewport.y && p.y<viewport.y+viewport.height; }
        if (!visible(source) && !visible(target)) continue;
        let delay=0;
        if (kind==="keep" && (source.x!==target.x || source.y!==target.y)) {
            if (removed&&!added) delay=266+moveIndex*55;
            ++moveIndex; duration=Math.max(duration,delay+310);
        }
        scene.push({row:(from||to).row,source:source,target:target,kind:kind,delay:delay,index:(from||to).index,order:i});
    }
    scene.sort((a,b)=>((a.kind==="remove")-(b.kind==="remove")) || a.order-b.order);
    return {rows:scene,duration:duration};
}
