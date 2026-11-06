import assert from 'node:assert/strict';
import {priceSeries, MARKET_CAST} from './market2d.js';
// A simulation chart must be reproducible and preserve OHLC geometry for every seed.
for(let seed=0;seed<30;seed++){
  for(const fall of [false,true]){
    const a=priceSeries(seed,24,fall),b=priceSeries(seed,24,fall);
    assert.deepEqual(a,b);
    assert.equal(a.length,24);
    for(const [i,c] of a.entries()){
      assert(Object.values(c).every(Number.isFinite));
      assert(c.high>=Math.max(c.open,c.close));
      assert(c.low<=Math.min(c.open,c.close));
      if(i)assert.equal(c.open,a[i-1].close);
    }
    if(fall)assert(a.at(-1).close<Math.max(...a.map(x=>x.close)));
    else assert(a.at(-1).close>a[0].open);
  }
}
assert.notEqual(MARKET_CAST.trump.tie,MARKET_CAST.nora.tie);
console.log('market2d: deterministic OHLC, continuity, direction and cast checks passed');
