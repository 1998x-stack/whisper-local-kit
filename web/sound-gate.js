// Dependency-free browser sound activity detection (SAD), not speech classification.
// Signals are measured on a microphone stream the user explicitly granted.
export class AdaptiveSoundGate {
  constructor({sampleRate, sensitivityDb = 12, onClip = () => {}, onState = () => {}}) {
    this.rate = sampleRate;
    this.sensitivityDb = sensitivityDb;
    this.onClip = onClip;
    this.onState = onState;
    this.samplesSeen = 0;
    this.calibrationEnd = sampleRate * 1.4;
    this.noiseDb = -65;
    this.calibrationCount = 0;
    this.history = [];
    this.active = false;
    this.frames = [];
    this.startedAt = 0;
    this.pendingAbove = 0;
    this.silentSamples = 0;
    this.lastStatus = '';
    this.stats = {levelDb: -100, noiseDb: -65, thresholdDb: -53, elapsed: 0};
    this.preSamples = Math.round(sampleRate * 0.35);
    this.triggerSamples = Math.round(sampleRate * 0.18);
    this.hangSamples = Math.round(sampleRate * 0.8);
    this.maxSamples = Math.round(sampleRate * 22);
  }
  emitState(kind) {
    if (kind !== this.lastStatus) {
      this.lastStatus = kind;
    }
    this.onState(kind, {...this.stats});
  }
  threshold() {
    return Math.min(-12, Math.max(-56, this.noiseDb + this.sensitivityDb));
  }
  push(input) {
    if (!input?.length) return;
    const buf = input instanceof Float32Array ? input : new Float32Array(input);
    let power = 0;
    for (let i = 0; i < buf.length; i++) power += buf[i] * buf[i];
    const db = 20 * Math.log10(Math.sqrt(power / buf.length) + 1e-9);
    const begin = this.samplesSeen;
    this.samplesSeen += buf.length;
    const frame = {buf, begin};
    this.stats = {levelDb: +db.toFixed(1), noiseDb: +this.noiseDb.toFixed(1),
                  thresholdDb: +this.threshold().toFixed(1), elapsed: this.samplesSeen / this.rate};
    if (this.samplesSeen <= this.calibrationEnd) {
      this.noiseDb = this.calibrationCount ? (this.noiseDb * 0.92 + db * 0.08) : db;
      this.calibrationCount++;
      this.emitState('calibrating');
      return;
    }
    if (!this.active) {
      this.history.push(frame);
      while (this.history.length > 1 && this.samplesSeen - this.history[0].begin > this.preSamples) this.history.shift();
      const isAbove = db >= this.threshold();
      if (isAbove) this.pendingAbove += buf.length;
      else this.pendingAbove = 0;
      if (this.pendingAbove >= this.triggerSamples) {
        this.active = true;
        this.frames = this.history.slice();
        this.startedAt = this.frames[0].begin;
        this.history = [];
        this.pendingAbove = 0;
        this.silentSamples = 0;
        this.emitState('recording');
      } else {
        // Only adapt during idle, especially when the energy stays near background.
        if (!isAbove) this.noiseDb = 0.98 * this.noiseDb + 0.02 * db;
        this.emitState('silent');
      }
      return;
    }
    this.frames.push(frame);
    // Second lower threshold prevents rapid flickering around the trigger level.
    this.silentSamples = db < this.threshold() - 4 ? this.silentSamples + buf.length : 0;
    this.emitState('recording');
    if (this.silentSamples >= this.hangSamples) this.flush();
    else if (this.samplesSeen - this.startedAt >= this.maxSamples) this.flush(true);
  }
  flush(forceSplit = false) {
    if (!this.active || !this.frames.length) return;
    const start = this.startedAt;
    const size = this.frames.reduce((n, f) => n + f.buf.length, 0);
    // Ignore isolated, very brief transients.
    if (size >= this.rate * 0.35) {
      const samples = new Float32Array(size);
      let cursor = 0;
      for (const f of this.frames) {samples.set(f.buf, cursor); cursor += f.buf.length;}
      this.onClip({samples, sampleRate: this.rate, startSeconds: start / this.rate,
                   durationSeconds: size / this.rate});
    }
    if (forceSplit) {
      // Small overlap reduces syllable truncation, but may create duplicate words.
      const tail = [];
      let tailSize = 0;
      for (let i = this.frames.length - 1; i >= 0; i--) {
        tail.unshift(this.frames[i]); tailSize += this.frames[i].buf.length;
        if (tailSize >= this.rate * 0.25) break;
      }
      this.frames = tail;
      this.startedAt = tail[0]?.begin ?? this.samplesSeen;
      this.silentSamples = 0;
      this.emitState('recording');
    } else {
      this.frames = [];
      this.active = false;
      this.silentSamples = 0;
      this.history = [];
      this.emitState('silent');
    }
  }
  finish() {this.flush(false); this.emitState('stopped');}
}

export function encodeWav(samples, sampleRate) {
  const out = new ArrayBuffer(44 + samples.length * 2);
  const dv = new DataView(out);
  const str = (offset, value) => {for(let i=0;i<value.length;i++) dv.setUint8(offset+i,value.charCodeAt(i));};
  str(0,'RIFF'); dv.setUint32(4,36+samples.length*2,true); str(8,'WAVE');
  str(12,'fmt '); dv.setUint32(16,16,true); dv.setUint16(20,1,true); dv.setUint16(22,1,true);
  dv.setUint32(24,sampleRate,true); dv.setUint32(28,sampleRate*2,true);
  dv.setUint16(32,2,true); dv.setUint16(34,16,true);
  str(36,'data'); dv.setUint32(40,samples.length*2,true);
  for (let i=0;i<samples.length;i++) {
    const s = Math.max(-1,Math.min(1,samples[i]));
    dv.setInt16(44+i*2,Math.round(s<0?s*32768:s*32767),true);
  }
  return new Blob([out],{type:'audio/wav'});
}
