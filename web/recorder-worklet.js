// Worklet has no network access and always outputs zero to prevent feedback.
class MicFrames extends AudioWorkletProcessor {
  constructor() {super(); this.buffer=new Float32Array(1024); this.used=0;}
  process(inputs, outputs) {
    const channel=inputs[0]?.[0];
    if (channel) {
      for (let i=0;i<channel.length;i++) {
        this.buffer[this.used++]=channel[i];
        if (this.used===1024) {
          const frame=this.buffer;
          this.port.postMessage(frame,[frame.buffer]);
          this.buffer=new Float32Array(1024); this.used=0;
        }
      }
    }
    const output=outputs[0];
    if (output) for(const ch of output) ch.fill(0);
    return true;
  }
}
registerProcessor('mic-frames',MicFrames);
