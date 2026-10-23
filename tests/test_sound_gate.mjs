import assert from 'node:assert/strict';
import {AdaptiveSoundGate,encodeWav} from '../web/sound-gate.js';
const rate=16000, frame=320, clips=[], phases=[];
const gate=new AdaptiveSoundGate({sampleRate:rate,onClip:c=>clips.push(c),onState:s=>phases.push(s)});
function feed(seconds,amplitude){const n=Math.round(seconds*rate/frame);for(let k=0;k<n;k++){
    const a=new Float32Array(frame);for(let i=0;i<frame;i++)a[i]=amplitude*Math.sin(2*Math.PI*200*(i+k*frame)/rate);
    gate.push(a);
}}
feed(2,0.001);feed(1,0.12);feed(1.25,0.001);feed(10,0.001);feed(1.2,0.12);feed(1,0.001);gate.finish();
assert.equal(clips.length,2,'two voiced intervals should yield two chunks');
assert.ok(clips[0].startSeconds>1.5 && clips[0].startSeconds<2.1);
assert.ok(clips[1].startSeconds>13,'silence gaps must be preserved');
const blob=encodeWav(clips[0].samples,rate);
assert.ok(blob.size>44);
assert.ok(phases.includes('recording')&&phases.includes('silent'));
console.log('JS sound-gate tests OK: '+clips.map(c=>c.startSeconds.toFixed(2)).join(', '));

const calibration=new AdaptiveSoundGate({sampleRate:rate});
function calibrate(seconds,amplitude){
  const n=Math.round(seconds*rate/frame);
  for(let k=0;k<n;k++){
    const a=new Float32Array(frame);
    for(let i=0;i<frame;i++)a[i]=amplitude*Math.sin(2*Math.PI*200*i/rate);
    calibration.push(a);
  }
}
calibrate(1.0,0.001);calibrate(0.4,0.15);
assert.ok(calibration.noiseDb < -45,'short loud event should not dominate the noise floor');
calibration.recalibrate();
assert.equal(calibration.calibrationCount,0);
